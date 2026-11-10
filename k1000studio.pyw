#!/usr/bin/env python3
"""K1000 Studio: load WAVs, trim and loop them, build and burn GX chip sets."""
import os
import shutil
import subprocess
import sys
import threading

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import k1000engine as k  # noqa: E402

try:
    import sounddevice as sd
except Exception:
    sd = None

MINIPRO = os.path.join(os.path.expanduser("~"), "Documents", "tools", "minipro_win", "minipro.exe")
PARTS = {"wave": "W27C040", "setup": "W27C512@DIP28"}
BURN_NOTE = {"wave": "W27C040, ~3.5 min", "setup": "W27C512, ~30 s"}
OUT = os.path.join(HERE, "build")
GREEN, BLUE, RED, DIM, BG, PANEL = "#5dff9b", "#5fb8ff", "#ff6b6b", "#6d8a7c", "#0d1412", "#16211d"

STYLE = f"""
* {{ font-family: Consolas; font-size: 13px; color: {GREEN}; }}
QMainWindow, QWidget {{ background: {BG}; }}
QLineEdit, QSpinBox, QComboBox, QListWidget, QPlainTextEdit {{
    background: {PANEL}; border: 1px solid #2c4238; padding: 4px; selection-background-color: #24513d; }}
QListWidget::item {{ padding: 4px; }}
QListWidget::item:selected {{ background: #24513d; color: {GREEN}; }}
QPushButton {{ background: {PANEL}; border: 1px solid #2f6a4e; padding: 7px 12px; }}
QPushButton:hover {{ background: #20392e; }}
QPushButton:disabled {{ color: #3e5249; border-color: #26342e; }}
QPushButton#build {{ color: {BLUE}; border-color: {BLUE}; font-weight: bold; padding: 10px; }}
QProgressBar {{ background: {PANEL}; border: 1px solid #2c4238; text-align: center; height: 18px; }}
QProgressBar::chunk {{ background: #1f6b4a; }}
QLabel#title {{ font-size: 20px; font-weight: bold; color: {BLUE}; }}
QLabel#dim {{ color: {DIM}; }}
"""


class WaveView(QWidget):
    moved = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(190)
        self.s = None
        self.drag = None
        self.snap = True
        self.cache = None

    def set_sample(self, s):
        self.s = s
        self.cache = None
        self.update()

    def resizeEvent(self, e):
        self.cache = None

    def x_of(self, frame):
        return frame / max(len(self.s.audio) - 1, 1) * (self.width() - 1)

    def frame_of(self, x):
        return int(np.clip(x / max(self.width() - 1, 1), 0, 1) * (len(self.s.audio) - 1))

    def markers(self):
        m = {"L": self.s.loop_at} if self.s.loop else {}   # L first: it wins a tie when grabbing
        m.update(S=self.s.start, E=self.s.end)
        return m

    def peaks(self):
        if self.cache is None:
            w = max(self.width(), 2)
            edges = np.linspace(0, len(self.s.audio), w + 1).astype(int)
            a = self.s.audio
            top = max(float(np.abs(a).max()), 1e-6)
            self.cache = [(float(a[p:max(q, p + 1)].min()) / top, float(a[p:max(q, p + 1)].max()) / top)
                          for p, q in zip(edges[:-1], edges[1:])]
        return self.cache

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(PANEL))
        if not self.s:
            p.setPen(QColor(DIM))
            p.drawText(self.rect(), Qt.AlignCenter, "Drop WAV files here")
            return
        h, mid = self.height(), self.height() / 2
        kept = QColor("#3dd48a")
        cut = QColor("#2b4037")
        sx, ex = self.x_of(self.s.start), self.x_of(self.s.end)
        for x, (lo, hi) in enumerate(self.peaks()):
            p.setPen(kept if sx <= x <= ex else cut)
            p.drawLine(x, int(mid - hi * (mid - 6)), x, int(mid - lo * (mid - 6)))
        colors = {"S": GREEN, "E": RED, "L": BLUE}
        for name, frame in self.markers().items():
            x = self.x_of(frame)
            p.setPen(QPen(QColor(colors[name]), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            p.drawText(QRectF(min(x + 4, self.width() - 16), 2, 14, 16), name)

    def mousePressEvent(self, e):
        if not self.s:
            return
        x = e.position().x()
        name, frame = min(self.markers().items(), key=lambda m: abs(self.x_of(m[1]) - x))
        self.drag = name if abs(self.x_of(frame) - x) < 12 else None

    def mouseMoveEvent(self, e):
        if not (self.s and self.drag):
            return
        s, f = self.s, self.frame_of(e.position().x())
        gap = 64
        if self.drag == "S":
            s.start = min(f, s.end - gap)
            s.loop_at = max(s.loop_at, s.start)
        elif self.drag == "E":
            s.end = max(f + 1, s.start + gap)
            s.loop_at = min(s.loop_at, s.end - gap)
        else:
            s.loop_at = int(np.clip(k.snap_zero(s.audio, f) if self.snap else f, s.start, s.end - gap))
        self.update()
        self.moved.emit()

    def mouseReleaseEvent(self, e):
        self.drag = None


class Studio(QMainWindow):
    burn_log = Signal(str)
    burn_done = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("K1000 Studio")
        icon = os.path.join(HERE, "hexgirl_icon.png")
        if os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))
        self.samples = []
        self.built = {}
        self.burning = False
        self.loading = False
        self.setAcceptDrops(True)
        self.setup_ui()
        self.burn_log.connect(self.log)
        self.burn_done.connect(self.on_burned)
        self.refresh()

    # ---- layout -----------------------------------------------------------
    def setup_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        top = QHBoxLayout()
        title = QLabel("K1000 STUDIO")
        title.setObjectName("title")
        top.addWidget(title)
        sub = QLabel("GX  /  new patches 160-192  /  factory sounds stay")
        sub.setObjectName("dim")
        top.addWidget(sub)
        top.addStretch()
        self.meters = {}
        for b in k.BANKS:
            m = QProgressBar()
            m.setFixedWidth(230)
            m.setRange(0, k.BANK_SAMPLES)
            top.addWidget(m)
            self.meters[b] = m
        v.addLayout(top)

        body = QHBoxLayout()
        v.addLayout(body, 1)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setFixedWidth(250)
        self.list.currentRowChanged.connect(self.select)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.b_add = self.button("ADD", self.add_dialog, row)
        self.b_del = self.button("DEL", self.remove, row)
        self.b_up = self.button("UP", lambda: self.shift(-1), row)
        self.b_down = self.button("DOWN", lambda: self.shift(1), row)
        left.addLayout(row)
        body.addLayout(left)

        mid = QVBoxLayout()
        self.wave = WaveView()
        self.wave.moved.connect(self.on_edit)
        mid.addWidget(self.wave, 1)

        g = QGridLayout()
        self.e_name = QLineEdit()
        self.e_name.setMaxLength(16)
        self.e_name.textEdited.connect(self.on_edit)
        self.e_root = QSpinBox()
        self.e_root.setRange(12, 108)
        self.e_root.valueChanged.connect(self.on_edit)
        self.l_root = QLabel()
        self.e_rate = QComboBox()
        self.e_rate.addItems(["30 kHz", "15 kHz (lofi, 2x time)", "7.5 kHz (crunch, 4x time)"])
        self.e_rate.currentIndexChanged.connect(self.on_edit)
        g.addWidget(QLabel("NAME"), 0, 0)
        g.addWidget(self.e_name, 0, 1)
        g.addWidget(QLabel("ROOT"), 0, 2)
        g.addWidget(self.e_root, 0, 3)
        g.addWidget(self.l_root, 0, 4)
        g.addWidget(QLabel("RATE"), 1, 0)
        g.addWidget(self.e_rate, 1, 1)
        self.e_loop = QCheckBox("LOOP")
        self.e_loop.toggled.connect(self.on_edit)
        self.e_rev = QCheckBox("REVERSE")
        self.e_rev.toggled.connect(self.on_edit)
        self.e_snap = QCheckBox("SNAP")
        self.e_snap.setChecked(True)
        self.e_snap.toggled.connect(self.on_edit)
        checks = QHBoxLayout()
        for c in (self.e_loop, self.e_rev, self.e_snap):
            checks.addWidget(c)
        checks.addStretch()
        g.addLayout(checks, 1, 2, 1, 3)
        mid.addLayout(g)

        act = QHBoxLayout()
        self.b_play = self.button("PREVIEW 12-BIT", self.preview, act)
        self.b_stop = self.button("STOP", self.stop_audio, act)
        self.l_info = QLabel()
        self.l_info.setObjectName("dim")
        act.addWidget(self.l_info, 1)
        mid.addLayout(act)
        body.addLayout(mid, 1)

        right = QVBoxLayout()
        self.b_build = QPushButton("BUILD CHIPS")
        self.b_build.setObjectName("build")
        self.b_build.clicked.connect(self.build)
        right.addWidget(self.b_build)
        self.chip_box = QVBoxLayout()
        right.addLayout(self.chip_box)
        right.addStretch()
        panel = QWidget()
        panel.setFixedWidth(270)
        panel.setLayout(right)
        body.addWidget(panel)

        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setFixedHeight(92)
        v.addWidget(self.status)
        self.resize(1180, 620)

    def button(self, text, fn, box):
        b = QPushButton(text)
        b.clicked.connect(lambda: fn())
        box.addWidget(b)
        return b

    def log(self, text):
        self.status.appendPlainText(text)

    # ---- samples ----------------------------------------------------------
    def current(self):
        i = self.list.currentRow()
        return self.samples[i] if 0 <= i < len(self.samples) else None

    def add_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add WAV files", "", "WAV files (*.wav)")
        self.add_files(paths)

    def add_files(self, paths):
        for p in paths:
            if len(self.samples) >= k.MAX_SLOTS:
                self.log(f"All {k.MAX_SLOTS} patch slots are full.")
                break
            try:
                self.samples.append(k.Sample.load(p))
                self.log(f"Added {os.path.basename(p)}")
            except Exception as e:
                self.log(f"Could not read {os.path.basename(p)}: {e}")
        self.changed()
        if self.samples and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        elif paths:
            self.list.setCurrentRow(len(self.samples) - 1)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_files([u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile().lower().endswith(".wav")])

    def remove(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.samples):
            del self.samples[i]
            self.changed()
            self.list.setCurrentRow(min(i, len(self.samples) - 1))

    def shift(self, by):
        i = self.list.currentRow()
        j = i + by
        if 0 <= i < len(self.samples) and 0 <= j < len(self.samples):
            self.samples[i], self.samples[j] = self.samples[j], self.samples[i]
            self.changed()
            self.list.setCurrentRow(j)

    def select(self, i):
        s = self.current()
        self.wave.set_sample(s)
        self.loading = True
        for w, v in ((self.e_name, s.name if s else ""),):
            w.setText(v)
        if s:
            self.e_root.setValue(s.root)
            self.e_rate.setCurrentIndex({1: 0, 2: 1, 4: 2}[s.div])
            self.e_loop.setChecked(s.loop)
            self.e_rev.setChecked(s.reverse)
        self.loading = False
        self.refresh()

    def on_edit(self, *_):
        s = self.current()
        self.wave.snap = self.e_snap.isChecked()
        if not s or self.loading:
            self.refresh()
            return
        s.name = k.clean_name(self.e_name.text()) if self.e_name.text().strip() else s.name
        s.root = self.e_root.value()
        s.div = (1, 2, 4)[self.e_rate.currentIndex()]
        if self.e_loop.isChecked() and not s.loop:
            s.loop_at = int(np.clip(k.snap_zero(s.audio, (s.start + s.end) // 2), s.start, s.end - 64))
        s.loop = self.e_loop.isChecked()
        s.reverse = self.e_rev.isChecked()
        self.wave.update()
        self.changed()

    def changed(self):
        """Anything edited: rebuild the list and retire old chip images."""
        keep = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for i, s in enumerate(self.samples):
            self.list.addItem(f"{k.FIRST_PROGRAM + i}  {s.name}")
        self.list.setCurrentRow(keep if keep < len(self.samples) else len(self.samples) - 1)
        self.list.blockSignals(False)
        self.clear_chips()
        self.refresh()

    def refresh(self):
        s = self.current()
        self.l_root.setText(k.note_name(self.e_root.value()))
        for w in (self.e_name, self.e_root, self.e_rate, self.e_loop, self.e_rev, self.b_play, self.b_del):
            w.setEnabled(bool(s))
        problem = None
        fill = {2: 0, 3: 0}
        try:
            if self.samples:
                fill = k.usage(self.samples)
        except ValueError as e:
            problem = str(e)
        for b, m in self.meters.items():
            m.setValue(min(fill[b], k.BANK_SAMPLES))
            m.setFormat(f"BANK {b}: {fill[b] / k.RATE:.1f} / {k.BANK_SAMPLES / k.RATE:.1f} s at 30k")
        self.b_build.setEnabled(bool(self.samples) and not problem and not self.burning)
        if problem:
            self.l_info.setText(problem)
        elif s:
            n = s.stored()
            self.l_info.setText(f"{(s.end - s.start) / s.sr:.2f} s  |  {n} samples  |  {s.rate:.0f} Hz  |  {s.sr} Hz source")
        else:
            self.l_info.setText("")

    # ---- audio ------------------------------------------------------------
    def preview(self):
        s = self.current()
        if not s or sd is None:
            self.log("Audio output is not available.")
            return
        w, at = s.words()
        a = k.from_sm12(w).astype(np.float32) / 2048
        if s.loop:
            a = np.concatenate([a] + [a[at:]] * 4)
        sd.play(a * 0.8, int(s.rate))

    def stop_audio(self):
        if sd is not None:
            sd.stop()

    # ---- chips ------------------------------------------------------------
    def clear_chips(self):
        self.built = {}
        while self.chip_box.count():
            w = self.chip_box.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def ask_for_setup(self):
        """First build: copy the user's own U22/U33 dumps next to the engine."""
        picked = []
        for chip in ("U22 (SETUP HI)", "U33 (SETUP LO)"):
            p, _ = QFileDialog.getOpenFileName(self, f"Pick your GX {chip} dump", "", "ROM dumps (*.bin *.rom);;All files (*)")
            if not p:
                return False
            picked.append(p)
        os.makedirs(k.DUMPS, exist_ok=True)
        for p, name in zip(picked, k.SETUP_NAMES):
            shutil.copyfile(p, os.path.join(k.DUMPS, name))
        return True

    def build(self):
        self.clear_chips()
        if not k.have_setup() and not self.ask_for_setup():
            self.log("Build needs your own U22 and U33 SETUP dumps.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            chips = k.build(self.samples, OUT)
        except Exception as e:
            self.log(f"BUILD FAILED: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        for label, path, kind in chips:
            b = QPushButton(f"BURN {label}")
            b.setToolTip(BURN_NOTE[kind])
            b.clicked.connect(lambda _=False, c=(label, path, kind): self.burn(*c))
            self.chip_box.addWidget(b)
            self.built[label] = b
        self.log(f"Built and verified {len(chips)} chip images in {OUT}")

    def burn(self, label, path, kind):
        if self.burning:
            return
        self.burning = True
        for b in self.built.values():
            b.setEnabled(False)
        self.b_build.setEnabled(False)
        self.log(f"{label}: writing, {BURN_NOTE[kind]}, don't pull")
        threading.Thread(target=self.burn_thread, args=(label, path, PARTS[kind]), daemon=True).start()

    def burn_thread(self, label, path, part):
        ok = False
        try:
            r = subprocess.run([MINIPRO, "-p", part, "-w", path], capture_output=True, text=True, errors="replace",
                               creationflags=subprocess.CREATE_NO_WINDOW)
            tail = [x for x in (r.stdout + r.stderr).replace("\r", "\n").splitlines() if x.strip()][-4:]
            self.burn_log.emit("\n".join(tail))
            ok = r.returncode == 0
        except Exception as e:
            self.burn_log.emit(str(e))
        self.burn_done.emit(label, ok)

    def on_burned(self, label, ok):
        self.burning = False
        for b in self.built.values():
            b.setEnabled(True)
        if label in self.built:
            self.built[label].setText(f"{'DONE' if ok else 'FAILED'} {label}")
        self.log(f"{label}: {'DONE, safe to pull' if ok else 'FAILED'}")
        self.refresh()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Consolas", 10))
    win = Studio()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
