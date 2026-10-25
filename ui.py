"""Supporting windows: parameter prompt, progress toast, result viewer, Float shelf."""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDrag, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from . import media
from .ops import Ctx, Op, Result, run_op

STYLE = """
QWidget#card { background: #1c1c20; border: 1px solid #34343c; border-radius: 12px; }
QLabel { color: #f0f0f4; font: 10pt 'Segoe UI'; }
QLabel#muted { color: #a0a0aa; font: 9pt 'Segoe UI'; }
QProgressBar { background: #2c2c34; border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #ff8c1a; border-radius: 4px; }
QPushButton { background: #2c2c34; color: #f0f0f4; border: none; border-radius: 6px;
              padding: 5px 12px; font: 9pt 'Segoe UI'; }
QPushButton:hover { background: #3a3a44; }
QPushButton#primary { background: #ff8c1a; color: #141414; }
"""


def app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.Antialiasing)
    qp.setPen(Qt.NoPen)
    qp.setBrush(QColor(255, 140, 26))
    qp.drawEllipse(6, 10, 52, 50)
    qp.setBrush(QColor(76, 175, 80))
    qp.drawEllipse(30, 2, 18, 11)
    qp.setBrush(QColor(255, 255, 255, 90))
    qp.drawEllipse(16, 20, 14, 10)
    qp.end()
    return QIcon(pm)


# ---- parameters ----------------------------------------------------------------

def ask_params(op: Op) -> dict | None:
    if not op.params:
        return {}
    store = QSettings("Satsuma", "Satsuma")
    dlg = QDialog()
    dlg.setWindowTitle(op.label)
    dlg.setWindowIcon(app_icon())
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint)
    form = QFormLayout(dlg)
    widgets = {}
    for p in op.params:
        last = store.value(f"params/{op.id}/{p.key}", p.default)
        if p.choices:
            w = QComboBox()
            w.addItems(p.choices)
            if last in p.choices:
                w.setCurrentText(last)
        else:
            w = QLineEdit(str(last))
            w.setPlaceholderText(p.hint)
            if p.hint:
                w.setToolTip(p.hint)
        widgets[p.key] = w
        form.addRow(p.label, w)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    form.addRow(bb)
    dlg.move(QCursor.pos())
    dlg.activateWindow()
    if dlg.exec() != QDialog.Accepted:
        return None
    vals = {k: (w.currentText() if isinstance(w, QComboBox) else w.text())
            for k, w in widgets.items()}
    for k, v in vals.items():
        store.setValue(f"params/{op.id}/{k}", v)
    return vals


# ---- running a job -------------------------------------------------------------

class _Bridge(QObject):
    progress = Signal(float, str)
    done = Signal(object)       # Result
    failed = Signal(str)


class JobToast(QWidget):
    """Small card in the bottom-right corner for one job."""

    live: list["JobToast"] = []

    def __init__(self, op: Op, paths: list[Path], params: dict) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet(STYLE)
        self.op, self.paths = op, paths
        card = QWidget(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        self.title = QLabel(op.label)
        name = paths[0].name if len(paths) == 1 else f"{len(paths)} files"
        self.sub = QLabel(name)
        self.sub.setObjectName("muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        row = QHBoxLayout()
        self.btn_show = QPushButton("Show")
        self.btn_show.setObjectName("primary")
        self.btn_show.hide()
        self.btn_show.clicked.connect(self._reveal)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._cancel)
        row.addStretch()
        row.addWidget(self.btn_show)
        row.addWidget(self.btn_cancel)
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addWidget(self.bar)
        lay.addLayout(row)
        self.setFixedWidth(320)
        self.adjustSize()
        self._place()
        self.result: Result | None = None
        self.ctx = Ctx(params, progress=self._on_progress_thread)
        self.bridge = _Bridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.done.connect(self._on_done)
        self.bridge.failed.connect(self._on_failed)
        JobToast.live.append(self)
        self.show()
        threading.Thread(target=self._work, daemon=True).start()

    def _place(self) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        stack = sum(t.height() + 10 for t in JobToast.live if t is not self)
        self.move(geo.right() - self.width() - 16, geo.bottom() - self.height() - 16 - stack)

    def _work(self) -> None:
        try:
            self.bridge.done.emit(run_op(self.op, self.paths, self.ctx))
        except media.Cancelled:
            self.bridge.failed.emit("Cancelled")
        except Exception as e:
            self.bridge.failed.emit(str(e) or type(e).__name__)

    def _on_progress_thread(self, frac: float, label: str) -> None:
        self.bridge.progress.emit(frac, label)

    def _on_progress(self, frac: float, label: str) -> None:
        self.bar.setValue(int(frac * 1000))
        if label:
            self.title.setText(f"{self.op.label} · {label}")

    def _on_done(self, result: Result) -> None:
        self.result = result
        self.bar.setValue(1000)
        self.title.setText(f"{self.op.label} · done")
        self.btn_cancel.setText("Close")
        self.btn_cancel.clicked.disconnect()
        self.btn_cancel.clicked.connect(self.close)
        if result.outputs:
            self.btn_show.show()
            if QSettings("Satsuma", "Satsuma").value("reveal", "true") == "true":
                reveal(result.outputs)
        msg = result.message
        if msg and (not result.outputs or len(msg) > 90 or "\n" in msg):
            ResultView(self.op.label, msg).show()
        elif msg:
            self.sub.setText(msg)
        QTimer.singleShot(6000 if result.outputs else 2500, self.close)

    def _on_failed(self, err: str) -> None:
        self.title.setText(f"{self.op.label} · failed" if err != "Cancelled" else "Cancelled")
        self.sub.setText(err)
        self.sub.setWordWrap(True)
        self.bar.hide()
        self.btn_cancel.setText("Close")
        self.btn_cancel.clicked.disconnect()
        self.btn_cancel.clicked.connect(self.close)
        self.adjustSize()
        QTimer.singleShot(12000, self.close)

    def _cancel(self) -> None:
        self.ctx.cancel()

    def _reveal(self) -> None:
        if self.result:
            reveal(self.result.outputs)

    def closeEvent(self, e) -> None:
        if self in JobToast.live:
            JobToast.live.remove(self)
        super().closeEvent(e)


class ResultView(QDialog):
    """Shows text results (metadata, QR contents, batch errors) with a Copy button."""

    keep: list["ResultView"] = []

    def __init__(self, title: str, text: str) -> None:
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setWindowTitle(title)
        self.setWindowIcon(app_icon())
        self.resize(460, 320)
        lay = QVBoxLayout(self)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        lay.addWidget(box)
        row = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addStretch()
        row.addWidget(copy)
        row.addWidget(close)
        lay.addLayout(row)
        ResultView.keep.append(self)
        self.finished.connect(lambda: ResultView.keep.remove(self) if self in ResultView.keep
                              else None)


# ---- shell helpers ----------------------------------------------------------------

def reveal(paths: list[Path]) -> None:
    if not paths:
        return
    p = Path(paths[0])
    if os.name == "nt":
        subprocess.Popen(f'explorer /select,"{p}"')
    elif os.uname().sysname == "Darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])


def copy_files_to_clipboard(paths: list[Path]) -> None:
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    md.setText("\n".join(str(p) for p in paths))
    # Tell Explorer that Paste should copy (1 = DROPEFFECT_COPY), not move
    md.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',
               (1).to_bytes(4, "little"))
    QApplication.clipboard().setMimeData(md)


def open_terminal(folder: Path) -> None:
    if os.name != "nt":
        subprocess.Popen(["x-terminal-emulator"], cwd=folder)
        return
    import shutil
    if shutil.which("wt"):
        subprocess.Popen(["wt", "-d", str(folder)])
    else:
        subprocess.Popen(["powershell", "-NoExit"], cwd=folder,
                         creationflags=0x00000010)  # CREATE_NEW_CONSOLE


def open_with(path: Path) -> None:
    if os.name == "nt":
        subprocess.Popen(["rundll32", "shell32.dll,OpenAs_RunDLL", str(path)])


def run_action(op: Op, paths: list[Path]) -> None:
    import shutil
    if op.id == "act_copy_path":
        QApplication.clipboard().setText("\n".join(f'"{p}"' if " " in str(p) else str(p)
                                                   for p in paths))
    elif op.id == "act_copy_file":
        copy_files_to_clipboard(paths)
    elif op.id == "act_terminal":
        open_terminal(paths[0] if paths[0].is_dir() else paths[0].parent)
    elif op.id == "act_reveal":
        reveal(paths)
    elif op.id in ("act_move", "act_copy_to"):
        dest = QFileDialog.getExistingDirectory(None, op.label, str(paths[0].parent))
        if not dest:
            return
        from .util import unique_path
        done = []
        for p in paths:
            target = unique_path(Path(dest) / p.name)
            if op.id == "act_move":
                shutil.move(str(p), target)
            elif p.is_dir():
                shutil.copytree(p, target)
            else:
                shutil.copy2(p, target)
            done.append(target)
        reveal(done)
    elif op.id == "act_float":
        FloatShelf(paths).show()
    elif op.id == "act_share":
        open_with(paths[0])


class FloatShelf(QWidget):
    """A small always-on-top card holding files; drag them out to drop anywhere."""

    keep: list["FloatShelf"] = []

    def __init__(self, paths: list[Path]) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.paths = paths
        card = QWidget(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        names = paths[0].name if len(paths) == 1 else f"{len(paths)} files"
        lbl = QLabel(f"📄  {names}")
        hint = QLabel("Drag me anywhere · double-click to close")
        hint.setObjectName("muted")
        lay.addWidget(lbl)
        lay.addWidget(hint)
        self.adjustSize()
        self.move(QCursor.pos())
        self._press = None
        FloatShelf.keep.append(self)

    def mousePressEvent(self, e) -> None:
        self._press = e.globalPosition().toPoint()

    def mouseMoveEvent(self, e) -> None:
        if self._press and (e.globalPosition().toPoint() - self._press).manhattanLength() > 8:
            drag = QDrag(self)
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(str(p)) for p in self.paths])
            drag.setMimeData(md)
            drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
            self._press = None

    def mouseDoubleClickEvent(self, e) -> None:
        self.close()
        if self in FloatShelf.keep:
            FloatShelf.keep.remove(self)
