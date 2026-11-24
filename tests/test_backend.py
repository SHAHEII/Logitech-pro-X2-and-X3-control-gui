"""Backend tests against the simulated mouse (real Device.request code over a socketpair)."""
import json, os, sys, time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import superstrike_mock as M


@pytest.fixture
def m():
    return M.make_mock()


def test_levels_and_mapping(m):
    be, dev, i, f, fw = m
    assert f == 0x0c
    assert be.get_info(dev, i, f) == bytes.fromhex("000328141401")
    rec = be.read_btn(dev, i, f, 0)
    assert (be.act_raw_to_level(rec[1]), be.hap_raw_to_level(rec[3]), be.rt_raw_to_level(rec[2])) == (5, 3, 2)
    be.set_button(dev, i, f, 0, be.act_level_to_raw(1), be.hap_level_to_raw(1), 1)
    assert fw.rec[0] == [0x04, 0x05, 0x00]  # rapid level 1 keeps flag bit 0 of x=0x09
    be.set_button(dev, i, f, 1, be.act_level_to_raw(10), be.hap_level_to_raw(6), 5)
    assert fw.rec[1] == [0x28, 0x15, 0x14]


def test_range_checks(m):
    be, dev, i, f, fw = m
    with pytest.raises(be.HidppError):
        be.set_button(dev, i, f, 0, rt_level=0)
    with pytest.raises(be.HidppError):
        be.set_button(dev, i, f, 0, rt_level=6)
    with pytest.raises(be.HidppError):
        be.check_raw("actuation", 0x2C, 0x28)
    with pytest.raises(be.HidppError):
        be.check_raw("haptics", 0x05, 0x14)


def test_error_frames_are_fast(m):
    be, dev, i, f, fw = m
    t = time.time()
    with pytest.raises(be.HidppError, match="INVALID_ARGUMENT"):
        be.set_button(dev, i, f, 0, act_raw=0x30)  # over max -> device rejects
    assert time.time() - t < 0.5  # used to wait ~2.4 s before the error-frame fix
    t = time.time()
    with pytest.raises(be.HidppError):
        dev.ping(3)  # empty receiver slot -> HID++ 1.0 error
    assert time.time() - t < 0.5


def test_dpi_extended(m):
    be, dev, i, f, fw = m
    d = be.read_dpi(dev, i)
    assert d["feature"] == 0x2202 and d["x"] == 800 and d["has_y"] and d["has_lod"]
    assert d["x_list"][0] == 100 and d["x_list"][-1] == 32000 and 1650 in d["x_list"]
    d = be.set_dpi(dev, i, 1600, 3200, 0)
    assert fw.dpi == [1600, 3200, 0] and d["y"] == 3200
    for bad in (1234, 50, 40000):
        with pytest.raises(be.HidppError, match="not allowed"):
            be.set_dpi(dev, i, bad)
    with pytest.raises(be.HidppError):
        be.set_dpi(dev, i, 800, 1234)
    with pytest.raises(be.HidppError):
        be.set_dpi(dev, i, 800, 800, 5)
    assert be.nearest_dpi(d["x_list"], 1234) == 1250


def test_dpi_legacy_2201():
    be, dev, i, f, fw = M.make_mock(dpi_feature=0x2201)
    d = be.read_dpi(dev, i)
    assert d["feature"] == 0x2201 and d["x_list"] == [200, 400, 800, 1600] and not d["has_y"]
    be.set_dpi(dev, i, 400)
    assert fw.dpi[0] == 400
    with pytest.raises(be.HidppError):
        be.set_dpi(dev, i, 500)


def test_no_dpi_feature():
    be, dev, i, f, fw = M.make_mock(dpi_feature=None)
    assert be.read_dpi(dev, i) is None
    with pytest.raises(be.HidppError):
        be.set_dpi(dev, i, 800)


def test_config_roundtrip(m, tmp_path):
    be, dev, i, f, fw = m
    be.set_button(dev, i, f, 0, 0x0C, 0x10, 4)
    be.set_dpi(dev, i, 2400, 2400, 1)
    path, cfg = be.save_config(dev, i, f, str(tmp_path / "c.conf"))
    assert cfg["left"] == {"actuation": 3, "haptics": 5, "rapid_trigger": 4} and cfg["dpi"]["x"] == 2400
    fw.rec[0] = [0x28, 0x09, 0x00]; fw.dpi = [800, 800, 2]
    be.apply_config(dev, i, f, json.load(open(path)))
    assert fw.rec[0] == [0x0C, 0x11, 0x10] and fw.dpi == [2400, 2400, 1]
    # an old upstream config (no rapid_trigger / dpi) still applies
    be.apply_config(dev, i, f, {"left": {"actuation": 7, "haptics": 2}, "right": {"actuation": 7, "haptics": 2}})
    assert fw.rec[0][0] == 0x1C and fw.rec[0][2] == 0x04 and fw.dpi == [2400, 2400, 1]
    with pytest.raises(be.HidppError):
        be.apply_config(dev, i, f, {"left": {"actuation": 11}})


def test_features(m):
    be, dev, i, f, fw = m
    rows = be.dump_features(dev, i)
    assert [r[1] for r in rows] == fw.features


def test_cli_help():
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, os.path.join(root, "superstrike-hits.py"), "-h"], capture_output=True, text=True).stdout
    for opt in ("--rapid-trigger", "--dpi", "--dpi-y", "--lod", "--dpi-list", "--dump-features", "--raw-haptics", "--apply"):
        assert opt in out
    r = subprocess.run([sys.executable, os.path.join(root, "superstrike-hits.py"), "--haptics", "7"], capture_output=True, text=True)
    assert r.returncode != 0 and "haptics must be 1..6" in r.stderr


def test_crc16():
    be = M.load_backend()
    assert be.crc16(b"123456789") == 0x29B1


def test_x3_dpi_error5_reproduced_and_fixed(m):
    """Live 0x2202 fn6 is still refused with error 5 in onboard mode; auto writes the format-8 profile instead."""
    be, dev, i, f, fw = m
    assert be.get_onboard_mode(dev, i) == "onboard"
    didx = dev.get_feature_index(i, 0x2202)
    payload = bytes([0x00, 0x06, 0x40, 0x06, 0x40, 0x02])  # sensor 0, X=1600, Y=1600 (BE), LOD byte as read
    with pytest.raises(be.HidppError) as e:
        dev.request(i, didx, 6, payload)
    assert e.value.code == 5
    with pytest.raises(be.HidppError, match="onboard-profile mode"):
        be.set_dpi(dev, i, 1600, 1600, 2, host_mode="never")
    assert fw.dpi[0] == 800
    # auto: write the onboard profile, stay onboard, matching existing slot 1; no live 0x2202 fn6
    before = [c for c in fw.calls if c[0] == 0x2202 and c[1] == 6]
    res = be.set_dpi(dev, i, 1600, 1600, 2)
    assert res["wrote_onboard"] and not res["switched_to_host"]
    assert res["x"] == 1600 and be.get_onboard_mode(dev, i) == "onboard" and fw.onboard_mode == 1
    assert [c for c in fw.calls if c[0] == 0x2202 and c[1] == 6] == before
    assert be.parse_ext_profile(fw.sectors[1])["dpi_index"] == 1
    fw.sleep_and_wake("none")
    assert fw.dpi[0] == 1600 and fw.onboard_mode == 1


def test_host_mode_host_still_live_writes(m):
    be, dev, i, f, fw = m
    payload = bytes([0x00, 0x06, 0x40, 0x06, 0x40, 0x02])
    res = be.set_dpi(dev, i, 1600, 1600, 2, host_mode="host")
    assert res["switched_to_host"] and not res["wrote_onboard"]
    assert fw.onboard_mode == 2 and res["x"] == 1600
    sent = [c for c in fw.calls if c[0] == 0x2202 and c[1] == 6][-1][2][:6]
    assert sent == payload
    # already in host mode, no further switch
    assert be.set_dpi(dev, i, 800, host_mode="host")["switched_to_host"] is False
    assert fw.onboard_mode == 2


def test_unknown_profile_format_falls_back_to_host():
    be, dev, i, f, fw = M.make_mock(profile_format=3)
    res = be.set_dpi(dev, i, 1600, 1600, 2)
    assert res["switched_to_host"] and not res["wrote_onboard"]
    assert fw.onboard_mode == 2 and fw.dpi[0] == 1600


def test_host_write_lost_on_sleep(m):
    be, dev, i, f, fw = m
    be.set_dpi(dev, i, 1600, 1600, 2, host_mode="host")
    assert fw.dpi[0] == 1600 and fw.onboard_mode == 2
    fw.sleep_and_wake("none")
    assert fw.dpi[0] == 800 and fw.onboard_mode == 1  # factory sector unchanged


def test_trace_hook(m):
    be, dev, i, f, fw = m
    lines = []
    be.TRACE = lines.append
    try:
        be.read_btn(dev, i, f, 0)
    finally:
        be.TRACE = None
    assert lines[0].startswith(">> 11 01 0c 2") and any(l.startswith("<< 11 01 0c 2") for l in lines)


def test_rate_read_uses_wireless_conn_byte(m):
    be, dev, i, f, fw = m
    fw.rate_idx = 6  # mouse really at 8000 Hz
    r = be.read_rate(dev, i)
    assert r["feature"] == 0x8061 and r["conn"] == 1 and r["hz"] == 8000
    assert r["supported"] == [125, 250, 500, 1000, 2000, 4000, 8000]
    assert (0x8061, 2, bytes([1, 0, 0, 0, 0, 0, 0, 0])) in fw.calls  # conn=1 sent (Solaar PR #3352)


def test_rate_set_and_host_switch(m):
    be, dev, i, f, fw = m
    with pytest.raises(be.HidppError, match="onboard-profile mode, which owns polling rate"):
        be.set_rate(dev, i, 8000, host_mode="never")
    assert fw.rate_idx == 3
    before = [c for c in fw.calls if c[0] == 0x8061 and c[1] == 3]
    res = be.set_rate(dev, i, 8000)
    assert res["hz"] == 8000 and res["wrote_onboard"] and not res["switched_to_host"]
    assert fw.rate_idx == 6 and fw.onboard_mode == 1
    assert be.parse_ext_profile(fw.sectors[1])["wireless_idx"] == 6
    assert [c for c in fw.calls if c[0] == 0x8061 and c[1] == 3] == before
    for bad in (400, 3000, 9000, 0):
        with pytest.raises(be.HidppError, match="not a rate"):
            be.set_rate(dev, i, bad)


def test_rate_snapping_and_cap():
    be = M.load_backend()
    sup = [125, 250, 500, 1000, 2000, 4000, 8000]
    assert be.snap_rate(sup, 3000) == (2000, 2000, 4000)  # tie -> lower
    assert be.snap_rate(sup, 3500) == (4000, 2000, 4000)
    assert be.snap_rate(sup, 400) == (500, 250, 500)
    assert be.snap_rate(sup, 99999) == (8000, 8000, 8000)  # capped at 8000
    assert be.snap_rate(sup, 8000) == (8000, 8000, 8000)
    assert be.snap_rate([125, 250, 500, 1000], 8000)[0] == 1000  # mouse without 8K


def test_rate_unsupported_on_link(m):
    be, dev, i, f, fw = m
    fw.rate_flags[1] = 0x0F  # link that only goes to 1000 Hz
    be.set_onboard_mode(dev, i, "host")
    assert be.read_rate(dev, i)["supported"] == [125, 250, 500, 1000]
    with pytest.raises(be.HidppError, match="not a rate"):
        be.set_rate(dev, i, 8000)


def test_rate_legacy_8060():
    be, dev, i, f, fw = M.make_mock(rate_feature=0x8060)
    r = be.read_rate(dev, i)
    assert r["feature"] == 0x8060 and r["supported"] == [125, 250, 500, 1000] and r["hz"] == 1000
    be.set_rate(dev, i, 250)
    assert fw.rate_ms == 4


def test_no_rate_feature():
    be, dev, i, f, fw = M.make_mock(rate_feature=None)
    assert be.read_rate(dev, i) is None


def test_rate_in_config(m, tmp_path):
    be, dev, i, f, fw = m
    be.set_rate(dev, i, 4000)
    path, cfg = be.save_config(dev, i, f, str(tmp_path / "c.conf"))
    assert cfg["polling_hz"] == 4000
    fw.rate_idx = 3; fw.onboard_mode = 1  # live values smashed; sector still has 4000 from set_rate
    # smash the sector back to factory so apply_config has work to do
    fw.sectors[1] = M.factory_sector()
    be.apply_config(dev, i, f, json.load(open(path)))
    assert fw.rate_idx == 5 and fw.onboard_mode == 1
    with pytest.raises(be.HidppError):
        be.apply_config(dev, i, f, {"polling_hz": 9000})


def test_cli_rate_validation():
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, os.path.join(root, "superstrike-hits.py"), "--rate", "9000"], capture_output=True, text=True)
    assert r.returncode != 0 and "1..8000" in r.stderr
