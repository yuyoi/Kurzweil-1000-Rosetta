"""Drive K1000 Studio by scripted clicks, offscreen, and save screenshots."""
import importlib.machinery, importlib.util, os, sys, tempfile, wave
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
loader = importlib.machinery.SourceFileLoader('k1000studio', os.path.join(ROOT, 'k1000studio.pyw'))
spec = importlib.util.spec_from_loader('k1000studio', loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
if not m.k.have_setup():
    raise SystemExit(f'Put your own U22/U33 SETUP dumps in {m.k.DUMPS} first: {m.k.SETUP_NAMES}')
OUT = sys.argv[1]
m.OUT = os.path.join(OUT, 'build')
app = QApplication([])
app.setStyleSheet(m.STYLE)
w = m.Studio(); w.show(); app.processEvents()

def tone(name, secs):
    sr = 44100; t = np.arange(int(secs * sr)) / sr
    s = (np.sin(2 * np.pi * 110 * t) * np.exp(-t * 1.5) * 28000).astype('<i2')
    p = os.path.join(OUT, name)
    with wave.open(p, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr); f.writeframes(s.tobytes())
    return p

click = lambda b: QTest.mouseClick(b, Qt.LeftButton)
assert not w.b_build.isEnabled(), 'build must be off when empty'
w.add_files([tone('Dusty Choir.wav', 2.0), tone('Tape Hit.wav', 0.6), tone('Drone.wav', 3.0)])
app.processEvents()
assert w.list.count() == 3 and w.list.item(0).text().startswith('160')

# edit by real widgets
w.list.setCurrentRow(0); app.processEvents()
assert w.e_name.text() == 'Dusty Choir'
w.e_root.setValue(48); assert w.samples[0].root == 48 and w.l_root.text() == 'C3'
w.e_rate.setCurrentIndex(1); assert w.samples[0].div == 2

# loop on and drag the markers
w.list.setCurrentRow(2); app.processEvents()
QTest.mouseClick(w.e_loop, Qt.LeftButton, pos=QPoint(8, w.e_loop.height() // 2))
assert w.samples[2].loop, 'loop tick'
wv = w.wave; s = w.samples[2]
def drag(frame, to_x):
    x = int(wv.x_of(frame)); y = wv.height() // 2
    QTest.mousePress(wv, Qt.LeftButton, pos=QPoint(x, y))
    QTest.mouseMove(wv, QPoint(to_x, y)); app.processEvents()
    QTest.mouseRelease(wv, Qt.LeftButton, pos=QPoint(to_x, y))
drag(s.end - 1, wv.width() - 60); assert s.end < len(s.audio), 'end marker'
drag(s.loop_at, wv.width() // 3)
print('loop_at', s.loop_at, 'start', s.start, 'end', s.end)
assert s.start < s.loop_at < s.end, 'loop marker'

# reorder and rename updates the patch list
click(w.b_up); app.processEvents()
assert w.list.currentRow() == 1 and w.list.item(1).text().endswith('Drone')
QTest.keyClicks(w.e_name, ' X'); app.processEvents()
assert w.samples[1].name == 'Drone X', w.samples[1].name

# 12-bit preview runs without opening an audio device here
played = []
m.sd = type('S', (), {'play': staticmethod(lambda a, r: played.append((len(a), r))), 'stop': staticmethod(lambda: played.append('stop'))})
click(w.b_play); click(w.b_stop); print('preview', played)
assert played[0][1] == 30200 and played[-1] == 'stop'

# build, and an edit must retire the images so nothing stale gets burned
click(w.b_build); app.processEvents()
assert list(w.built) == ['U60_X2', 'U52_Y2', 'U56_Z2', 'U22_setup_hi', 'U33_setup_lo'], list(w.built)
w.grab().save(os.path.join(OUT, 'studio_built.png'))
for n in w.built: assert os.path.getsize(os.path.join(m.OUT, n + '.bin')) in (524288, 65536)
w.e_root.setValue(50); app.processEvents(); QTest.qWait(20)
assert not w.built and w.chip_box.count() == 0, 'stale images must go'
click(w.b_build); app.processEvents()

# burn path, with minipro faked
cmds = []
class R:
    returncode = 0; stdout = 'Verification OK\n'; stderr = ''
real = m.subprocess.run
m.subprocess.run = lambda a, **kw: (cmds.append(a), R())[1]
click(w.built['U60_X2'])
assert all(not b.isEnabled() for b in w.built.values()), 'buttons lock during a burn'
for _ in range(100):
    QTest.qWait(10)
    if not w.burning: break
m.subprocess.run = real
print(cmds[0][1:3], os.path.basename(cmds[0][-1]))
assert cmds[0][2] == 'W27C040' and cmds[0][3] == '-w' and w.built['U60_X2'].text() == 'DONE U60_X2'
click(w.b_del); app.processEvents(); assert len(w.samples) == 2
w.grab().save(os.path.join(OUT, 'studio_final.png'))
print('STUDIO CLICKS OK')
