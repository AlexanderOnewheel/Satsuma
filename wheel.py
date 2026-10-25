"""The radial drop wheel.

It appears under the pointer as an almost-invisible, hit-testable window.
Nothing is drawn until an OLE file drag actually enters it, so a Shift+
rubber-band selection in Explorer never shows a stray wheel. Once files
enter, the wheel fills with the operations for that file type; modifiers
held during the drag switch rings live:

    Shift           Convert
    Ctrl + Shift    Tools
    Alt + Shift     Actions
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QWidget

from .ops import Op, ops_for

R_OUT, R_IN, R_HUB = 168, 74, 62
PAD = 28
HINT_H = 34
MODES = ("convert", "tool", "action")
MODE_NAMES = {"convert": "Convert", "tool": "Tools", "action": "Actions"}

ACCENT = QColor(255, 140, 26)
BG = QColor(28, 28, 32, 236)
WEDGE = QColor(48, 48, 56, 240)
WEDGE_LINE = QColor(255, 255, 255, 26)
TEXT = QColor(240, 240, 244)
MUTED = QColor(160, 160, 170)


def mode_from_modifiers(mods) -> str:
    if mods & Qt.ControlModifier:
        return "tool"
    if mods & Qt.AltModifier:
        return "action"
    return "convert"


class Wheel(QWidget):
    chosen = Signal(object, list)          # (Op, [Path])

    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        side = 2 * (R_OUT + PAD)
        self.setFixedSize(side, side + HINT_H)
        self.center = QPointF(side / 2, side / 2)
        self.paths: list[Path] = []
        self.mode = "convert"
        self.items: list[Op] = []
        self.hover = -1
        self.live = False                  # True once files have entered
        self.forced_mode: str | None = None
        self._probe = QTimer(self, singleShot=True, interval=700, timeout=self._probe_expired)
        self._leave = QTimer(self, singleShot=True, interval=220, timeout=self.dismiss)
        self.font_glyph = QFont("Segoe UI", 10, QFont.DemiBold)
        self.font_hub = QFont("Segoe UI", 11, QFont.DemiBold)
        self.font_small = QFont("Segoe UI", 8)

    # ---- show / hide -------------------------------------------------------
    def arm(self, pos: QPoint | None = None) -> None:
        """Place the (still invisible) wheel under the pointer and wait for a drag."""
        pos = pos or QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = pos.x() - int(self.center.x())
        y = pos.y() - int(self.center.y())
        x = max(geo.left(), min(x, geo.right() - self.width()))
        y = max(geo.top(), min(y, geo.bottom() - self.height()))
        self.move(x, y)
        self.live, self.hover, self.items, self.paths = False, -1, [], []
        self.show()
        self.raise_()
        self._probe.start()
        self.update()

    def _probe_expired(self) -> None:
        if not self.live:
            self.hide()

    def dismiss(self) -> None:
        self._probe.stop()
        self.live = False
        self.hide()

    def drag_ended(self) -> None:
        """Mouse button released anywhere; give a pending drop a moment to arrive."""
        QTimer.singleShot(120, self.dismiss)

    # ---- drag & drop -------------------------------------------------------
    def _set_mode(self, mode: str) -> None:
        if mode != self.mode or not self.items and self.paths:
            self.mode = mode
            self.items = ops_for(self.paths, mode)
            self.hover = -1
            self.update()

    def dragEnterEvent(self, e) -> None:
        urls = [u for u in e.mimeData().urls() if u.isLocalFile()]
        if not urls:
            e.ignore()
            return
        self._leave.stop()
        self.paths = [Path(u.toLocalFile()) for u in urls]
        self.live = True
        self.mode = ""
        self._set_mode(self.forced_mode or mode_from_modifiers(e.modifiers()))
        e.setDropAction(Qt.CopyAction)     # never let Explorer treat this as a move
        e.accept()

    def dragMoveEvent(self, e) -> None:
        self._set_mode(self.forced_mode or mode_from_modifiers(e.modifiers()))
        p = e.position() if hasattr(e, "position") else QPointF(e.pos())
        h = self.index_at(p)
        if h != self.hover:
            self.hover = h
            self.update()
        e.setDropAction(Qt.CopyAction)
        e.accept()

    def dragLeaveEvent(self, e) -> None:
        self.hover = -1
        self.update()
        self._leave.start()               # pointer left the wheel: get out of the way

    def dropEvent(self, e) -> None:
        p = e.position() if hasattr(e, "position") else QPointF(e.pos())
        idx = self.index_at(p)
        paths = list(self.paths)
        e.setDropAction(Qt.CopyAction)
        e.accept()
        self.dismiss()
        if 0 <= idx < len(self.items):
            self.chosen.emit(self.items[idx], paths)

    # ---- geometry ------------------------------------------------------------
    def index_at(self, p: QPointF) -> int:
        n = len(self.items)
        if not n:
            return -1
        dx, dy = p.x() - self.center.x(), p.y() - self.center.y()
        r = math.hypot(dx, dy)
        if r < R_IN or r > R_OUT + PAD:
            return -1
        ang = (math.degrees(math.atan2(dx, -dy)) + 360) % 360   # 0° = up, clockwise
        span = 360 / n
        return int(((ang + span / 2) % 360) // span)

    # ---- painting ------------------------------------------------------------
    def paintEvent(self, _ev) -> None:
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        if not self.live:
            # 1/255 alpha keeps the layered window hit-testable for OLE drops
            qp.fillRect(self.rect(), QColor(0, 0, 0, 1))
            return
        c = self.center
        # soft shadow
        g = QRadialGradient(c, R_OUT + PAD)
        g.setColorAt(0.80, QColor(0, 0, 0, 70))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        qp.setPen(Qt.NoPen)
        qp.setBrush(g)
        qp.drawEllipse(c, R_OUT + PAD, R_OUT + PAD)
        qp.setBrush(BG)
        qp.drawEllipse(c, R_OUT + 6, R_OUT + 6)

        n = len(self.items)
        span = 360 / n if n else 360
        for i, item in enumerate(self.items):
            start = 90 - i * span + span / 2          # Qt angles: 0° = 3 o'clock, CCW
            path = QPainterPath()
            outer = QRectF(c.x() - R_OUT, c.y() - R_OUT, 2 * R_OUT, 2 * R_OUT)
            inner = QRectF(c.x() - R_IN, c.y() - R_IN, 2 * R_IN, 2 * R_IN)
            gap = 1.2 if n > 1 else 0
            path.arcMoveTo(outer, start - gap)
            path.arcTo(outer, start - gap, -(span - 2 * gap))
            path.arcTo(inner, start - span + gap, span - 2 * gap)
            path.closeSubpath()
            hot = i == self.hover
            qp.setPen(QPen(WEDGE_LINE, 1))
            qp.setBrush(ACCENT if hot else WEDGE)
            qp.drawPath(path)
            mid = math.radians(i * span)
            rr = (R_OUT + R_IN) / 2
            tx, ty = c.x() + rr * math.sin(mid), c.y() - rr * math.cos(mid)
            qp.setFont(self.font_glyph)
            qp.setPen(QColor(20, 20, 20) if hot else TEXT)
            label = item.glyph
            fm = QFontMetrics(self.font_glyph)
            max_w = 2 * rr * math.sin(math.radians(span / 2)) - 10 if n > 1 else 120
            label = fm.elidedText(label, Qt.ElideRight, int(max(36, max_w)))
            qp.drawText(QRectF(tx - 50, ty - 12, 100, 24), Qt.AlignCenter, label)

        # hub
        qp.setPen(Qt.NoPen)
        qp.setBrush(QColor(20, 20, 24, 250))
        qp.drawEllipse(c, R_HUB, R_HUB)
        qp.setPen(TEXT)
        qp.setFont(self.font_hub)
        if self.hover >= 0:
            title = self.items[self.hover].label
        elif not self.items:
            title = f"No {MODE_NAMES[self.mode].lower()}"
        else:
            title = MODE_NAMES[self.mode]
        qp.drawText(QRectF(c.x() - R_HUB + 6, c.y() - 30, 2 * R_HUB - 12, 40),
                    Qt.AlignCenter | Qt.TextWordWrap, title)
        qp.setFont(self.font_small)
        qp.setPen(MUTED)
        sub = (self.paths[0].name if len(self.paths) == 1 else f"{len(self.paths)} files") \
            if self.paths else ""
        sub = QFontMetrics(self.font_small).elidedText(sub, Qt.ElideMiddle, 2 * R_HUB - 18)
        qp.drawText(QRectF(c.x() - R_HUB, c.y() + 10, 2 * R_HUB, 20), Qt.AlignCenter, sub)

        # mode hint pill
        hint = "⇧ Convert    Ctrl+⇧ Tools    Alt+⇧ Actions"
        fm = QFontMetrics(self.font_small)
        w = fm.horizontalAdvance(hint) + 24
        pill = QRectF(c.x() - w / 2, self.height() - HINT_H + 4, w, 24)
        qp.setPen(Qt.NoPen)
        qp.setBrush(BG)
        qp.drawRoundedRect(pill, 12, 12)
        x = pill.x() + 12
        for part, m in (("⇧ Convert", "convert"), ("Ctrl+⇧ Tools", "tool"),
                        ("Alt+⇧ Actions", "action")):
            qp.setPen(ACCENT if m == self.mode else MUTED)
            qp.drawText(QRectF(x, pill.y(), fm.horizontalAdvance(part) + 2, 24),
                        Qt.AlignVCenter, part)
            x += fm.horizontalAdvance(part + "    ")
        qp.end()
