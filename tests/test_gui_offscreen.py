"""Start the GUI offscreen against the mock mouse, drive every control, save screenshots."""
import importlib.util, os, sys, time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HOME"] = os.environ.get("TEST_HOME", "/tmp/superstrike-test-home")

spec = importlib.util.spec_from_file_location("gui", os.path.join(ROOT, "superstrike-gui.py"))
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)
from PySide6.QtWidgets import QApplication

be = gui.be
be.CONF = os.path.join(os.environ["HOME"], ".config/superstrike-hits.conf")
gui.AUTOAPPLY_FLAG = os.path.join(os.environ["HOME"], ".config/superstrike-hits.autoapply")


def settle(app, w):
    for _ in range(50):
        app.processEvents()
        w.pool.waitForDone(100)
        app.processEvents()
    return w


def main(shots=None):
    app = QApplication.instance() or QApplication(sys.argv)
    gui.apply_dark(app)
    w = gui.Main(mock=True)
    w.show()
    settle(app, w)
    fw = w._fw
    assert w.conn, "not connected"
    assert w.ctl[0]["act"].value() == 5 and w.ctl[0]["hap"].value() == 3 and w.ctl[0]["rt"].value() == 2
    # HITS: link both, set act 2 / haptics off / rapid 1
    w.link.setChecked(True)
    w.ctl[0]["act"].setValue(2); w.ctl[0]["hap"].setValue(1); w.ctl[0]["rt"].setValue(1)
    w.apply_hits(); settle(app, w)
    assert fw.rec[0] == [0x08, 0x05, 0x00] and fw.rec[1] == [0x08, 0x05, 0x00], fw.rec
    # per-button
    w.link.setChecked(False); w.ctl[1]["act"].setValue(9); w.apply_hits(); settle(app, w)
    assert fw.rec[1][0] == 0x24 and fw.rec[0][0] == 0x08
    # raw (invalid then valid)
    w.raw_act.setValue(0x10); w.raw_hap.setValue(0x0C); w.raw_btn.setCurrentText("left"); w.apply_raw(); settle(app, w)
    assert fw.rec[0][0] == 0x10 and fw.rec[0][2] == 0x0C
    # DPI - mouse starts in onboard mode (like the real X3); first try with auto-switch off -> error 5 shown
    assert w.dpi and w.dpi["x"] == 800 and w.mode_lbl.text() == "mode: onboard"
    w.auto_host.setChecked(False)
    w.debug.setChecked(True)
    w.dpi_preset(1600); settle(app, w)
    log = w.log.toPlainText()
    assert "hid++ error 5" in log and "onboard-profile mode" in log and fw.dpi[0] == 800
    assert ">> 11 01 09 6" in log and "<< 11 01 ff 09 6" in log  # raw frames of the refused setSensorDpi
    w.debug.setChecked(False)
    if shots:
        w.tabs.setCurrentIndex(1); settle(app, w); w.grab().save(os.path.join(shots, "screenshot-dpi-error5-before.png"))
    # fixed path: auto host switch on
    w.auto_host.setChecked(True)
    w.dpi_preset(1600); settle(app, w)
    assert fw.dpi[:2] == [1600, 1600], fw.dpi
    assert fw.onboard_mode == 2 and w.mode_lbl.text() == "mode: host" and "Switched the mouse" in w.log.toPlainText()
    w.set_mode("onboard"); settle(app, w); assert fw.onboard_mode == 1
    w.set_mode("host"); settle(app, w); assert fw.onboard_mode == 2
    w.dpi_same.setChecked(False); w.dpi_x.setValue(3210); w.dpi_y.setValue(1234); w.lod.setCurrentIndex(0)
    w.apply_dpi(); settle(app, w)
    assert fw.dpi == [3200, 1250, 0], fw.dpi
    # config save / apply / auto
    w.save_cfg(); settle(app, w)
    assert os.path.exists(be.CONF)
    fw.rec[0] = [0x28, 0x09, 0x14]
    w.apply_cfg(); settle(app, w)
    assert fw.rec[0] == [0x10, 0x05, 0x0C], fw.rec[0]
    w.auto.setChecked(True); assert os.path.exists(gui.AUTOAPPLY_FLAG)
    w.auto.setChecked(False); assert not os.path.exists(gui.AUTOAPPLY_FLAG)
    # features
    w.dump(); settle(app, w)
    assert w.ftable.rowCount() == len(fw.features)
    if shots:
        for i, name in enumerate(["hits", "dpi", "config", "features"]):
            w.tabs.setCurrentIndex(i); settle(app, w)
            w.grab().save(os.path.join(shots, f"screenshot-{name}.png"))
    print("GUI offscreen test OK; log tail:\n" + "\n".join(w.log.toPlainText().splitlines()[-6:]))


def test_gui():
    main()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
