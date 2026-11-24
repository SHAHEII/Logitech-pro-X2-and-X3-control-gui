"""Tray icon, close-to-tray, single instance, login autostart, keep-settings service toggle, --diagnose, installer."""
import importlib.util, json, os, shutil, subprocess, sys, tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SUPERSTRIKE_ASSUME_TRAY"] = "1"  # offscreen has no tray; pretend one exists
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="ss-tray-")
os.environ.setdefault("HOME", TMP)
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_RUNTIME_DIR"] = os.path.join(TMP, "run")
os.makedirs(os.environ["XDG_RUNTIME_DIR"], exist_ok=True)

import pytest

spec = importlib.util.spec_from_file_location("gui_tray", os.path.join(ROOT, "superstrike-gui.py"))
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)
from PySide6.QtWidgets import QApplication

SHOTS = os.environ.get("SHOTS")


class FakeSystemctl:
    def __init__(self):
        self.enabled = False
        self.calls = []

    def __call__(self, *args, timeout=10):
        self.calls.append(args)
        if args[0] == "is-enabled":
            return (0 if self.enabled else 1), ("enabled" if self.enabled else "disabled")
        if args[0] == "is-active":
            return (0 if self.enabled else 3), ("active" if self.enabled else "inactive")
        if args[0] == "enable":
            self.enabled = True
        if args[0] == "disable":
            self.enabled = False
        return 0, ""


def settle(app, w, n=30):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(100)
        app.processEvents()


@pytest.fixture
def ui(tmp_path):
    app = QApplication.instance() or QApplication(sys.argv)
    gui.apply_dark(app)
    gui.be.CONF = str(tmp_path / "superstrike-hits.conf")
    sc = FakeSystemctl()
    w = gui.Main(mock=True, systemctl_fn=sc)
    w.show()
    settle(app, w)
    yield app, w, w._fw, sc
    w._quitting = True
    w.tray.hide()
    w.close()
    settle(app, w, 3)


def actions(menu):
    return {a.text(): a for a in menu.actions()}


def test_tray_menu_and_presets(ui):
    app, w, fw, sc = ui
    acts = actions(w.tray_menu)
    for t in ("Open SUPERSTRIKE Control", "Re-apply saved settings", "Quit", "DPI", "Polling rate"):
        assert t in acts, acts.keys()
    assert w.act_status.text() == "800 DPI · 1000 Hz" and not w.act_status.isEnabled()
    assert "4000 Hz" in actions(w.rate_menu) and "8000 Hz" in actions(w.rate_menu)
    actions(w.dpi_menu)["1600 DPI"].trigger(); settle(app, w)
    assert fw.dpi[0] == 1600 and fw.dpi[1] == 1600 and fw.onboard_mode == 1
    actions(w.rate_menu)["8000 Hz"].trigger(); settle(app, w)
    assert fw.rate_idx == 6
    assert w.act_status.text() == "1600 DPI · 8000 Hz"
    assert actions(w.dpi_menu)["1600 DPI"].isChecked() and actions(w.rate_menu)["8000 Hz"].isChecked()
    if SHOTS:
        w.tray_menu.grab().save(os.path.join(SHOTS, "screenshot-tray-menu.png"))


def test_reapply_from_tray(ui):
    app, w, fw, sc = ui
    actions(w.dpi_menu)["1600 DPI"].trigger(); settle(app, w)
    w.save_cfg(); settle(app, w)
    fw.sleep_and_wake("none")  # sector already has 1600; stays onboard
    w.act_reapply.trigger(); settle(app, w)
    assert fw.dpi[0] == 1600 and fw.onboard_mode == 1


def test_close_to_tray_and_quit(ui):
    app, w, fw, sc = ui
    assert w.close_tray.isChecked()  # default on
    w.close(); settle(app, w, 3)
    assert not w.isVisible() and not w._quitting
    w.tray.activated.emit(gui.QSystemTrayIcon.Trigger); settle(app, w, 3)
    assert w.isVisible()
    w.close_tray.setChecked(False)
    assert w.settings.value("close_to_tray", type=bool) is False
    w.close(); settle(app, w, 3)
    assert w._quitting
    w.settings.setValue("close_to_tray", True)


def test_single_instance_raises_existing(ui):
    app, w, fw, sc = ui
    srv = gui.start_single_instance_server(w)
    w.hide(); settle(app, w, 3)
    assert gui._send_to_running(b"show")
    for _ in range(20):
        settle(app, w, 2)
        if w.isVisible():
            break
    assert w.isVisible()
    srv.close()


def test_login_autostart_file(ui):
    app, w, fw, sc = ui
    w.login_tray.setChecked(True)
    f = gui.autostart_file()
    txt = open(f).read()
    exec_line = [l for l in txt.splitlines() if l.startswith("Exec=")][0]
    assert exec_line.endswith(" --tray") and os.path.isabs(exec_line[5:].split()[0])
    if shutil.which("desktop-file-validate"):
        assert subprocess.run(["desktop-file-validate", f]).returncode == 0
    w.login_tray.setChecked(False)
    assert not os.path.exists(f)


def test_keep_settings_toggle_and_autosave(ui):
    app, w, fw, sc = ui
    assert not os.path.exists(gui.be.CONF)
    w.keep.setChecked(True); w.toggle_keep(True); settle(app, w)
    assert ("enable", "--now", gui.WATCH_UNIT) in sc.calls
    assert os.path.exists(gui.be.CONF)  # saved first, so the service has something to restore
    assert w.keep.isChecked() and "active" in w.svc_lbl.text()
    # with the service on, applied changes are auto-saved
    actions(w.dpi_menu)["2400 DPI"].trigger(); settle(app, w)
    assert json.load(open(gui.be.CONF))["dpi"]["x"] == 2400
    # status file from the watcher is shown
    json.dump({"state": "ok", "last_apply": {"time": 0, "reason": "mouse reconnected"}, "last_error": None},
              open(gui.watch_status_file(), "w"))
    w.refresh_service_status()
    assert "watcher: ok" in w.svc_lbl.text() and "mouse reconnected" in w.svc_lbl.text()
    if SHOTS:
        w.tabs.setCurrentIndex(3); settle(app, w, 3)
        w.grab().save(os.path.join(SHOTS, "screenshot-keep-settings-tray.png"))
    w.keep.setChecked(False); w.toggle_keep(False)
    assert ("disable", "--now", gui.WATCH_UNIT) in sc.calls and not w.keep.isChecked()


def test_stale_fd_reconnects(ui):
    app, w, fw, sc = ui
    old = w.conn
    w._done(None, "write failed on /dev/hidraw4: [Errno 19] No such device", lambda r: None)
    settle(app, w)
    assert w.conn and w.conn is not old and "reconnecting" in w.log.toPlainText()


def test_diagnose_runs():
    lines = []
    assert gui.diagnose(out=lines.append) == 0
    txt = "\n".join(lines)
    assert "PySide6       OK" in txt and "superstrike-watch.service" in txt and "mouse" in txt


def test_installer_absolute_exec_and_uninstall(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), SKIP_ROOT="1", XDG_CONFIG_HOME=str(tmp_path / ".config"))
    r = subprocess.run(["bash", os.path.join(ROOT, "install.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    desk = tmp_path / ".local/share/applications/superstrike-gui.desktop"
    txt = desk.read_text()
    assert f"Exec={tmp_path}/.local/bin/superstrike-gui" in txt and f"TryExec={tmp_path}/.local/bin/superstrike-gui" in txt
    assert "@BIN@" not in txt
    wrapper = (tmp_path / ".local/bin/superstrike-gui").read_text()
    assert "@DEST@" not in wrapper and "gui.log" in wrapper
    for f in ("superstrike-watch.py", "superstrike-gui.py", "superstrike-hits.py"):
        assert (tmp_path / ".local/share/superstrike-hits" / f).exists()
    assert (tmp_path / ".config/systemd/user/superstrike-watch.service").exists()
    # wrapper --diagnose works with the bundled python
    r = subprocess.run([str(tmp_path / ".local/bin/superstrike-gui"), "--diagnose"],
                       env=dict(env, SUPERSTRIKE_PYTHON=sys.executable), capture_output=True, text=True)
    assert "SUPERSTRIKE Control diagnostics" in r.stdout
    (tmp_path / ".config/autostart").mkdir(parents=True)
    (tmp_path / ".config/autostart/superstrike-gui.desktop").write_text("[Desktop Entry]\nExec=x --tray\n")
    r = subprocess.run(["bash", os.path.join(ROOT, "install.sh"), "--uninstall"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not desk.exists() and not (tmp_path / ".local/bin/superstrike-gui").exists()
    assert not (tmp_path / ".config/autostart/superstrike-gui.desktop").exists()
    assert not (tmp_path / ".config/systemd/user/superstrike-watch.service").exists()
