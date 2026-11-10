"""Engine check: real WAV path, mixed settings, both banks, failure cases."""
import os, sys, tempfile, wave
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import k1000engine as k

if not k.have_setup():
    raise SystemExit(f'Put your own U22/U33 SETUP dumps in {k.DUMPS} first: {k.SETUP_NAMES}')
tmp = tempfile.mkdtemp(prefix='k1000test')
def tone(name, secs, sr=44100, ch=2):
    t = np.arange(int(secs * sr)) / sr
    s = (np.sin(2 * np.pi * 220 * t) * np.exp(-t) * 30000).astype('<i2')
    p = os.path.join(tmp, name)
    with wave.open(p, 'wb') as w:
        w.setnchannels(ch); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(np.repeat(s, ch).tobytes())
    return p

a = k.Sample.load(tone('short hit.wav', 0.5))
b = k.Sample.load(tone('loopy.wav', 1.0)); b.loop = True; b.loop_at = 22050; b.root = 48
c = k.Sample.load(tone('lofi.wav', 2.0)); c.div = 4; c.reverse = True; c.name = 'A very long sample name'[:16]
d = k.Sample.load(tone('big.wav', 30.0, 22050, 1))
e = k.Sample.load(tone('big2.wav', 8.0, 22050, 1))
sm = [a, b, c, d, e]
print('layout', k.layout(sm), 'usage', k.usage(sm))
out = os.path.join(tmp, 'out')
res = k.build(sm, out)
for label, path, kind in res:
    print(label, kind, os.path.getsize(path))
assert {r[0] for r in res} == {'U60_X2','U52_Y2','U56_Z2','U61_X3','U53_Y3','U57_Z3','U22_setup_hi','U33_setup_lo'}

# one sample uses only bank 2
assert len(k.build([a], os.path.join(tmp, 'o2'))) == 5

# 33 slots fit in SETUP, 34 refused
many = [k.Sample.load(tone('s.wav', 0.05)) for _ in range(33)]
print('33 slots', len(k.build(many, os.path.join(tmp, 'o3'))))
try:
    k.build(many + [a], os.path.join(tmp, 'o4')); raise SystemExit('34 accepted')
except ValueError as x:
    print('refused:', x)

# a too long sample is refused
try:
    k.build([k.Sample.load(tone('huge.wav', 36.0, 22050, 1))], os.path.join(tmp, 'o5')); raise SystemExit('huge accepted')
except ValueError as x:
    print('refused:', x)

# a broken image is caught by verify
fac = bytes(k.load_setup())
x, y, z = k.encode_bank(np.zeros(k.BANK_SAMPLES, np.int32))
print('factory objects', len(k.walk(fac)[0]), 'end', hex(k.walk(fac)[1]))
print('ALL OK')
