"""Background watcher: simulated sleep/wake (reconnect notifications) must re-apply the saved config."""
import importlib.util, json, os, sys, threading, time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import superstrike_mock as M

spec = importlib.util.spec_from_file_location("watch", os.path.join(ROOT, "superstrike-watch.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    be, dev, idx, fidx, fw = M.make_mock()
    # user set 1600 DPI / 8000 Hz / HITS and saved it
    be.set_dpi(dev, idx, 1600, 1600, 2)
    be.set_rate(dev, idx, 8000)
    be.set_button(dev, idx, fidx, 0, 0x08, 0x00, 1)
    conf = str(tmp_path / "superstrike-hits.conf")
    be.save_config(dev, idx, fidx, conf)
    nodes = {"/dev/hidraw4": fw.add_listener()}
    logs = []
    w = watch.Watcher(be, conf=conf, verify_interval=0, settle=0.05, log=logs.append,
                      scan=lambda: list(nodes), open_listener=lambda p: nodes[p],
                      open_device=lambda p: (dev, idx, fidx))
    return be, dev, idx, fidx, fw, w, nodes, logs


def run_until(w, cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end and not cond():
        w.step(max_wait=0.05)
    return cond()


def test_startup_applies_once(env):
    be, dev, idx, fidx, fw, w, nodes, logs = env
    fw.sleep_and_wake(kind="none")  # mouse already reverted before the service started
    fw.listeners.clear()
    w.settle = 0.05
    w.rescan()  # node appears -> scheduled (2 s delay); shorten for the test
    w.pending = (time.monotonic(), w.pending[1], 3)
    assert run_until(w, lambda: fw.dpi[0] == 1600)
    assert fw.onboard_mode == 2 and fw.rate_idx == 6


@pytest.mark.parametrize("kind", ["1d4b", "41"])
def test_reconnect_notification_reapplies(env, kind):
    be, dev, idx, fidx, fw, w, nodes, logs = env
    w.rescan()
    w.pending = None
    w.status_idx["/dev/hidraw4"] = fw.features.index(0x1D4B)  # learned after first connect
    fw.sleep_and_wake(kind)
    assert fw.dpi[0] == 800 and fw.onboard_mode == 1  # the bug: back at 800 in onboard mode
    assert run_until(w, lambda: fw.dpi[0] == 1600)
    assert fw.onboard_mode == 2 and fw.rate_idx == 6 and fw.rec[0][0] == 0x08
    assert any("mouse reconnected" in l for l in logs)
    assert json.load(open(watch.status_path()))["state"] == "ok"


def test_1d4b_index_learned_on_apply(env):
    be, dev, idx, fidx, fw, w, nodes, logs = env
    w.rescan()
    w.pending = (time.monotonic(), "test", 1)
    run_until(w, lambda: w.applies == 1)
    assert w.status_idx["/dev/hidraw4"] == fw.features.index(0x1D4B)


def test_node_disappears_and_returns(env):
    be, dev, idx, fidx, fw, w, nodes, logs = env
    w.rescan(); w.pending = None
    sock = nodes.pop("/dev/hidraw4")
    fw.listeners.clear()
    sock.close()
    w.rescan()
    assert w.fds == {}
    nodes["/dev/hidraw4"] = fw.add_listener()  # USB re-enumerated after resume
    fw.sleep_and_wake(kind="none")
    w.rescan()
    assert "/dev/hidraw4" in w.fds and w.pending
    w.pending = (time.monotonic(), w.pending[1], 3)
    assert run_until(w, lambda: fw.dpi[0] == 1600)


def test_eof_drops_node(env):
    be, dev, idx, fidx, fw, w, nodes, logs = env
    w.rescan(); w.pending = None
    for l in fw.listeners:
        l.close()  # other end closes -> EOF on our listener
    run_until(w, lambda: not w.fds, 1.0)
    assert any("EOF" in l for l in logs)


def test_resume_from_suspend_detected(env):
    be, dev, idx, fidx, fw, w, nodes, logs = env
    t = {"mono": 100.0, "boot": 100.0}
    w.clock = lambda: (t["mono"], t["boot"])
    w._mono, w._boot = 100.0, 100.0
    w.rescan(); w.pending = None
    fw.sleep_and_wake(kind="none")
    t["mono"] += 1; t["boot"] += 600  # 10 min suspended
    w.step(max_wait=0)
    assert w.pending and "resumed" in w.pending[1]
    w.pending = (time.monotonic(), w.pending[1], 3)
    assert run_until(w, lambda: fw.dpi[0] == 1600)


def test_periodic_verify_catches_onboard(env):
    be, dev, idx, fidx, fw, w, nodes, logs = env
    w.rescan(); w.pending = None
    fw.sleep_and_wake(kind="none")
    w.verify()
    assert w.pending and "onboard" in w.pending[1]


def test_no_config_does_nothing(env, tmp_path):
    be, dev, idx, fidx, fw, w, nodes, logs = env
    w.conf = str(tmp_path / "missing.conf")
    fw.sleep_and_wake(kind="none")
    assert w.apply_now("x") and fw.dpi[0] == 800


def test_lock_serialises(env):
    be = env[0]
    order = []

    def hold():
        with be.device_lock():
            order.append("a-in"); time.sleep(0.3); order.append("a-out")

    t = threading.Thread(target=hold); t.start(); time.sleep(0.05)
    with be.device_lock():
        order.append("b")
    t.join()
    assert order == ["a-in", "a-out", "b"]
    with be.device_lock():
        with pytest.raises(be.HidppError, match="busy"):
            with be.device_lock(timeout=0.2):
                pass


def test_notification_parser():
    be = M.load_backend()
    assert be.is_reconnect_notification(bytes([0x10, 1, 0x41, 0x0C, 0x00, 0xBE, 0x40]))
    assert not be.is_reconnect_notification(bytes([0x10, 1, 0x41, 0x0C, 0x40, 0xBE, 0x40]))  # link lost
    assert be.is_reconnect_notification(bytes([0x11, 1, 4, 0x00, 1, 1, 1]) + bytes(13), 4)
    assert not be.is_reconnect_notification(bytes([0x11, 1, 4, 0x00, 1, 1, 1]) + bytes(13), None)
    assert not be.is_reconnect_notification(bytes([0x11, 1, 4, 0x05, 1, 1, 1]) + bytes(13), 4)  # a reply, swid 5
    assert not be.is_reconnect_notification(bytes([0x11, 1, 4, 0x00, 1, 0, 1]) + bytes(13), 4)  # no reconfig request
