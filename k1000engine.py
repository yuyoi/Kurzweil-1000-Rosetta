#!/usr/bin/env python3
"""Kurzweil 1000 GX sample engine: WAVs in, burnable chip images out.

Each sample becomes a NEW program (IDs 160-192) with its own keymap and
soundfile, appended to the SETUP. Factory objects are left byte-identical.
Wave data goes into the two banks the GX leaves empty:
  bank 2 = 0xC00000: X U60, Y U52, Z U56
  bank 3 = 0xE00000: X U61, Y U53, Z U57
"""
import math
import os
import struct
import wave
from dataclasses import dataclass

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DUMPS = os.path.join(HERE, "dumps")
SETUP_NAMES = ("gx_setup_hi_u22_v25_read1.bin", "gx_setup_lo_u33_v25_read1.bin")   # your own U22/U33 dumps

RATE = 30200                # base rate from the factory sine soundfile
ROM = 524288                # bytes per wave chip
BANK_SAMPLES = ROM * 2      # 12-bit samples per bank
BANKS = {2: (0xC00000, "U60_X2", "U52_Y2", "U56_Z2"),
         3: (0xE00000, "U61_X3", "U53_Y3", "U57_Z3")}
TAIL = 64                   # silent looped samples so a one-shot ends quietly

BLOCK_END = 0x16364         # end of the factory GX SETUP object block
FIRST_PROGRAM = 160
MAX_SLOTS = 33              # program IDs 160-192 are mapped by the Presets table
FIRST_KEYMAP = 168
FIRST_SOUNDFILE = 55
TPL_PROGRAM = 0x509F        # Tuning Note: one layer, flat sustain
TPL_KEYMAP = 0x4679         # Sine Wave: 100 cents per key, keys 12-126
TPL_SOUNDFILE = 0x4504      # Noises: flat natural envelope, one entry
NOTE_NAMES = "C C# D D# E F F# G G# A A# B".split()


def note_name(n):
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


# ---- audio ----------------------------------------------------------------
def read_wav(path):
    with wave.open(path, "rb") as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if sw == 1:
        a = (np.frombuffer(raw, np.uint8).astype(float) - 128) / 128
    elif sw == 2:
        a = np.frombuffer(raw, "<i2").astype(float) / 32768
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        a = ((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) << 8 >> 8) / 8388608
    elif sw == 4:
        a = np.frombuffer(raw, "<i4").astype(float) / 2147483648
    else:
        raise ValueError(f"unsupported sample width {sw}")
    return a.reshape(-1, ch).mean(axis=1), sr


def resample(a, sr, to):
    if sr == to or len(a) < 2:
        return a.copy()
    if to < sr:                         # Hann-windowed sinc so it doesn't alias
        cut = to / sr / 2
        half = min(int(8 / cut), 2048)
        t = np.arange(-half, half + 1)
        h = np.sinc(2 * cut * t) * 2 * cut * np.hanning(2 * half + 1)
        a = np.convolve(a, h / h.sum(), "same")
    n = max(2, int(round(len(a) * to / sr)))
    return np.interp(np.arange(n) * sr / to, np.arange(len(a)), a)


def to_sm12(a):
    peak = float(np.abs(a).max()) or 1.0
    v = np.rint(a / peak * 2047).astype(np.int32)
    return np.where(v < 0, 0x800 | -v, v)


def from_sm12(w):
    m = w & 0x7FF
    return np.where(w & 0x800, -m, m)


@dataclass
class Sample:
    path: str
    name: str
    audio: np.ndarray
    sr: int
    root: int = 60
    loop: bool = False
    reverse: bool = False
    div: int = 1                # 1 = 30 kHz, 2 = 15 kHz, 4 = 7.5 kHz
    start: int = 0              # the three markers are source frames
    end: int = 0
    loop_at: int = 0

    @classmethod
    def load(cls, path):
        a, sr = read_wav(path)
        if len(a) < 2:
            raise ValueError("WAV is empty")
        name = os.path.splitext(os.path.basename(path))[0]
        return cls(path, clean_name(name), a, sr, end=len(a))

    @property
    def rate(self):
        return RATE / self.div

    def words(self):
        """12-bit sign-magnitude data and the loop offset, excluding any tail."""
        a = self.audio[self.start:self.end]
        loop_from = self.loop_at - self.start
        if self.reverse:
            a = a[::-1]
            loop_from = 0
        w = to_sm12(resample(a, self.sr, self.rate))
        at = min(int(round(loop_from * self.rate / self.sr)), len(w) - 2) if self.loop else 0
        return w, max(at, 0)

    def stored(self):
        return int(round((self.end - self.start) * self.rate / self.sr)) + (0 if self.loop else TAIL)


def clean_name(s, n=16):
    s = "".join(c if 32 <= ord(c) < 127 else " " for c in s).strip()
    return s[:n] or "Sample"


def snap_zero(a, at):
    """Nearest rising zero crossing within 2 ms-ish of frame `at`."""
    lo, hi = max(at - 128, 0), min(at + 128, len(a) - 1)
    if hi <= lo:
        return at
    seg = a[lo:hi + 1]
    z = np.where((seg[:-1] < 0) & (seg[1:] >= 0))[0]
    return lo + int(z[np.argmin(np.abs(z - (at - lo)))]) + 1 if len(z) else at


# ---- SETUP ----------------------------------------------------------------
def obj_size(b, a):
    t = b[a] - 0x40
    if t < 0 or t > 0x18:
        return 0
    if t == 0:
        return (struct.unpack(">I", b[a + 4 : a + 8])[0] + 1) & ~1
    if t in (0x12, 0x13, 0x16, 0x17):
        return 8
    if t == 0x15:
        return 16
    return (struct.unpack(">H", b[a + 2 : a + 4])[0] + 1) & ~1


def walk(b):
    """Objects the way the OS finds them (0x5602): id -> (addr, size), last one wins."""
    length = -struct.unpack(">i", b[0x20:0x24])[0]
    assert length > 0, "block length must be stored negative"
    out = {}
    a, left = 0x24, length - 4
    while left > 0:
        s = obj_size(b, a)
        assert 0 < s <= left, f"bad object at {a:#x}"
        out[struct.unpack(">H", b[a : a + 2])[0]] = (a, s)
        a += s
        left -= s
    assert struct.unpack(">i", b[a : a + 4])[0] == 0, "block chain must end in a zero long"
    return out, a


def have_setup():
    return all(os.path.exists(os.path.join(DUMPS, n)) for n in SETUP_NAMES)


def load_setup():
    hi, lo = (open(os.path.join(DUMPS, n), "rb").read() for n in SETUP_NAMES)
    if len(hi) != 65536 or len(lo) != 65536:
        raise ValueError("SETUP dumps must be 64 KB each (U22 and U33)")
    return bytearray(v for p in zip(hi, lo) for v in p)


def lane_sum(lane):
    return sum(lane[:-2]) & 0xFFFF


def split_lanes(img):
    hi, lo = bytearray(img[0::2]), bytearray(img[1::2])
    for lane in (hi, lo):
        lane[-2:] = struct.pack(">H", lane_sum(lane))
    return hi, lo


def make_soundfile(tpl, sid, name, root, pitch, start, loop, end):
    o = bytearray(tpl)
    o[1] = sid
    o[0x10:0x1E] = name[:13].encode("ascii").ljust(14, b"\0")
    e = 8 + struct.unpack(">H", o[8:10])[0]
    o[e : e + 8] = struct.pack(">BBhHH", root, 0, 0, pitch, 0)
    o[e + 8 : e + 24] = struct.pack(">IIII", start, start, loop, end)
    return bytes(o)


def make_keymap(tpl, kid, sid, name):
    o = bytearray(tpl)
    o[1] = kid
    o[0x10:0x12] = struct.pack(">H", sid)
    table = 8 + struct.unpack(">H", o[8:10])[0]
    keys = struct.unpack(">H", o[6:8])[0]
    o[0x20:table] = name[:table - 0x21].encode("ascii").ljust(table - 0x20, b"\0")
    o[table : table + keys] = b"\x01" * keys
    return bytes(o)


def make_program(tpl, pid, kid, name):
    head = bytearray(tpl[:0x10])
    head[1] = pid
    e = tpl.index(0, 0x10)
    layer = bytearray(tpl[0x10 + ((e - 0x10 + 2) & ~1) :])
    layer[4] = kid
    layer[10] = 0                       # Tuning Note's trim is -10; patches run at full level
    nm = name[:16].encode("ascii") + b"\0"
    if len(nm) & 1:
        nm += b"\0"
    o = head + nm + layer
    o[2:4] = struct.pack(">H", len(o))
    return bytes(o)


# ---- build ----------------------------------------------------------------
def layout(samples):
    """Place samples into banks. Returns [(bank, first_sample_index)] or raises."""
    if len(samples) > MAX_SLOTS:
        raise ValueError(f"{len(samples)} samples, the slot limit is {MAX_SLOTS}")
    places, bank, used = [], 2, 0
    for s in samples:
        n = s.stored()
        if n > BANK_SAMPLES:
            raise ValueError(f"{s.name}: {n} samples, one bank holds {BANK_SAMPLES}")
        if used + n > BANK_SAMPLES:
            bank, used = bank + 1, 0
            if bank not in BANKS:
                raise ValueError("both free banks are full")
        places.append((bank, used))
        used += n
    return places


def usage(samples):
    """Fraction of banks 2 and 3 used, by actual placement."""
    fill = {2: 0, 3: 0}
    for s, (bank, at) in zip(samples, layout(samples)):
        fill[bank] = at + s.stored()
    return fill


def encode_bank(words):
    w0, w1 = words[0::2], words[1::2]
    return (w0 >> 4).astype(np.uint8), (w1 >> 4).astype(np.uint8), ((w0 & 15) | ((w1 & 15) << 4)).astype(np.uint8)


def decode_bank(x, y, z):
    x, y, z = (a.astype(np.int32) for a in (x, y, z))
    s = np.empty(2 * len(x), np.int32)
    s[0::2] = (x << 4) | (z & 15)
    s[1::2] = (y << 4) | (z >> 4)
    return s


def build(samples, out):
    """Write chip images into `out`, verify them, and return a list of (label, path, kind)."""
    if not samples:
        raise ValueError("no samples")
    places = layout(samples)
    banks = {b: np.zeros(BANK_SAMPLES, np.int32) for b, _ in places}

    img = load_setup()
    try:
        objs, end = walk(img)
    except AssertionError:
        end = None
    if end != BLOCK_END:
        raise ValueError("These dumps are not GX SETUP V2.5 (U22 = HI, U33 = LO)")
    tpl = {k: bytes(img[objs[k][0] : objs[k][0] + objs[k][1]]) for k in (TPL_PROGRAM, TPL_KEYMAP, TPL_SOUNDFILE)}

    new, expect = bytearray(), []
    for i, (s, (bank, at)) in enumerate(zip(samples, places)):
        w, loop_from = s.words()
        n = len(w)
        banks[bank][at : at + n] = w
        base = BANKS[bank][0] + 2 * at
        total = n if s.loop else n + TAIL
        stop = base + 2 * total
        loop = base + 2 * loop_from if s.loop else stop - 2 * TAIL
        pitch = s.root * 100 + int(round(1200 * math.log2(s.div)))
        pid, kid, sid = FIRST_PROGRAM + i, FIRST_KEYMAP + i, FIRST_SOUNDFILE + i
        new += make_soundfile(tpl[TPL_SOUNDFILE], sid, s.name, s.root, pitch, base, loop, stop)
        new += make_keymap(tpl[TPL_KEYMAP], kid, sid, s.name)
        new += make_program(tpl[TPL_PROGRAM], pid, kid, s.name)
        expect.append((pid, kid, sid, s.name, s.root, pitch, base, loop, stop, bank, at, w))

    limit = len(img) - 4 - 4            # checksum long, then the zero terminator
    if BLOCK_END + len(new) > limit:
        raise ValueError("SETUP is full")
    factory = bytes(img)
    img[BLOCK_END : BLOCK_END + len(new)] = new
    img[0x20:0x24] = struct.pack(">i", -(BLOCK_END + len(new) - 0x20))
    hi, lo = split_lanes(img)

    chips = {}
    for b, words in banks.items():
        x, y, z = encode_bank(words)
        _, xn, yn, zn = BANKS[b]
        chips[xn], chips[yn], chips[zn] = x.tobytes(), y.tobytes(), z.tobytes()
    chips["U22_setup_hi"], chips["U33_setup_lo"] = bytes(hi), bytes(lo)

    verify(chips, factory, BLOCK_END + len(new), expect)

    os.makedirs(out, exist_ok=True)
    result = []
    for label, data in chips.items():
        path = os.path.join(out, label + ".bin")
        with open(path, "wb") as f:
            f.write(data)
        result.append((label, path, "setup" if "setup" in label else "wave"))
    return result


def verify(chips, factory, new_end, expect):
    """Re-read everything from the finished chip bytes alone."""
    hi, lo = chips["U22_setup_hi"], chips["U33_setup_lo"]
    assert len(hi) == len(lo) == 65536, "SETUP chips must be 64 KB"
    for lane in (hi, lo):
        assert lane_sum(lane) == struct.unpack(">H", lane[-2:])[0], "checksum"
    b = bytes(v for p in zip(hi, lo) for v in p)
    objs, end = walk(b)
    assert end == new_end, "walk stopped early"

    fac, _ = walk(factory)
    assert len(objs) == len(fac) + 3 * len(expect), "object count"
    for k, (a, s) in fac.items():
        assert objs[k] == (a, s), f"factory object {k:#x} moved"
        assert b[a : a + s] == factory[a : a + s], f"factory object {k:#x} changed"
    assert b[0x24:BLOCK_END] == factory[0x24:BLOCK_END], "factory block changed"

    for pid, kid, sid, name, root, pitch, base, loop, stop, bank, at, w in expect:
        pa, ps = objs[0x5000 + pid]
        e = b.index(0, pa + 0x10)
        assert b[pa + 0x10 : e].decode() == name[:16], "program name"
        la = pa + 0x10 + ((e - pa - 0x10 + 2) & ~1)
        assert b[la] == 0x51 and b[la + 4] == kid, "program layer"
        assert la + obj_size(b, la) == pa + ps, "program size"
        ka, _ = objs[0x4600 + kid]
        assert struct.unpack(">H", b[ka + 0x10 : ka + 0x12])[0] == sid, "keymap soundfile"
        sa, _ = objs[0x4500 + sid]
        ee = sa + 8 + struct.unpack(">H", b[sa + 8 : sa + 10])[0]
        row = struct.unpack(">BBhHHIIII", b[ee : ee + 24])
        assert row == (root, 0, 0, pitch, 0, base, base, loop, stop), "sample entry"
        assert base <= loop < stop and not base & 1, "sample addresses"
        _, xn, yn, zn = BANKS[bank]
        decoded = decode_bank(*(np.frombuffer(chips[c], np.uint8) for c in (xn, yn, zn)))
        assert np.array_equal(decoded[at : at + len(w)], w), "wave round trip"
        assert (stop - BANKS[bank][0]) // 2 <= BANK_SAMPLES, "sample crosses the bank end"
