"""Simulated PRO X2 SUPERSTRIKE for testing superstrike-hits.py / superstrike-gui.py without hardware.

The fake firmware sits on the other end of a SOCK_SEQPACKET socketpair, so the *real*
Device.request() code (framing, select/read loop, swid matching, error frames) is exercised.
It enforces the same rules the real device does (per PROTOCOL.md and Solaar issue #3202):
values above the max or with the reserved low bits set -> ERR_INVALID_ARGUMENT (0x02).
"""
import importlib.util, os, socket, struct, threading

HERE = os.path.dirname(os.path.abspath(__file__))

def load_backend(path=None):
    path = path or os.environ.get("SUPERSTRIKE_BACKEND") or os.path.join(HERE, "superstrike-hits.py")
    spec = importlib.util.spec_from_file_location("superstrike_hits", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# the real feature table of the X2 (feature-table.txt), trimmed to the end
FEATURES = [0x0000, 0x0001, 0x0003, 0x0005, 0x1D4B, 0x0020, 0x1004, 0x2250, 0x2251, 0x2202, 0x8090, 0x80E0,
            0x1B0C, 0x8061, 0x8100, 0x8110]


class _Refuse(Exception):
    def __init__(self, code):
        self.code = code


class FakeFirmware:
    def __init__(self, sock, dev_idx=1, name=b"PRO X2 SUPERSTRIKE", dpi_feature=0x2202, rate_feature=0x8061):
        self.sock, self.idx, self.name = sock, dev_idx, name
        self.features = [f if f != 0x2202 else dpi_feature for f in FEATURES]
        if dpi_feature is None:
            self.features = [f for f in FEATURES if f != 0x2202]
        if rate_feature != 0x8061:
            self.features = [f if f != 0x8061 else rate_feature for f in self.features if rate_feature or f != 0x8061]
        # 0x8061 state: index 3 = 1000 Hz. X3: 8000 Hz wired + wireless (all 7 bits). wired_flags can be changed
        # to emulate an X2 (8000 wireless, 1000 wired).
        self.rate_idx = 3
        self.rate_flags = {0: 0x7F, 1: 0x7F}
        self.rate_ms = 1  # 0x8060 state
        self.rec = {0: [0x14, 0x09, 0x08], 1: [0x14, 0x09, 0x08], 2: [0x14, 0x09, 0x08]}  # act L5, x, hap L3
        self.dpi = [800, 800, 2]
        # like a real gaming mouse: starts in onboard-profile mode, where direct DPI writes are refused with
        # HID++ error 5 (LOGITECH_INTERNAL) - reproduces the PRO X3 report (feat 0x2202 fn 6 -> error 5)
        self.onboard_mode = 1
        self.calls = []
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        while True:
            try:
                data = self.sock.recv(64)
            except OSError:
                return
            if not data:
                return
            self.handle_frame(data)

    def send(self, b):
        self.sock.send(b.ljust(20, b"\0"))

    def handle_frame(self, d):
        try:
            return self._handle_frame(d)
        except _Refuse as r:
            rid, idx, fi, fnsw = d[0], d[1], d[2], d[3]
            return self.send(bytes([0x11, idx, 0xFF, fi, fnsw, r.code]))

    def _handle_frame(self, d):
        rid, idx, fi, fnsw, p = d[0], d[1], d[2], d[3], d[4:]
        if idx != self.idx:  # empty receiver slot -> HID++ 1.0 error 0x8F
            return self.send(bytes([0x10, idx, 0x8F, fi, fnsw, 0x09]))
        if fi >= len(self.features):
            return self.send(bytes([0x11, idx, 0xFF, fi, fnsw, 0x06]))
        fid, fn = self.features[fi], fnsw >> 4
        self.calls.append((fid, fn, bytes(p[:8])))
        try:
            body = self.handle(fid, fn, p)
        except ValueError:
            return self.send(bytes([0x11, idx, 0xFF, fi, fnsw, 0x02]))
        if body is None:
            return self.send(bytes([0x11, idx, 0xFF, fi, fnsw, 0x07]))
        self.send(bytes([0x11, idx, fi, fnsw]) + body)

    def handle(self, fid, fn, p):
        if fid == 0x0000:
            if fn == 0:
                want = (p[0] << 8) | p[1]
                return bytes([self.features.index(want) if want in self.features else 0, 0, 0])
            if fn == 1:
                return bytes([4, 2, p[2]])
        if fid == 0x0001:
            if fn == 0:
                return bytes([len(self.features) - 1])
            if fn == 1:
                return struct.pack(">HBB", self.features[p[0]], 0, 0)
        if fid == 0x0005:
            if fn == 0:
                return bytes([len(self.name)])
            if fn == 1:
                return self.name[p[0]:p[0] + 16]
        if fid == 0x1B0C:
            if fn == 0:
                return bytes([0x00, 0x03, 0x28, 0x14, 0x14, 0x01])
            if fn == 2:
                return bytes([p[0]] + self.rec[p[0]])
            if fn == 1:
                b, a, x, h = p[0], p[1], p[2], p[3]
                if b > 2 or a & 3 or not 4 <= a <= 0x28 or h & 3 or h > 0x14 or x & 2 or not 4 <= (x & 0xFC) <= 0x14:
                    raise ValueError
                self.rec[b] = [a, x, h]
                return bytes([b, a, x, h])
        if fid == 0x2202:
            lst = struct.pack(">HHHH", 100, 0xE000 | 50, 32000, 0)
            if fn == 1:
                return bytes([0, 0, 0x03])
            if fn == 2:
                return bytes(p[0:3]) + lst
            if fn == 5:
                x, y, lod = self.dpi
                return struct.pack(">BHHHHB", 0, x, 0, y, 0, lod)
            if fn == 6:
                if self.onboard_mode == 1:
                    raise _Refuse(5)
                _s, x, y, lod = struct.unpack(">BHHB", p[0:6])
                if x % 50 or not 100 <= x <= 32000 or y % 50 or not 100 <= y <= 32000 or lod > 2:
                    raise ValueError
                self.dpi = [x, y, lod]
                return b""
        if fid == 0x2201:
            if fn == 1:
                return bytes([0]) + struct.pack(">HHHH", 200, 400, 800, 1600) + b"\0\0"
            if fn == 2:
                return struct.pack(">BHH", 0, self.dpi[0], 800)
            if fn == 3:
                if self.onboard_mode == 1:
                    raise _Refuse(5)
                x = (p[1] << 8) | p[2]
                if x not in (200, 400, 800, 1600):
                    raise ValueError
                self.dpi[0] = x
                return b""
        if fid == 0x8061:
            if fn == 1:
                return struct.pack(">H", self.rate_flags.get(p[0], 0))
            if fn == 2:
                if p[0] != (0 if self.idx == 0xFF else 1):
                    return bytes([3])  # wrong link type -> answers for the other link (PR #3352 behaviour)
                return bytes([self.rate_idx])
            if fn == 3:
                if self.onboard_mode == 1:
                    raise _Refuse(5)
                if p[0] > 6 or not self.rate_flags[0 if self.idx == 0xFF else 1] & (1 << p[0]):
                    raise ValueError
                self.rate_idx = p[0]
                return b""
        if fid == 0x8060:
            if fn == 0:
                return bytes([0x8B])  # 1, 2, 4, 8 ms
            if fn == 1:
                return bytes([self.rate_ms])
            if fn == 2:
                if self.onboard_mode == 1:
                    raise _Refuse(5)
                if p[0] not in (1, 2, 4, 8):
                    raise ValueError
                self.rate_ms = p[0]
                return b""
        if fid == 0x8100:
            if fn == 1:
                if p[0] not in (1, 2):
                    raise ValueError
                self.onboard_mode = p[0]
                return b""
            if fn == 2:
                return bytes([self.onboard_mode])
        return None


def make_mock(backend=None, dev_idx=1, dpi_feature=0x2202, rate_feature=0x8061):
    """-> (backend_module, device, dev_idx, analog_feature_index, firmware)."""
    be = backend or load_backend()
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    fw = FakeFirmware(b, dev_idx, dpi_feature=dpi_feature, rate_feature=rate_feature)

    class MockDevice(be.Device):
        def __init__(self):
            self.path = "mock://superstrike"
            self.fd = a.fileno()
            self._sock = a

        def close(self):
            pass

    dev = MockDevice()
    fidx = dev.get_feature_index(dev_idx, be.ANALOG_FEATURE_ID)
    return be, dev, dev_idx, fidx, fw
