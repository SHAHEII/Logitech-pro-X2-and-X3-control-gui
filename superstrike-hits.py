#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""superstrike-hits (hardened) — Logitech G PRO X2 SUPERSTRIKE click-haptics &
actuation control on Linux, no G HUB.

Based on the reverse-engineered protocol (HID++ feature 0x1B0C); see PROTOCOL.md.
Hardened vs the original:
  * auto-locates the receiver's HID++ control interface (FF00 usage page) and
    skips other hidraw nodes that reject writes (no more BrokenPipe crash);
    --device is no longer needed.
  * config persistence: --save / --apply  (+ ~/.config/superstrike-hits.conf)
    so a udev/systemd hook can re-apply settings on every connect.
"""
import os, sys, glob, time, select, argparse, json

VID, PID_RECEIVER = 0x046D, 0xC54D
HIDPP_SHORT, HIDPP_LONG = 0x10, 0x11
SHORT_LEN, LONG_LEN = 7, 20
SWID = 0x05
ANALOG_FEATURE_ID = 0x1B0C
FEAT_IROOT, FEAT_FEATURESET = 0x00, 0x01
CONF = os.path.expanduser("~/.config/superstrike-hits.conf")

def act_level_to_raw(l): return l * 4
def act_raw_to_level(b): return b // 4
def hap_level_to_raw(l): return (l - 1) * 4
def hap_raw_to_level(b): return b // 4 + 1
ACT_MIN, ACT_MAX = 1, 10
HAP_MIN, HAP_MAX = 1, 6
# Rapid trigger lives in the "x" byte: bits 7..2 = level 1..5, bit 0 = firmware flag that must be
# preserved (observed x=0x09 -> level 2 + flag). Encoding from Solaar (PR #3132 + fix #3207),
# tested there on real PRO X2 SUPERSTRIKE hardware. Level 0 is rejected by the firmware.
def rt_level_to_raw(l, cur_x): return ((l & 0x3F) << 2) | (cur_x & 0x01)
def rt_raw_to_level(b): return b >> 2
RT_MIN, RT_MAX = 1, 5
DPI_EXT_FEATURE_ID, DPI_FEATURE_ID, FEATURESET_ID = 0x2202, 0x2201, 0x0001

class HidppError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code  # HID++ error code from the device, if any

# --trace / GUI debug toggle: set to a callable(str) to see every raw HID++ frame
TRACE = None

def _trace(direction, data):
    if TRACE:
        TRACE(f"{direction} {bytes(data).hex(' ')}")

HIDPP_ERRORS = {1: "UNKNOWN", 2: "INVALID_ARGUMENT", 3: "OUT_OF_RANGE", 4: "HW_ERROR", 5: "LOGITECH_INTERNAL",
                6: "INVALID_FEATURE_INDEX", 7: "INVALID_FUNCTION_ID", 8: "BUSY (is G HUB/Solaar using it?)",
                9: "UNSUPPORTED"}

def sysfs_hid_id(name):
    try:
        with open(f"/sys/class/hidraw/{name}/device/uevent") as f:
            for line in f:
                if line.startswith("HID_ID="):
                    bus, vid, pid = line.strip().split("=")[1].split(":")
                    return int(bus, 16), int(vid, 16), int(pid, 16)
    except OSError:
        pass
    return None

def has_ff00(name):
    """True if this hidraw's report descriptor declares vendor usage page 0xFF00 (HID++)."""
    try:
        with open(f"/sys/class/hidraw/{name}/device/report_descriptor", "rb") as f:
            rd = f.read()
        return b"\x06\x00\xff" in rd
    except OSError:
        return False

class Device:
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    def close(self):
        try: os.close(self.fd)
        except OSError: pass
    def _drain(self):
        while True:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r: break
            try: os.read(self.fd, 64)
            except (BlockingIOError, OSError): break
    def request(self, dev_idx, feat_idx, fn, params=b"", long=True,
                want_byte0=None, timeout=0.6, tries=4):
        rid = HIDPP_LONG if long else HIDPP_SHORT
        n = LONG_LEN if long else SHORT_LEN
        pkt = bytes([rid, dev_idx, feat_idx, (fn << 4) | SWID]) + bytes(params)
        pkt = pkt + b"\x00" * (n - len(pkt))
        last_err = None
        for _ in range(tries):
            self._drain()
            try:
                _trace(">>", pkt)
                os.write(self.fd, pkt)
            except OSError as e:
                # wrong interface (e.g. BrokenPipe): treat as no-such-device here
                raise HidppError(f"write failed on {self.path}: {e}")
            end = time.time() + timeout
            while time.time() < end:
                r, _, _ = select.select([self.fd], [], [], max(0, end - time.time()))
                if not r: continue
                try: data = os.read(self.fd, 64)
                except (BlockingIOError, OSError): continue
                _trace("<<", data)
                if len(data) < 6 or data[1] != dev_idx: continue
                # error frames: [rid][dev][0xFF][feat_idx][fn|swid][code]  (HID++ 2.0)
                #               [rid][dev][0x8F][feat_idx][fn|swid][code]  (HID++ 1.0, e.g. empty receiver slot)
                # check these BEFORE the swid test below: in an error frame byte 3 is the
                # feature index, not fn|swid, so the old order silently dropped errors and
                # every failed call waited out all retries (~2.4 s).
                if data[2] in (0xFF, 0x8F) and data[3] == feat_idx and data[4] == ((fn << 4) | SWID):
                    raise HidppError(f"hid++ error {data[5]} ({HIDPP_ERRORS.get(data[5], '?')}) "
                                     f"feat=0x{feat_idx:02x} fn={fn}", code=data[5])
                if (data[3] & 0x0F) != SWID: continue
                if data[2] == feat_idx and (data[3] >> 4) == fn:
                    body = data[4:]
                    if want_byte0 is None or (body and body[0] == want_byte0):
                        return body
            time.sleep(0.04)
        raise HidppError(f"no/err response feat=0x{feat_idx:02x} fn={fn} "
                         f"(last hid++ error={last_err})")
    def ping(self, dev_idx):
        body = self.request(dev_idx, FEAT_IROOT, 1, bytes([0, 0, 0x5A]))
        return body[0], body[1]
    def get_feature_index(self, dev_idx, fid):
        body = self.request(dev_idx, FEAT_IROOT, 0, bytes([fid >> 8, fid & 0xFF]))
        return body[0]

def find_device(explicit=None):
    if explicit:
        cands = [explicit]
    else:
        # HID++ interface first: VID 046d AND FF00 usage page
        all_hr = sorted(glob.glob("/dev/hidraw*"))
        pref = [p for p in all_hr if (sysfs_hid_id(os.path.basename(p)) or (0, 0, 0))[1] == VID
                and has_ff00(os.path.basename(p))]
        rest = [p for p in all_hr if p not in pref]
        cands = pref + rest
    for path in cands:
        name = os.path.basename(path)
        if not explicit:
            hid = sysfs_hid_id(name)
            if not hid or hid[1] != VID:
                continue
        try:
            dev = Device(path)
        except OSError:
            continue
        for dev_idx in (1, 2, 3, 4, 5, 6, 0xFF):
            try:
                dev.ping(dev_idx)
                fidx = dev.get_feature_index(dev_idx, ANALOG_FEATURE_ID)
            except HidppError:
                continue
            if fidx:
                return dev, dev_idx, fidx
        dev.close()
    raise SystemExit("ERROR: no SUPERSTRIKE HID++ (0x1B0C) device found. "
                     "Is the mouse powered on and connected? Do you have root / the udev rule installed?")

def read_btn(dev, dev_idx, fidx, btn):
    rec = dev.request(dev_idx, fidx, 2, bytes([btn]), want_byte0=btn)
    return bytearray(rec[:4])  # [btn, act, x, hap]

def set_button(dev, dev_idx, fidx, btn, act_raw=None, hap_raw=None, rt_level=None):
    rec = read_btn(dev, dev_idx, fidx, btn)
    if act_raw is not None: rec[1] = act_raw
    if hap_raw is not None: rec[3] = hap_raw
    if rt_level is not None:
        if not RT_MIN <= rt_level <= RT_MAX:
            raise HidppError(f"rapid trigger must be {RT_MIN}..{RT_MAX}")
        rec[2] = rt_level_to_raw(rt_level, rec[2])
    res = dev.request(dev_idx, fidx, 1, bytes(rec), want_byte0=btn)
    return res[:4]

def cmd_get(dev, dev_idx, fidx):
    for btn, label in ((0, "left "), (1, "right")):
        b = read_btn(dev, dev_idx, fidx, btn)
        print(f"  {label}: actuation L{act_raw_to_level(b[1])} (0x{b[1]:02x})  "
              f"click-haptics L{hap_raw_to_level(b[3])} (0x{b[3]:02x})  "
              f"rapid-trigger L{rt_raw_to_level(b[2])} [x=0x{b[2]:02x}]")

def get_info(dev, dev_idx, fidx):
    """0x1B0C fn0 getInfo -> raw bytes [00, nButtons, act_max, ?, ?, flags] (see PROTOCOL.md)."""
    return bytes(dev.request(dev_idx, fidx, 0)[:6])

def check_raw(name, val, maximum):
    """Raw bytes must be a multiple of 4 (low 2 bits are reserved) and within the device max."""
    if not 0 <= val <= maximum or val & 0x03:
        raise HidppError(f"raw {name} 0x{val:02x} invalid: must be a multiple of 4 between 0x00 and 0x{maximum:02x}")

# ---------------------------------------------------------------- feature table (read-only)
FEATURE_NAMES = {0x0000: "IRoot", 0x0001: "IFeatureSet", 0x0003: "device info / fw", 0x0005: "device name & type",
                 0x1D4B: "wireless device status", 0x0020: "config change", 0x1004: "unified battery",
                 0x2201: "adjustable DPI", 0x2202: "extended adjustable DPI", 0x1B0C: "analog / HITS",
                 0x8060: "report rate", 0x8061: "extended report rate", 0x8100: "onboard profiles",
                 0x8110: "mouse button spy", 0x1805: "OOB state", 0x1830: "power modes",
                 0x1E00: "enable hidden features"}

def dump_features(dev, dev_idx):
    """[(index, feature_id, type_flags, version)] via IFeatureSet. Only getCount/getFeatureID are called."""
    fs = dev.get_feature_index(dev_idx, FEATURESET_ID)
    count = dev.request(dev_idx, fs, 0)[0]
    out = [(0, 0x0000, 0, 0)]
    for i in range(1, count + 1):
        b = dev.request(dev_idx, fs, 1, bytes([i]))
        out.append((i, (b[0] << 8) | b[1], b[2], b[3]))
    return out

def cmd_dump_features(dev, dev_idx):
    for i, fid, flags, ver in dump_features(dev, dev_idx):
        tag = "  <<<<< analog / HITS" if fid == ANALOG_FEATURE_ID else ""
        hid = " [hidden]" if flags & 0x40 else ""
        print(f"  index 0x{i:02x} -> ID 0x{fid:04x}  v{ver}  {FEATURE_NAMES.get(fid, '')}{hid}{tag}")

# ---------------------------------------------------------------- DPI (HID++ 0x2202, fallback 0x2201)
# Wire formats from Solaar's settings_templates.py (ExtendedAdjustableDpi / AdjustableDpi / produce_dpi_list).
LOD_NAMES = ("LOW", "MEDIUM", "HIGH")

def find_dpi_feature(dev, dev_idx):
    """-> (feature_id, feature_index) or (None, 0)."""
    for fid in (DPI_EXT_FEATURE_ID, DPI_FEATURE_ID):
        try:
            idx = dev.get_feature_index(dev_idx, fid)
        except HidppError:
            idx = 0
        if idx:
            return fid, idx
    return None, 0

def _dpi_list(dev, dev_idx, didx, fn, skip, direction):
    data = b""
    for i in range(0x40):
        data += bytes(dev.request(dev_idx, didx, fn, bytes([0, direction, i]))[skip:])
        if data[-2:] == b"\x00\x00":
            break
    out, i = [], 0
    while i + 1 < len(data):
        v = (data[i] << 8) | data[i + 1]
        if v == 0:
            break
        if v >> 13 == 0b111:  # range item: step, then last value
            step, last = v & 0x1FFF, (data[i + 2] << 8) | data[i + 3]
            if not out or step == 0 or last <= out[-1]:
                raise HidppError("invalid DPI list from device")
            out += list(range(out[-1] + step, last + 1, step))
            i += 4
        else:
            out.append(v)
            i += 2
    return out

def read_dpi(dev, dev_idx):
    """-> dict(feature, x, y, lod, x_list, y_list, has_y, has_lod) or None if no DPI feature."""
    fid, didx = find_dpi_feature(dev, dev_idx)
    if not didx:
        return None
    if fid == DPI_EXT_FEATURE_ID:
        caps = dev.request(dev_idx, didx, 1, bytes([0]))
        has_y, has_lod = bool(caps[2] & 1), bool(caps[2] & 2)
        xs = _dpi_list(dev, dev_idx, didx, 2, 3, 0)
        ys = _dpi_list(dev, dev_idx, didx, 2, 3, 1) if has_y else None
        r = dev.request(dev_idx, didx, 5, bytes([0]))
        x = (r[1] << 8) | r[2]
        y = ((r[5] << 8) | r[6]) if has_y else None
        lod = r[9] if has_lod else None
    else:  # 0x2201: one value for both axes
        has_y = has_lod = False
        xs, ys, y, lod = _dpi_list(dev, dev_idx, didx, 1, 1, 0), None, None, None
        r = dev.request(dev_idx, didx, 2, bytes([0]))
        x = (r[1] << 8) | r[2] or (r[3] << 8) | r[4]  # 0 -> use default dpi
    return dict(feature=fid, x=x, y=y, lod=lod, x_list=xs, y_list=ys, has_y=has_y, has_lod=has_lod)

def nearest_dpi(choices, want):
    return min(choices, key=lambda v: abs(v - want))

# ---------------------------------------------------------------- onboard profiles mode (HID++ 0x8100)
# Gaming mice run from an onboard profile by default ("onboard mode"). In that mode the profile owns
# DPI / report rate, and a direct 0x2202 setSensorDpi is refused (seen on a real PRO X3 SUPERSTRIKE:
# HID++ error 5). Solaar's docs say the same ("may need Onboard Profiles set to Disable"), and
# libratbag never sends 0x2202 set while 0x8100 is present (it edits the profile instead).
# Mode codes from Solaar's OnboardProfiles setting: fn1 setMode(1=onboard, 2=host), fn2 getMode.
ONBOARD_FEATURE_ID = 0x8100
MODE_ONBOARD, MODE_HOST = 0x01, 0x02

def get_onboard_mode(dev, dev_idx):
    """-> 'onboard', 'host', or None if the device has no 0x8100."""
    try:
        oidx = dev.get_feature_index(dev_idx, ONBOARD_FEATURE_ID)
    except HidppError:
        return None
    if not oidx:
        return None
    m = dev.request(dev_idx, oidx, 2)[0]
    return "onboard" if m == MODE_ONBOARD else "host" if m == MODE_HOST else f"unknown({m})"

def set_onboard_mode(dev, dev_idx, mode):
    """mode: 'host' (software controls DPI; not kept across power cycles) or 'onboard' (profile controls DPI)."""
    oidx = dev.get_feature_index(dev_idx, ONBOARD_FEATURE_ID)
    if not oidx:
        raise HidppError("this device has no onboard profiles feature (0x8100)")
    dev.request(dev_idx, oidx, 1, bytes([MODE_HOST if mode == "host" else MODE_ONBOARD]))
    return get_onboard_mode(dev, dev_idx)

def set_dpi(dev, dev_idx, x, y=None, lod=None, host_mode="auto"):
    """Range-checked: x/y must be exact values from the mouse's own list (use nearest_dpi to snap)."""
    info = read_dpi(dev, dev_idx)
    if info is None:
        raise HidppError("this device has no DPI feature (0x2202 / 0x2201)")
    fid, didx = find_dpi_feature(dev, dev_idx)
    if x not in info["x_list"]:
        raise HidppError(f"DPI {x} not allowed (range {info['x_list'][0]}..{info['x_list'][-1]}, "
                         f"nearest {nearest_dpi(info['x_list'], x)})")
    if fid == DPI_FEATURE_ID:
        switched = _ensure_host_mode(dev, dev_idx, host_mode)
        _dpi_write(dev, dev_idx, didx, 3, bytes([0, x >> 8, x & 0xFF]))
        out = read_dpi(dev, dev_idx)
        out["switched_to_host"] = switched
        return out
    y = x if y is None else y
    if info["has_y"] and y not in info["y_list"]:
        raise HidppError(f"Y DPI {y} not allowed")
    lod = info["lod"] if lod is None else lod
    if info["has_lod"] and lod not in (0, 1, 2):
        raise HidppError("LOD must be 0 (LOW), 1 (MEDIUM) or 2 (HIGH)")
    p = bytes([0, x >> 8, x & 0xFF])
    p += bytes([y >> 8, y & 0xFF]) if info["has_y"] else b"\x00\x00"
    p += bytes([lod if info["has_lod"] else 0])
    switched = _ensure_host_mode(dev, dev_idx, host_mode)
    _dpi_write(dev, dev_idx, didx, 6, p)
    out = read_dpi(dev, dev_idx)
    out["switched_to_host"] = switched
    return out

def _ensure_host_mode(dev, dev_idx, host_mode):
    """host_mode='auto': leave onboard mode first if needed (returns True if it switched). 'never': don't."""
    if host_mode != "auto":
        return False
    if get_onboard_mode(dev, dev_idx) == "onboard":
        set_onboard_mode(dev, dev_idx, "host")
        return True
    return False

def _dpi_write(dev, dev_idx, didx, fn, p, what="DPI"):
    try:
        dev.request(dev_idx, didx, fn, p)
    except HidppError as e:
        if e.code == 5 and get_onboard_mode(dev, dev_idx) == "onboard":
            raise HidppError(f"{e} - the mouse is in onboard-profile mode, which owns {what}. "
                             "Switch to host mode (GUI button, or --onboard host) and retry.", code=5)
        raise

def fmt_dpi(info):
    if info is None:
        return "  DPI: not available (no 0x2202/0x2201 feature)"
    s = f"  DPI: X {info['x']}"
    if info["has_y"]: s += f"  Y {info['y']}"
    if info["has_lod"] and info["lod"] is not None: s += f"  lift-off {LOD_NAMES[info['lod']]}"
    return s + f"   (allowed {info['x_list'][0]}..{info['x_list'][-1]}, {len(info['x_list'])} values, feature 0x{info['feature']:04x})"

# ---------------------------------------------------------------- polling / report rate (0x8061, fallback 0x8060)
# 0x8061 (extended adjustable report rate) - wire format agreed by Solaar (ExtendedReportRate) and libratbag
# (PR #1896, X2 support):  fn1 = supported-rate bitmask (BE16, bit i -> RATE_EXT_HZ[i]),
# fn2(conn) = current rate index, fn3(index) = set.  conn: 0 = wired, 1 = LIGHTSPEED wireless.  Solaar
# PR #3352 found that on the PRO X3 dongle the read MUST carry conn=1, otherwise the mouse answers for the
# wired link (showed 1000 Hz while really at 8000 Hz).  We send conn for the list query too (libratbag does).
# 0x8060 (older report rate, Solaar ReportRate): fn0 = bitmask (bit i -> (i+1) ms), fn1 = get ms, fn2 = set ms.
# Only the discrete rates the mouse reports can be set - there is no "custom Hz" in HID++.
RATE_EXT_FEATURE_ID, RATE_FEATURE_ID = 0x8061, 0x8060
RATE_EXT_HZ = [125, 250, 500, 1000, 2000, 4000, 8000]
RATE_MAX_HZ = 8000

def find_rate_feature(dev, dev_idx):
    for fid in (RATE_EXT_FEATURE_ID, RATE_FEATURE_ID):
        try:
            idx = dev.get_feature_index(dev_idx, fid)
        except HidppError:
            idx = 0
        if idx:
            return fid, idx
    return None, 0

def rate_connection(dev_idx):
    """0 = wired/direct (device index 0xFF), 1 = wireless through a receiver/dongle."""
    return 0 if dev_idx == 0xFF else 1

def read_rate(dev, dev_idx):
    """-> dict(feature, hz, supported=[Hz...], conn) or None if the mouse has no report-rate feature."""
    fid, ridx = find_rate_feature(dev, dev_idx)
    if not ridx:
        return None
    conn = rate_connection(dev_idx)
    if fid == RATE_EXT_FEATURE_ID:
        r = dev.request(dev_idx, ridx, 1, bytes([conn]))
        flags = (r[0] << 8) | r[1]
        supported = [hz for i, hz in enumerate(RATE_EXT_HZ) if flags & (1 << i)]
        cur = dev.request(dev_idx, ridx, 2, bytes([conn]))[0]
        hz = RATE_EXT_HZ[cur] if cur < len(RATE_EXT_HZ) else None
    else:
        flags = dev.request(dev_idx, ridx, 0)[0]
        supported = sorted({1000 // (i + 1) for i in range(8) if flags & (1 << i)})
        ms = dev.request(dev_idx, ridx, 1)[0]
        hz = 1000 // ms if ms else None
    return dict(feature=fid, hz=hz, supported=supported, conn=conn)

def snap_rate(supported, want):
    """Nearest supported rate to a custom number (capped at 8000). Ties go to the lower rate.
    -> (snapped, lower_neighbour, upper_neighbour)."""
    if not supported:
        raise HidppError("the mouse reported no supported polling rates")
    want = max(1, min(int(want), RATE_MAX_HZ))
    lower = max([r for r in supported if r <= want], default=None)
    upper = min([r for r in supported if r >= want], default=None)
    snapped = min(supported, key=lambda r: (abs(r - want), r))
    return snapped, lower, upper

def set_rate(dev, dev_idx, hz, host_mode="auto"):
    """Set an exact supported rate in Hz (use snap_rate for custom numbers). Never sends unsupported values."""
    info = read_rate(dev, dev_idx)
    if info is None:
        raise HidppError("this device has no report-rate feature (0x8061 / 0x8060)")
    if hz > RATE_MAX_HZ or hz not in info["supported"]:
        raise HidppError(f"{hz} Hz is not a rate this mouse supports (supported: {info['supported']})")
    fid, ridx = find_rate_feature(dev, dev_idx)
    switched = _ensure_host_mode(dev, dev_idx, host_mode)
    if fid == RATE_EXT_FEATURE_ID:
        _dpi_write(dev, dev_idx, ridx, 3, bytes([RATE_EXT_HZ.index(hz)]), what="polling rate")
    else:
        _dpi_write(dev, dev_idx, ridx, 2, bytes([1000 // hz]), what="polling rate")
    out = read_rate(dev, dev_idx)
    out["switched_to_host"] = switched
    if out["hz"] != hz:
        mode = get_onboard_mode(dev, dev_idx)
        raise HidppError(f"mouse accepted the command but still reports {out['hz']} Hz"
                         + (" - it is in onboard-profile mode, switch to host mode" if mode == "onboard" else ""))
    return out

def fmt_rate(info):
    if info is None:
        return "  Polling rate: not available (no 0x8061/0x8060 feature)"
    link = "wired" if info["conn"] == 0 else "wireless"
    return (f"  Polling rate: {info['hz']} Hz ({link})   supported: "
            + ", ".join(map(str, info["supported"])) + f"   (feature 0x{info['feature']:04x})")

# ---------------------------------------------------------------- persistence (--save / --apply)
def read_config(dev, dev_idx, fidx):
    cfg = {}
    for btn in (0, 1):
        b = read_btn(dev, dev_idx, fidx, btn)
        cfg[("left", "right")[btn]] = {"actuation": act_raw_to_level(b[1]), "haptics": hap_raw_to_level(b[3]),
                                       "rapid_trigger": rt_raw_to_level(b[2])}
    try:
        d = read_dpi(dev, dev_idx)
    except HidppError:
        d = None
    if d:
        cfg["dpi"] = {"x": d["x"], "y": d["y"], "lod": d["lod"]}
    try:
        r = read_rate(dev, dev_idx)
    except HidppError:
        r = None
    if r and r["hz"]:
        cfg["polling_hz"] = r["hz"]
    return cfg

def save_config(dev, dev_idx, fidx, path=None):
    cfg = read_config(dev, dev_idx, fidx)
    path = path or CONF
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    return path, cfg

def apply_config(dev, dev_idx, fidx, cfg):
    """Apply a config dict (keys are optional; old files without rapid_trigger/dpi still work)."""
    for btn in (0, 1):
        c = cfg.get(("left", "right")[btn], {})
        if "actuation" in c and not ACT_MIN <= c["actuation"] <= ACT_MAX:
            raise HidppError("actuation in config must be 1..10")
        if "haptics" in c and not HAP_MIN <= c["haptics"] <= HAP_MAX:
            raise HidppError("haptics in config must be 1..6")
        set_button(dev, dev_idx, fidx, btn,
                   act_raw=act_level_to_raw(c["actuation"]) if "actuation" in c else None,
                   hap_raw=hap_level_to_raw(c["haptics"]) if "haptics" in c else None,
                   rt_level=c.get("rapid_trigger"))
    if cfg.get("dpi"):
        d = cfg["dpi"]
        set_dpi(dev, dev_idx, d["x"], d.get("y"), d.get("lod"))
    if cfg.get("polling_hz"):
        hz = cfg["polling_hz"]
        if not isinstance(hz, int) or not 1 <= hz <= RATE_MAX_HZ:
            raise HidppError("polling_hz in config must be 1..8000")
        set_rate(dev, dev_idx, hz)

def main():
    ap = argparse.ArgumentParser(description="G PRO X2 SUPERSTRIKE click-haptics / actuation control (Linux)")
    ap.add_argument("--get", action="store_true", help="read current per-button config (+ DPI)")
    ap.add_argument("--haptics", type=int, metavar="1-6", help="set click-haptics level (1 = off)")
    ap.add_argument("--actuation", type=int, metavar="1-10", help="set actuation-point level")
    ap.add_argument("--rapid-trigger", type=int, metavar="1-5", help="set rapid-trigger level (1 = fastest reset)")
    ap.add_argument("--raw-haptics", type=lambda v: int(v, 0), metavar="0xNN", help="set raw haptics byte (advanced)")
    ap.add_argument("--raw-actuation", type=lambda v: int(v, 0), metavar="0xNN", help="set raw actuation byte (advanced)")
    ap.add_argument("--left", action="store_true", help="apply to the left main button only")
    ap.add_argument("--right", action="store_true", help="apply to the right main button only")
    ap.add_argument("--dpi", type=int, metavar="N", help="set DPI (snapped to the nearest value the mouse allows)")
    ap.add_argument("--dpi-y", type=int, metavar="N", help="separate Y-axis DPI (if the mouse supports it)")
    ap.add_argument("--lod", choices=["low", "medium", "high"], help="lift-off distance (with 0x2202)")
    ap.add_argument("--rate", type=int, metavar="HZ",
                    help="set polling rate (max 8000); other numbers snap to the nearest rate the mouse supports")
    ap.add_argument("--rate-list", action="store_true", help="show current + supported polling rates")
    ap.add_argument("--dpi-list", action="store_true", help="print every DPI value the mouse accepts")
    ap.add_argument("--onboard", choices=["status", "host", "onboard"],
                    help="show / switch onboard-profile mode (host = software controls DPI until power-off)")
    ap.add_argument("--no-host-switch", action="store_true",
                    help="with --dpi: do NOT leave onboard-profile mode automatically")
    ap.add_argument("--trace", action="store_true", help="print every raw HID++ frame (for bug reports)")
    ap.add_argument("--dump-features", action="store_true", help="print the whole HID++ feature table (self-check)")
    ap.add_argument("--device", help="explicit /dev/hidrawN (normally auto-detected)")
    ap.add_argument("--save", action="store_true", help="also remember the resulting config in ~/.config/superstrike-hits.conf")
    ap.add_argument("--apply", action="store_true", help="read the saved config and apply it (for a boot/connect hook)")
    args = ap.parse_args()

    # validate before touching the device
    if args.actuation is not None and not ACT_MIN <= args.actuation <= ACT_MAX:
        raise SystemExit("actuation must be 1..10")
    if args.haptics is not None and not HAP_MIN <= args.haptics <= HAP_MAX:
        raise SystemExit("haptics must be 1..6")
    if args.rapid_trigger is not None and not RT_MIN <= args.rapid_trigger <= RT_MAX:
        raise SystemExit("rapid trigger must be 1..5")
    if args.dpi is not None and not 1 <= args.dpi <= 0xFFFF:
        raise SystemExit("dpi out of range")
    if args.rate is not None and not 1 <= args.rate <= RATE_MAX_HZ:
        raise SystemExit("polling rate must be 1..8000 Hz")

    global TRACE
    if args.trace:
        TRACE = lambda line: print("  " + line, file=sys.stderr)
    dev, dev_idx, fidx = find_device(args.device)
    try:
        if args.onboard:
            if args.onboard != "status":
                set_onboard_mode(dev, dev_idx, args.onboard)
            print("onboard-profile mode:", get_onboard_mode(dev, dev_idx) or "not supported")
            if not (args.dpi or args.lod or args.actuation or args.haptics or args.rapid_trigger):
                return
        if args.apply:
            if not os.path.exists(CONF):
                print("no config file, skipping"); return
            cfg = json.load(open(CONF))
            apply_config(dev, dev_idx, fidx, cfg)
            print("applied config:", cfg); return

        if args.dump_features:
            cmd_dump_features(dev, dev_idx); return
        if args.rate_list:
            print(fmt_rate(read_rate(dev, dev_idx))); return
        if args.dpi_list:
            d = read_dpi(dev, dev_idx)
            print(fmt_dpi(d))
            if d: print("  " + " ".join(map(str, d["x_list"])))
            return

        act_raw = act_level_to_raw(args.actuation) if args.actuation is not None else None
        hap_raw = hap_level_to_raw(args.haptics) if args.haptics is not None else None
        if args.raw_actuation is not None or args.raw_haptics is not None:
            info = get_info(dev, dev_idx, fidx)
            if args.raw_actuation is not None:
                check_raw("actuation", args.raw_actuation, info[2]); act_raw = args.raw_actuation
            if args.raw_haptics is not None:
                check_raw("haptics", args.raw_haptics, 0x14); hap_raw = args.raw_haptics
        rt = args.rapid_trigger
        changed = False

        if act_raw is not None or hap_raw is not None or rt is not None:
            buttons = [0, 1] if not (args.left or args.right) else ([0] if args.left else []) + ([1] if args.right else [])
            for btn in buttons:
                out = set_button(dev, dev_idx, fidx, btn, act_raw, hap_raw, rt)
                print(f"  set {('left ', 'right')[btn]}: actuation L{act_raw_to_level(out[1])}  "
                      f"click-haptics L{hap_raw_to_level(out[3])}  rapid-trigger L{rt_raw_to_level(out[2])}  OK")
            changed = True

        if args.dpi is not None or args.lod:
            cur = read_dpi(dev, dev_idx)
            if cur is None:
                raise SystemExit("this device has no DPI feature")
            x = nearest_dpi(cur["x_list"], args.dpi) if args.dpi is not None else cur["x"]
            y = None
            if cur["has_y"]:
                y = nearest_dpi(cur["y_list"], args.dpi_y) if args.dpi_y is not None else x
            lod = ("low", "medium", "high").index(args.lod) if args.lod else None
            res = set_dpi(dev, dev_idx, x, y, lod, host_mode="never" if args.no_host_switch else "auto")
            if res.get("switched_to_host"):
                print("  note: switched the mouse from onboard-profile mode to host mode so DPI can be set.\n"
                      "        Host mode is lost when the mouse powers off; use --save + auto-apply to restore it.")
            print(fmt_dpi(res).replace("DPI:", "set DPI:"))
            changed = True

        if args.rate is not None:
            cur = read_rate(dev, dev_idx)
            if cur is None:
                raise SystemExit("this device has no polling-rate feature")
            hz, lo, hi = snap_rate(cur["supported"], args.rate)
            if hz != args.rate:
                print(f"  note: {args.rate} Hz is not a hardware rate; snapped to {hz} Hz "
                      f"(nearest supported: {lo or '-'} / {hi or '-'})")
            res = set_rate(dev, dev_idx, hz, host_mode="never" if args.no_host_switch else "auto")
            if res.get("switched_to_host"):
                print("  note: switched the mouse from onboard-profile mode to host mode so the rate can be set.\n"
                      "        Host mode is lost when the mouse powers off; use --save + auto-apply to restore it.")
            print(fmt_rate(res).replace("Polling rate:", "set polling rate:"))
            changed = True

        if not changed and not args.save:
            cmd_get(dev, dev_idx, fidx)
            print(fmt_dpi(read_dpi(dev, dev_idx)))
            print(fmt_rate(read_rate(dev, dev_idx)))
            print("  onboard-profile mode:", get_onboard_mode(dev, dev_idx) or "not supported")
            return

        if args.save:
            path, cfg = save_config(dev, dev_idx, fidx)
            print("saved config ->", path, cfg)
    except HidppError as e:
        raise SystemExit(f"ERROR: {e}")
    finally:
        dev.close()

if __name__ == "__main__":
    main()
