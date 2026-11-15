#!/usr/bin/env python3
"""superstrike-gui - dark PySide6 front-end for superstrike-hits.py (Logitech G PRO X2 SUPERSTRIKE).

Every device operation calls the functions in superstrike-hits.py (find_device, read_btn,
set_button, get_info, dump_features, read_dpi/set_dpi, save_config/apply_config), so the
GUI behaves exactly like the CLI.   Run:  superstrike-gui   [--mock]
"""
import glob, importlib.util, json, os, sys, traceback

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QSlider, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

HERE = os.path.dirname(os.path.abspath(__file__))
AUTOAPPLY_FLAG = os.path.expanduser("~/.config/superstrike-hits.autoapply")
DPI_PRESETS = [400, 800, 1200, 1600, 2400, 3200, 6400]


def load_backend():
    for p in (os.environ.get("SUPERSTRIKE_BACKEND"), os.path.join(HERE, "superstrike-hits.py"),
              os.path.expanduser("~/.local/share/superstrike-hits/superstrike-hits.py")):
        if p and os.path.exists(p):
            spec = importlib.util.spec_from_file_location("superstrike_hits", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise SystemExit("superstrike-hits.py not found next to superstrike-gui.py")


be = load_backend()

DARK_QSS = """
QWidget { background: #16181d; color: #e6e6e6; font-size: 10.5pt; }
QGroupBox { border: 1px solid #2c313a; border-radius: 10px; margin-top: 14px; padding: 10px; background: #1c1f26; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #7dd3fc; font-weight: bold; }
QPushButton { background: #2a2f3a; border: 1px solid #3a4150; border-radius: 7px; padding: 6px 14px; }
QPushButton:hover { background: #343b48; }
QPushButton:disabled { color: #666; }
QPushButton#primary { background: #0ea5e9; color: #0b0d10; border: none; font-weight: bold; }
QPushButton#primary:hover { background: #38bdf8; }
QPushButton#preset { padding: 4px 8px; }
QPushButton#preset:checked { background: #0ea5e9; color: #0b0d10; border: none; font-weight: bold; }
QTabWidget::pane { border: 1px solid #2c313a; border-radius: 8px; top: -1px; }
QTabBar::tab { background: #1c1f26; padding: 8px 16px; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }
QTabBar::tab:selected { background: #2a2f3a; color: #7dd3fc; }
QSlider::groove:horizontal { height: 6px; background: #2c313a; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #0ea5e9; border-radius: 3px; }
QSlider::handle:horizontal { background: #e6e6e6; width: 16px; margin: -6px 0; border-radius: 8px; }
QPlainTextEdit, QTableWidget, QSpinBox, QComboBox { background: #111318; border: 1px solid #2c313a; border-radius: 6px; padding: 3px; }
QHeaderView::section { background: #1c1f26; color: #9aa4b2; border: none; padding: 4px; }
QLabel, QCheckBox, QSlider { background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #4b5563; border-radius: 4px; background: #111318; }
QCheckBox::indicator:checked { background: #0ea5e9; border-color: #0ea5e9; }
QLabel#muted { color: #8b95a5; }
QLabel#big { font-size: 15pt; font-weight: bold; }
QLabel#value { color: #7dd3fc; font-weight: bold; min-width: 26px; }
QStatusBar { background: #111318; color: #9aa4b2; }
"""


class _TraceSig(QObject):
    line = Signal(str)


class _Sig(QObject):
    done = Signal(object, object, object)  # result, error, callback


class Job(QRunnable):
    def __init__(self, fn, cb):
        super().__init__()
        self.fn, self.cb, self.sig = fn, cb, _Sig()

    def run(self):
        try:
            res, err = self.fn(), None
        except SystemExit as e:  # backend find_device() raises SystemExit with a helpful message
            res, err = None, str(e)
        except Exception as e:
            res, err = None, f"{e}"
            traceback.print_exc()
        self.sig.done.emit(res, err, self.cb)


class LevelSlider(QWidget):
    def __init__(self, lo, hi, left_hint, right_hint, fmt=str):
        super().__init__()
        self.fmt = fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(lo, hi)
        self.s.setPageStep(1)
        self.s.setTickPosition(QSlider.TicksBelow)
        self.val = QLabel(objectName="value")
        self.s.valueChanged.connect(lambda v: self.val.setText(self.fmt(v)))
        for w, st in ((QLabel(left_hint, objectName="muted"), 0), (self.s, 1), (QLabel(right_hint, objectName="muted"), 0), (self.val, 0)):
            lay.addWidget(w, st)
        self.setValue(lo)

    def value(self):
        return self.s.value()

    def setValue(self, v):
        self.s.setValue(v)
        self.val.setText(self.fmt(v))


class Main(QMainWindow):
    def __init__(self, mock=False):
        super().__init__()
        self.mock = mock
        self.conn = None  # (dev, dev_idx, fidx)
        self.dpi = None
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)  # one HID++ transaction at a time
        self.setWindowTitle("SUPERSTRIKE Control")
        self.resize(860, 800)

        root = QWidget()
        v = QVBoxLayout(root)
        v.addLayout(self._header())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._hits_tab(), "Click (HITS)")
        self.tabs.addTab(self._dpi_tab(), "DPI")
        self.tabs.addTab(self._rate_tab(), "Polling rate")
        self.tabs.addTab(self._config_tab(), "Saved config && auto-apply")
        self.tabs.addTab(self._features_tab(), "Feature table")
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(2000)
        logbox = QWidget()
        lv = QVBoxLayout(logbox)
        lv.setContentsMargins(0, 0, 0, 0)
        lrow = QHBoxLayout()
        self.debug = QCheckBox("Debug: log raw HID++ frames (>> sent, << received)")
        self.debug.toggled.connect(self.toggle_trace)
        copyb = QPushButton("Copy log")
        copyb.clicked.connect(lambda: QApplication.clipboard().setText(self.log.toPlainText()))
        lrow.addWidget(self.debug)
        lrow.addStretch(1)
        lrow.addWidget(copyb)
        lv.addLayout(lrow)
        lv.addWidget(self.log)
        self._trace_sig = _TraceSig()
        self._trace_sig.line.connect(lambda l: self.log.appendPlainText("   " + l))
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.tabs)
        split.addWidget(logbox)
        split.setSizes([480, 150])
        v.addWidget(split, 1)
        self.setCentralWidget(root)
        self.connect_device()

    # ------------------------------------------------------------------ layout
    def _header(self):
        h = QHBoxLayout()
        col = QVBoxLayout()
        self.title = QLabel("Not connected", objectName="big")
        self.sub = QLabel("", objectName="muted")
        col.addWidget(self.title)
        col.addWidget(self.sub)
        h.addLayout(col, 1)
        self.devsel = QComboBox()
        self.devsel.setToolTip("Which /dev/hidraw node to use (--device). Auto finds the HID++ interface itself.")
        self._fill_devsel()
        h.addWidget(QLabel("Device:", objectName="muted"))
        h.addWidget(self.devsel)
        b = QPushButton("Connect")
        b.setObjectName("primary")
        b.clicked.connect(self.connect_device)
        h.addWidget(b)
        return h

    def _fill_devsel(self):
        self.devsel.clear()
        self.devsel.addItem("Auto-detect", None)
        for p in sorted(glob.glob("/dev/hidraw*")):
            n = os.path.basename(p)
            hid = be.sysfs_hid_id(n)
            if hid and hid[1] == be.VID:
                self.devsel.addItem(f"{p} (046d:{hid[2]:04x}{', HID++' if be.has_ff00(n) else ''})", p)

    def _hits_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        expl = QLabel("<b>Actuation</b>: how deep you press before it clicks (1 = hair trigger, 10 = deep). "
                      "<b>Click haptics</b>: strength of the simulated click (1 = off, 6 = strongest). "
                      "<b>Rapid trigger</b>: how soon the click releases when you lift (1 = fastest).")
        expl.setWordWrap(True)
        expl.setObjectName("muted")
        v.addWidget(expl)
        grid = QGridLayout()
        self.ctl = {}
        for col, (btn, name) in enumerate(((0, "Left button"), (1, "Right button"))):
            box = QGroupBox(name)
            f = QFormLayout(box)
            act = LevelSlider(be.ACT_MIN, be.ACT_MAX, "light", "deep")
            hap = LevelSlider(be.HAP_MIN, be.HAP_MAX, "off", "max", lambda x: "off" if x == 1 else str(x))
            rt = LevelSlider(be.RT_MIN, be.RT_MAX, "fast", "slow")
            raw = QLabel("raw: -", objectName="muted")
            f.addRow("Actuation point", act)
            f.addRow("Click haptics", hap)
            f.addRow("Rapid trigger", rt)
            f.addRow(raw)
            self.ctl[btn] = dict(act=act, hap=hap, rt=rt, raw=raw)
            grid.addWidget(box, 0, col)
        v.addLayout(grid)
        self.link = QCheckBox("Same settings for both buttons (left drives right)")
        self.link.setChecked(True)
        for k in ("act", "hap", "rt"):
            self.ctl[0][k].s.valueChanged.connect(lambda val, k=k: self.link.isChecked() and self.ctl[1][k].setValue(val))
        v.addWidget(self.link)
        row = QHBoxLayout()
        d = QPushButton("Defaults (5 / 3 / 3)")
        d.clicked.connect(self.hits_defaults)
        r = QPushButton("Read from mouse")
        r.clicked.connect(self.read_hits)
        self.apply_btn = QPushButton("Apply to mouse")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply_hits)
        row.addWidget(d)
        row.addStretch(1)
        row.addWidget(r)
        row.addWidget(self.apply_btn)
        v.addLayout(row)

        adv = QGroupBox("Advanced: raw bytes (--raw-actuation / --raw-haptics)")
        a = QHBoxLayout(adv)
        self.raw_act = QSpinBox(prefix="0x", displayIntegerBase=16, minimum=4, maximum=0x28, singleStep=4)
        self.raw_hap = QSpinBox(prefix="0x", displayIntegerBase=16, minimum=0, maximum=0x14, singleStep=4)
        self.raw_btn = QComboBox()
        self.raw_btn.addItems(["both", "left", "right"])
        rb = QPushButton("Write raw")
        rb.clicked.connect(self.apply_raw)
        for wdg in (QLabel("actuation"), self.raw_act, QLabel("haptics"), self.raw_hap, QLabel("button"), self.raw_btn, rb):
            a.addWidget(wdg)
        a.addStretch(1)
        v.addWidget(adv)
        v.addStretch(1)
        return w

    def _dpi_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        box = QGroupBox("Sensitivity")
        f = QFormLayout(box)
        self.dpi_cur = QLabel("-", objectName="big")
        self.dpi_range = QLabel("", objectName="muted")
        self.dpi_x = QSpinBox(minimum=50, maximum=48000, singleStep=50)
        self.dpi_y = QSpinBox(minimum=50, maximum=48000, singleStep=50)
        self.dpi_same = QCheckBox("Y = X")
        self.dpi_same.setChecked(True)
        self.dpi_same.toggled.connect(lambda c: self.dpi_y.setEnabled(not c and bool(self.dpi and self.dpi["has_y"])))
        self.lod = QComboBox()
        self.lod.addItems(["LOW", "MEDIUM", "HIGH"])
        yrow = QHBoxLayout()
        yrow.addWidget(self.dpi_y)
        yrow.addWidget(self.dpi_same)
        f.addRow("Current", self.dpi_cur)
        f.addRow("", self.dpi_range)
        f.addRow("DPI X", self.dpi_x)
        f.addRow("DPI Y", yrow)
        f.addRow("Lift-off distance", self.lod)
        pre = QHBoxLayout()
        self.preset_btns = []
        for p in DPI_PRESETS:
            b = QPushButton(str(p), objectName="preset")
            b.clicked.connect(lambda _=False, p=p: self.dpi_preset(p))
            pre.addWidget(b)
            self.preset_btns.append((p, b))
        f.addRow("Presets", pre)
        v.addWidget(box)
        mbox = QGroupBox("Profile mode (HID++ 0x8100)")
        mf = QVBoxLayout(mbox)
        mrow = QHBoxLayout()
        self.mode_lbl = QLabel("mode: -", objectName="value")
        self.host_btn = QPushButton("Switch to host mode")
        self.host_btn.clicked.connect(lambda: self.set_mode("host"))
        self.onb_btn = QPushButton("Back to onboard profile")
        self.onb_btn.clicked.connect(lambda: self.set_mode("onboard"))
        mrow.addWidget(self.mode_lbl)
        mrow.addStretch(1)
        mrow.addWidget(self.host_btn)
        mrow.addWidget(self.onb_btn)
        mf.addLayout(mrow)
        self.auto_host = QCheckBox("Switch to host mode automatically when applying DPI")
        self.auto_host.setChecked(True)
        mf.addWidget(self.auto_host)
        mnote = QLabel("In <b>onboard</b> mode the mouse's stored profile owns DPI, and the mouse refuses DPI changes "
                       "(HID++ error 5). <b>Host</b> mode lets this app set DPI, but the mouse goes back to its onboard "
                       "profile when it powers off. Auto-apply (next tab) restores your DPI on every connect. "
                       "Click (HITS) settings are stored on the mouse in both modes.", objectName="muted")
        mnote.setWordWrap(True)
        mf.addWidget(mnote)
        v.addWidget(mbox)
        row = QHBoxLayout()
        r = QPushButton("Read from mouse")
        r.clicked.connect(self.read_dpi)
        a = QPushButton("Apply DPI")
        a.setObjectName("primary")
        a.clicked.connect(self.apply_dpi)
        row.addStretch(1)
        row.addWidget(r)
        row.addWidget(a)
        v.addLayout(row)
        note = QLabel("Values snap to the nearest step the mouse reports.", objectName="muted")
        v.addWidget(note)
        v.addStretch(1)
        return w

    def _rate_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        box = QGroupBox("Polling rate (report rate)")
        f = QFormLayout(box)
        self.rate_cur = QLabel("-", objectName="big")
        self.rate_info = QLabel("", objectName="muted")
        f.addRow("Current", self.rate_cur)
        f.addRow("", self.rate_info)
        self.rate_presets_w = QWidget()
        self.rate_presets = QHBoxLayout(self.rate_presets_w)
        self.rate_presets.setContentsMargins(0, 0, 0, 0)
        f.addRow("Presets", self.rate_presets_w)
        crow = QHBoxLayout()
        self.rate_custom = QSpinBox(minimum=1, maximum=8000, singleStep=100, suffix=" Hz")
        self.rate_custom.setValue(1000)
        self.rate_custom.setToolTip("Type any number up to 8000 Hz (the cap). The mouse only supports fixed hardware rates, "
                                    "so the number is snapped to the nearest one it reports before anything is sent.")
        self.rate_custom.valueChanged.connect(self.update_snap_label)
        cb = QPushButton("Apply custom")
        cb.setObjectName("primary")
        cb.clicked.connect(lambda: self.apply_rate(self.rate_custom.value()))
        crow.addWidget(self.rate_custom)
        crow.addWidget(cb)
        crow.addStretch(1)
        f.addRow("Custom (max 8000)", crow)
        self.snap_lbl = QLabel("", objectName="value")
        f.addRow("", self.snap_lbl)
        v.addWidget(box)
        note = QLabel("HID++ only lets a mouse run at the fixed rates it reports, usually 125 / 250 / 500 / 1000 / 2000 / "
                      "4000 / 8000 Hz. In-between numbers like 400 or 3000 Hz don't exist in the hardware, so a custom "
                      "number is snapped to the nearest supported rate (a tie goes to the lower one) and only that "
                      "rate is sent. 8000 Hz uses a lot more battery (Tom's Hardware: under 40 h on the X3). Like DPI, the "
                      "rate is owned by the onboard profile, so the app switches to host mode first if "
                      "'Switch to host mode automatically' on the DPI tab is ticked.", objectName="muted")
        note.setWordWrap(True)
        v.addWidget(note)
        row = QHBoxLayout()
        r = QPushButton("Read from mouse")
        r.clicked.connect(self.read_rate)
        row.addStretch(1)
        row.addWidget(r)
        v.addLayout(row)
        v.addStretch(1)
        self.rate = None
        return w

    def read_rate(self):
        def ok(info):
            self.rate = info
            while self.rate_presets.count():
                it = self.rate_presets.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            if not info:
                self.rate_cur.setText("not supported")
                return
            link = "wired" if info["conn"] == 0 else "wireless"
            self.rate_cur.setText(f"{info['hz']} Hz")
            self.rate_info.setText(f"{link} link · supported: {', '.join(map(str, info['supported']))} Hz · "
                                   f"HID++ 0x{info['feature']:04X}")
            for hz in info["supported"]:
                b = QPushButton(f"{hz}", objectName="preset")
                b.setCheckable(True)
                b.setChecked(hz == info["hz"])
                b.clicked.connect(lambda _=False, hz=hz: self.apply_rate(hz))
                self.rate_presets.addWidget(b)
            if 400 not in info["supported"]:
                self.rate_presets.addWidget(QLabel("(no 400 Hz in hardware)", objectName="muted"))
            self.rate_presets.addStretch(1)
            self.update_snap_label()
            self.say(be.fmt_rate(info).strip())

        self.run(lambda: be.read_rate(self.conn[0], self.conn[1]), ok)

    def update_snap_label(self):
        if not self.rate or not self.rate["supported"]:
            self.snap_lbl.setText("")
            return
        want = self.rate_custom.value()
        hz, lo, hi = be.snap_rate(self.rate["supported"], want)
        if hz == want:
            self.snap_lbl.setText(f"{want} Hz is a supported rate ✔")
        else:
            nb = " / ".join(str(x) for x in dict.fromkeys(v for v in (lo, hi) if v))
            self.snap_lbl.setText(f"snapped {want} → {hz} Hz   (supported neighbours: {nb})")

    def apply_rate(self, want):
        if not self.rate:
            return self.say("Polling rate not available on this device", True)
        hz, lo, hi = be.snap_rate(self.rate["supported"], want)
        if hz != want:
            self.say(f"{want} Hz isn't a hardware rate → using {hz} Hz")
        host_mode = "auto" if self.auto_host.isChecked() else "never"

        def ok(res):
            if res.get("switched_to_host"):
                self.say("Switched the mouse to host mode so the polling rate can be changed "
                         "(back to the onboard profile after power-off; enable auto-apply to keep it).")
            self.say(f"Polling rate set to {res['hz']} Hz ✔")
            self.read_rate()
            self.read_dpi()

        self.run(lambda: be.set_rate(self.conn[0], self.conn[1], hz, host_mode=host_mode), ok)

    def _config_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        box = QGroupBox(f"Saved config  ({be.CONF})")
        b = QVBoxLayout(box)
        self.cfg_view = QPlainTextEdit(readOnly=True)
        b.addWidget(self.cfg_view)
        row = QHBoxLayout()
        s = QPushButton("Save current mouse settings (--save)")
        s.clicked.connect(self.save_cfg)
        a = QPushButton("Apply saved config now (--apply)")
        a.setObjectName("primary")
        a.clicked.connect(self.apply_cfg)
        row.addWidget(s)
        row.addStretch(1)
        row.addWidget(a)
        b.addLayout(row)
        v.addWidget(box)
        box2 = QGroupBox("Auto-apply")
        b2 = QVBoxLayout(box2)
        self.auto = QCheckBox("Re-apply the saved config at login and whenever the receiver is plugged in")
        self.auto.setChecked(os.path.exists(AUTOAPPLY_FLAG))
        self.auto.toggled.connect(self.toggle_auto)
        b2.addWidget(self.auto)
        lab = QLabel("Uses the superstrike-apply systemd user service + udev hook installed by install.sh.", objectName="muted")
        lab.setWordWrap(True)
        b2.addWidget(lab)
        v.addWidget(box2)
        self.refresh_cfg_view()
        return w

    def _features_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.ftable = QTableWidget(0, 4)
        self.ftable.setHorizontalHeaderLabels(["Index", "Feature ID", "Name", "Flags / version"])
        self.ftable.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.ftable.verticalHeader().setVisible(False)
        v.addWidget(self.ftable)
        b = QPushButton("Read feature table (--dump-features)")
        b.clicked.connect(self.dump)
        v.addWidget(b, 0, Qt.AlignRight)
        return w

    # ------------------------------------------------------------------ helpers
    def say(self, msg, err=False):
        self.log.appendPlainText(("✖ " if err else "• ") + msg)
        self.statusBar().showMessage(msg.splitlines()[0], 8000)

    def run(self, fn, cb, need=True):
        if need and not self.conn:
            return self.say("Not connected: plug in the receiver, wake the mouse, press Connect", True)
        job = Job(fn, cb)
        job.sig.done.connect(self._done)
        self.pool.start(job)

    def _done(self, res, err, cb):
        if err:
            if self.title.text() == "Searching…":
                self.title.setText("No SUPERSTRIKE found")
                self.sub.setText("Plug in the receiver, move the mouse to wake it, then press Connect")
            self.say(err, True)
            if QApplication.platformName() not in ("offscreen", "minimal"):
                QMessageBox.warning(self, "SUPERSTRIKE", err)
            return
        cb(res)

    def targets(self, which):
        return {"both": [0, 1], "left": [0], "right": [1]}[which]

    # ------------------------------------------------------------------ actions
    def connect_device(self):
        if self.conn:
            self.conn[0].close()
            self.conn = None

        def work():
            if self.mock:
                import superstrike_mock
                _b, dev, idx, fidx, self._fw = superstrike_mock.make_mock(be)
            else:
                dev, idx, fidx = be.find_device(self.devsel.currentData())
            name = "PRO X2 SUPERSTRIKE"
            try:
                fs = dev.get_feature_index(idx, 0x0005)
                n = dev.request(idx, fs, 0)[0]
                s = b""
                while len(s) < n:
                    s += bytes(dev.request(idx, fs, 1, bytes([len(s)])))
                name = s[:n].decode(errors="replace")
            except Exception:
                pass
            return dev, idx, fidx, name

        def ok(r):
            dev, idx, fidx, name = r
            self.conn = (dev, idx, fidx)
            where = "wired / direct" if idx == 0xFF else f"wireless, slot {idx}"
            self.title.setText(name)
            self.sub.setText(f"{dev.path}  ·  {where}  ·  HITS feature index 0x{fidx:02x}")
            self.say(f"Connected to {name} on {dev.path}")
            self.read_hits()
            self.read_dpi()
            self.read_rate()

        self.title.setText("Searching…")
        self.run(work, ok, need=False)

    def read_hits(self):
        def work():
            dev, idx, fidx = self.conn
            return [be.read_btn(dev, idx, fidx, b) for b in (0, 1)], be.get_info(dev, idx, fidx)

        def ok(r):
            recs, info = r
            same = recs[0][1:] == recs[1][1:]
            self.link.setChecked(same)
            for b, rec in enumerate(recs):
                c = self.ctl[b]
                c["act"].setValue(be.act_raw_to_level(rec[1]))
                c["hap"].setValue(be.hap_raw_to_level(rec[3]))
                c["rt"].setValue(be.rt_raw_to_level(rec[2]))
                c["raw"].setText(f"raw record: {bytes(rec).hex(' ')}   (act 0x{rec[1]:02x}, x 0x{rec[2]:02x}, hap 0x{rec[3]:02x})")
            self.raw_act.setMaximum(info[2] or 0x28)
            self.raw_act.setValue(recs[0][1])
            self.raw_hap.setValue(recs[0][3])
            self.say(f"Read HITS: left {recs[0].hex(' ')} / right {recs[1].hex(' ')}  (getInfo {info.hex(' ')})")

        self.run(work, ok)

    def hits_defaults(self):
        for b in (0, 1):
            self.ctl[b]["act"].setValue(5)
            self.ctl[b]["hap"].setValue(3)
            self.ctl[b]["rt"].setValue(3)

    def apply_hits(self):
        vals = {b: (self.ctl[b]["act"].value(), self.ctl[b]["hap"].value(), self.ctl[b]["rt"].value()) for b in (0, 1)}

        def work():
            dev, idx, fidx = self.conn
            for b, (a, h, r) in vals.items():
                be.set_button(dev, idx, fidx, b, be.act_level_to_raw(a), be.hap_level_to_raw(h), r)

        def ok(_):
            self.say("Saved click settings to the mouse ✔")
            self.read_hits()

        self.run(work, ok)

    def apply_raw(self):
        a, h, which = self.raw_act.value(), self.raw_hap.value(), self.raw_btn.currentText()

        def work():
            dev, idx, fidx = self.conn
            info = be.get_info(dev, idx, fidx)
            be.check_raw("actuation", a, info[2])
            be.check_raw("haptics", h, 0x14)
            for b in self.targets(which):
                be.set_button(dev, idx, fidx, b, a, h)

        def ok(_):
            self.say(f"Wrote raw actuation 0x{a:02x} / haptics 0x{h:02x} to {which}")
            self.read_hits()

        self.run(work, ok)

    def read_dpi(self):
        def ok(info):
            self.dpi = info
            self.refresh_mode()
            if not info:
                self.dpi_cur.setText("not supported")
                return
            self.dpi_x.setRange(info["x_list"][0], info["x_list"][-1])
            self.dpi_x.setValue(info["x"])
            step = info["x_list"][1] - info["x_list"][0] if len(info["x_list"]) > 1 else 50
            self.dpi_x.setSingleStep(step)
            self.dpi_y.setSingleStep(step)
            if info["has_y"]:
                self.dpi_y.setRange(info["y_list"][0], info["y_list"][-1])
                self.dpi_y.setValue(info["y"])
                self.dpi_same.setChecked(info["x"] == info["y"])
            self.dpi_y.setEnabled(info["has_y"] and not self.dpi_same.isChecked())
            self.dpi_same.setEnabled(info["has_y"])
            self.lod.setEnabled(info["has_lod"])
            if info["has_lod"] and info["lod"] is not None:
                self.lod.setCurrentIndex(info["lod"])
            for p, b in self.preset_btns:
                b.setEnabled(info["x_list"][0] <= p <= info["x_list"][-1])
            cur = f"{info['x']}" + (f" × {info['y']}" if info["has_y"] and info["y"] != info["x"] else "")
            self.dpi_cur.setText(f"{cur} DPI")
            self.dpi_range.setText(f"allowed {info['x_list'][0]}–{info['x_list'][-1]} ({len(info['x_list'])} steps) "
                                   f"via HID++ 0x{info['feature']:04X}")
            self.say(be.fmt_dpi(info).strip())

        def work():
            info = be.read_dpi(self.conn[0], self.conn[1])
            mode = be.get_onboard_mode(self.conn[0], self.conn[1])
            if info is not None:
                info = dict(info, mode=mode)
            self._show_mode(mode)
            return info

        self.run(work, ok)

    def _show_mode(self, mode):
        self._mode = mode
        # (called from the worker; widgets are updated in refresh_mode on the GUI thread)

    def refresh_mode(self):
        mode = getattr(self, "_mode", None)
        self.mode_lbl.setText(f"mode: {mode or 'not supported'}")
        self.host_btn.setEnabled(mode == "onboard")
        self.onb_btn.setEnabled(mode == "host")

    def set_mode(self, mode):
        def work():
            return be.set_onboard_mode(self.conn[0], self.conn[1], mode)

        def ok(m):
            self._mode = m
            self.refresh_mode()
            self.say(f"Profile mode is now: {m}" + ("  (DPI can be set; lost on power-off unless auto-apply is on)" if m == "host" else ""))
            self.read_dpi()

        self.run(work, ok)

    def dpi_preset(self, p):
        self.dpi_x.setValue(p)
        if self.dpi_same.isChecked():
            self.dpi_y.setValue(p)
        self.apply_dpi()

    def apply_dpi(self):
        if not self.dpi:
            return self.say("DPI not available on this device", True)
        info = self.dpi
        x = be.nearest_dpi(info["x_list"], self.dpi_x.value())
        y = None
        if info["has_y"]:
            y = x if self.dpi_same.isChecked() else be.nearest_dpi(info["y_list"], self.dpi_y.value())
        lod = self.lod.currentIndex() if info["has_lod"] else None
        host_mode = "auto" if self.auto_host.isChecked() else "never"

        def ok(res):
            if res.get("switched_to_host"):
                self.say("Switched the mouse from onboard-profile mode to host mode so DPI can be changed. "
                         "It returns to the onboard profile after power-off; enable auto-apply to keep your DPI.")
            self.say(f"DPI set to {x}" + (f" / {y}" if y and y != x else "") + " ✔")
            self.read_dpi()

        self.run(lambda: be.set_dpi(self.conn[0], self.conn[1], x, y, lod, host_mode=host_mode), ok)

    def refresh_cfg_view(self):
        try:
            self.cfg_view.setPlainText(open(be.CONF).read())
        except OSError:
            self.cfg_view.setPlainText("(no saved config yet. Set things up, then press Save.)")

    def save_cfg(self):
        self.run(lambda: be.save_config(*self.conn),
                 lambda r: (self.say(f"Saved config -> {r[0]}"), self.refresh_cfg_view()))

    def apply_cfg(self):
        def work():
            if not os.path.exists(be.CONF):
                raise RuntimeError("no saved config yet: press Save first")
            cfg = json.load(open(be.CONF))
            be.apply_config(*self.conn, cfg)
            return cfg

        self.run(work, lambda cfg: (self.say(f"Applied saved config: {json.dumps(cfg)}"), self.read_hits(), self.read_dpi(),
                                    self.read_rate()))

    def toggle_auto(self, on):
        try:
            os.makedirs(os.path.dirname(AUTOAPPLY_FLAG), exist_ok=True)
            if on:
                open(AUTOAPPLY_FLAG, "w").write("enabled by superstrike-gui\n")
                if not os.path.exists(be.CONF):
                    self.say("Auto-apply on, but there is no saved config yet: press Save", True)
                    return
            elif os.path.exists(AUTOAPPLY_FLAG):
                os.remove(AUTOAPPLY_FLAG)
            self.say(f"Auto-apply {'enabled' if on else 'disabled'}")
        except OSError as e:
            self.say(f"Could not change auto-apply: {e}", True)

    def toggle_trace(self, on):
        be.TRACE = self._trace_sig.line.emit if on else None
        self.say("Raw HID++ frame logging " + ("ON - reproduce the problem, then press Copy log" if on else "off"))

    def dump(self):
        def ok(rows):
            self.ftable.setRowCount(len(rows))
            for r, (i, fid, flags, ver) in enumerate(rows):
                name = be.FEATURE_NAMES.get(fid, "")
                fl = ", ".join(n for bit, n in ((0x80, "obsolete"), (0x40, "hidden"), (0x20, "internal")) if flags & bit)
                for c, txt in enumerate((f"0x{i:02x}", f"0x{fid:04X}", name, f"{fl or '-'} / v{ver}")):
                    it = QTableWidgetItem(txt)
                    if fid == be.ANALOG_FEATURE_ID:
                        it.setForeground(QColor("#7dd3fc"))
                    self.ftable.setItem(r, c, it)
            self.say(f"Feature table: {len(rows)} features")

        self.run(lambda: be.dump_features(self.conn[0], self.conn[1]), ok)


def apply_dark(app):
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in ((QPalette.Window, "#16181d"), (QPalette.WindowText, "#e6e6e6"), (QPalette.Base, "#111318"),
                      (QPalette.AlternateBase, "#1c1f26"), (QPalette.Text, "#e6e6e6"), (QPalette.Button, "#2a2f3a"),
                      (QPalette.ButtonText, "#e6e6e6"), (QPalette.Highlight, "#0ea5e9"), (QPalette.HighlightedText, "#0b0d10")):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(DARK_QSS)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("superstrike-gui")
    app.setDesktopFileName("superstrike-gui")
    apply_dark(app)
    w = Main(mock="--mock" in sys.argv)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
