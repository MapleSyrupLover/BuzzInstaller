"""
CloseButton — a properly-drawn X close button.

The previous implementation used the Unicode character "✕" (U+2715),
which renders inconsistently across fonts and often appears thin,
misaligned, or "misformed".  This widget draws the X with QPainter so
it is always perfectly symmetric and crisp on every system.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QPushButton

from ui.theme import Palette


class CloseButton(QPushButton):
    """A circular close (X) button drawn with QPainter."""

    def __init__(self, palette: Palette, size: int = 32,
                 parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._hovered = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Close")
        # Transparent background — we paint everything ourselves.
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        size = self.width()
        # Circle background on hover
        if self._hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self._palette.error))
            painter.drawEllipse(QRectF(1, 1, size - 2, size - 2))

        # Draw the X — two crossing lines, perfectly centered.
        color = QColor(self._palette.text_tertiary)
        if self._hovered:
            color = QColor("#FFFFFF")

        pen = QPen(color)
        pen.setWidthF(max(1.5, size / 16.0))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)

        # Padding so the X doesn't touch the circle edge
        pad = size * 0.28
        x1 = QPointF(pad, pad)
        y1 = QPointF(size - pad, size - pad)
        x2 = QPointF(size - pad, pad)
        y2 = QPointF(pad, size - pad)

        painter.drawLine(x1, y1)
        painter.drawLine(x2, y2)

        painter.end()