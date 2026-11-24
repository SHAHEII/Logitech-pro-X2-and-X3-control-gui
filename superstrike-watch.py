#!/usr/bin/env python3
"""superstrike-watch - keeps your saved SUPERSTRIKE settings after the mouse sleeps / reconnects
or the PC resumes from suspend.

Why: DPI and polling rate are written into the onboard profile, so they normally survive sleep.
This watcher is a safety net: it re-applies ~/.config/superstrike-hits.conf (HITS, and DPI/rate if
they drifted from the saved config) when:
  * the receiver/dongle reports the mouse (re)connected  (HID++ 1.0 0x41 / HID++ 2.0 0x1D4B event,
    the same notifications Solaar uses to detect a reconnect),
  * a receiver/dongle hidraw node appears (plug-in, USB re-enumeration after resume),
  * the PC resumes from suspend (CLOCK_BOOTTIME jumps ahead of CLOCK_MONOTONIC),
  * a cheap periodic check finds live DPI/rate different from the saved config (default every 30 s).
It only blocks in select() (no busy polling), opens hidraw read-only for listening, and takes the shared
device lock while writing, so the GUI/tray keep working.  Runs as a systemd --user service.
"""
import argparse, importlib.util, json, os, select, sys, time

__version__ = "1.0.1"

HERE = os.path.dirname(os.path.abspath(__file__))


def load_backend():
    for p in (os.environ.get("SUPERSTRIKE_BACKEND"), os.path.join(HERE, "superstrike-hits.py"),
              os.path.expanduser("~/.local/share/superstrike-hits/superstrike-hits.py")):
        if p and os.path.exists(p):
            spec = importlib.util.spec_from_file_location("superstrike_hits", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise SystemExit("superstrike-hits.py not found")


def status_path():
    rt = os.environ.get("XDG_RUNTIME_DIR") or os.path.expanduser("~/.cache/superstrike")
    os.makedirs(rt, exist_ok=True)
    return os.path.join(rt, "superstrike-watch.status")


def _clock(name):
    return time.clock_gettime(getattr(time, name))


class Watcher:
    """Event loop. Everything device-specific is injectable so tests can drive it with the mock."""

    def __init__(self, be, conf=None, verify_interval=30.0, settle=1.5, log=print,
                 scan=None, open_listener=None, open_device=None, clock=None):
        self.be = be
        self.conf = conf or be.CONF
        self.verify_interval = verify_interval
        self.settle = settle  # wait after a reconnect before writing (the mouse is still initialising)
        self.log = log
        self.scan = scan or be.hidpp_nodes
        self.open_listener = open_listener or (lambda p: os.open(p, os.O_RDONLY | os.O_NONBLOCK))
        self.open_device = open_device or (lambda p: be.find_device())  # scan all FF00 nodes; ignore the listener path
        self.clock = clock or (lambda: (_clock("CLOCK_MONOTONIC"), _clock("CLOCK_BOOTTIME")))
        self.fds = {}  # path -> fd
        self.status_idx = {}  # path -> 0x1D4B feature index (once known)
        self.pending = None  # (due_time, reason, attempts_left)
        self.last_verify = 0.0
        self.last_apply = None
        self.last_error = None
        self.applies = 0
        self._mono, self._boot = self.clock()

    # ---------------------------------------------------------------- helpers
    def config(self):
        try:
            with open(self.conf) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def write_status(self, state):
        try:
            with open(status_path(), "w") as f:
                json.dump({"state": state, "time": time.time(), "applies": self.applies,
                           "last_apply": self.last_apply, "last_error": self.last_error,
                           "nodes": sorted(self.fds)}, f)
        except OSError:
            pass

    def schedule(self, reason, delay=None, attempts=4):
        due = time.monotonic() + (self.settle if delay is None else delay)
        if self.pending is None or due < self.pending[0]:
            self.pending = (due, reason, attempts)
        self.log(f"event: {reason} -> re-apply in {due - time.monotonic():.1f}s")

    # ---------------------------------------------------------------- node management
    def rescan(self):
        paths = set(self.scan())
        for p in list(self.fds):
            if p not in paths:
                self.drop(p, "node gone")
        for p in sorted(paths - set(self.fds)):
            try:
                self.fds[p] = self.open_listener(p)
            except OSError as e:
                self.log(f"cannot open {p}: {e}")
                continue
            self.log(f"listening on {p}")
            self.schedule(f"receiver node {p} appeared", delay=2.0)

    def drop(self, path, why):
        fd = self.fds.pop(path, None)
        if fd is not None:
            try:
                os.close(fd) if isinstance(fd, int) else fd.close()
            except OSError:
                pass
            self.log(f"stopped listening on {path} ({why})")

    def _fileno(self, fd):
        return fd if isinstance(fd, int) else fd.fileno()

    # ---------------------------------------------------------------- the actual work
    def apply_now(self, reason):
        cfg = self.config()
        if not cfg:
            self.log("no saved config - nothing to re-apply (save one in the GUI)")
            return True
        last = None
        for path in sorted(self.fds) or [None]:
            try:
                with self.be.device_lock():
                    dev, idx, fidx = self.open_device(path)
                    try:
                        self.be.apply_config(dev, idx, fidx, cfg)
                        try:
                            self.status_idx[path] = dev.get_feature_index(idx, self.be.WIRELESS_STATUS_FEATURE_ID)
                        except self.be.HidppError:
                            pass
                    finally:
                        dev.close()
                self.applies += 1
                self.last_apply = {"time": time.time(), "reason": reason, "node": path}
                self.last_error = None
                self.log(f"re-applied saved settings ({reason}) on {path}")
                self.write_status("ok")
                return True
            except (self.be.HidppError, SystemExit, OSError) as e:
                last = str(e)
        self.last_error = last
        self.log(f"re-apply failed ({reason}): {last}")
        self.write_status("waiting for mouse")
        return False

    def verify(self):
        """Safety net: compare live DPI/rate to the saved config. Matching onboard is success."""
        cfg = self.config()
        if not cfg or not (cfg.get("dpi") or cfg.get("polling_hz")):
            return
        for path in sorted(self.fds):
            try:
                with self.be.device_lock(timeout=2):
                    dev, idx, fidx = self.open_device(path)
                    try:
                        drifted = False
                        if cfg.get("dpi"):
                            d = self.be.read_dpi(dev, idx)
                            want = cfg["dpi"]
                            if not d or d["x"] != want["x"]:
                                drifted = True
                            elif want.get("y") is not None and d.get("y") is not None and d["y"] != want["y"]:
                                drifted = True
                            elif want.get("lod") is not None and d.get("lod") is not None and d["lod"] != want["lod"]:
                                drifted = True
                        if cfg.get("polling_hz"):
                            r = self.be.read_rate(dev, idx)
                            if not r or r["hz"] != cfg["polling_hz"]:
                                drifted = True
                    finally:
                        dev.close()
            except (self.be.HidppError, SystemExit, OSError):
                continue  # mouse asleep / not there: fine, a reconnect notification will come
            if drifted:
                self.schedule("periodic check: DPI/rate drifted from saved config", delay=0)
            return

    def step(self, max_wait=None):
        """One loop iteration. Returns after an event or timeout."""
        now = time.monotonic()
        # PC resume: BOOTTIME keeps counting during suspend, MONOTONIC does not
        mono, boot = self.clock()
        if (boot - self._boot) - (mono - self._mono) > 5.0:
            self.schedule("PC resumed from suspend", delay=4.0, attempts=6)
        self._mono, self._boot = mono, boot

        if not self.fds:
            self.rescan()
        timeouts = [self.verify_interval - (now - self.last_verify) if self.verify_interval else 3600]
        if self.pending:
            timeouts.append(self.pending[0] - now)
        if not self.fds:
            timeouts.append(3.0)  # nothing to listen on: look for nodes again soon
        if max_wait is not None:
            timeouts.append(max_wait)
        timeout = max(0.0, min(timeouts))

        fdmap = {self._fileno(fd): p for p, fd in self.fds.items()}
        try:
            ready, _, _ = select.select(list(fdmap), [], [], timeout) if fdmap else ([], [], time.sleep(timeout))
        except (OSError, ValueError):
            ready = []
            self.rescan()
        for fno in ready:
            path = fdmap[fno]
            try:
                data = os.read(fno, 64)
            except BlockingIOError:
                continue
            except OSError as e:
                self.drop(path, str(e))
                continue
            if not data:
                self.drop(path, "EOF")
                continue
            if self.be.is_reconnect_notification(data, self.status_idx.get(path)):
                if self.last_apply and time.time() - self.last_apply["time"] < 3.0:
                    continue  # echo of our own re-apply - ignore
                self.schedule(f"mouse reconnected ({data[:7].hex(' ')})")

        now = time.monotonic()
        if self.pending and now >= self.pending[0]:
            due, reason, attempts = self.pending
            self.pending = None
            if not self.apply_now(reason) and attempts > 1:
                self.pending = (time.monotonic() + 2.0, reason + " (retry)", attempts - 1)
        if self.verify_interval and now - self.last_verify >= self.verify_interval:
            self.last_verify = now
            self.rescan()
            self.verify()

    def run(self):
        self.log("superstrike-watch started")
        self.write_status("starting")
        self.rescan()
        if not self.fds:
            self.write_status("waiting for receiver")
        while True:
            self.step()


def main():
    ap = argparse.ArgumentParser(description="Re-apply saved SUPERSTRIKE settings after sleep/reconnect/resume")
    ap.add_argument("--verify-interval", type=float, default=30.0,
                    help="seconds between safety-net checks of live DPI/rate vs the saved config (0 = off)")
    ap.add_argument("--once", action="store_true", help="apply the saved config once and exit")
    ap.add_argument("--status", action="store_true", help="print the last status written by the running service")
    args = ap.parse_args()
    if args.status:
        try:
            print(open(status_path()).read())
        except OSError:
            print("no status yet (service not running?)")
        return
    be = load_backend()
    w = Watcher(be, verify_interval=args.verify_interval, log=lambda m: print(m, flush=True))
    if args.once:
        w.rescan()
        sys.exit(0 if w.apply_now("manual --once") else 1)
    try:
        w.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
