"""
Error dialog — a modal, frameless dialog for showing user-friendly
error messages.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
)

from ui.base_window import BaseWindow
from ui.theme import Palette, button_qss


class ErrorDialog(BaseWindow):
    """Modal error/info dialog."""

    closed = Signal()

    def __init__(self, title: str = "Error",
                 message: str = "",
                 info: bool = False,
                 palette: Optional[Palette] = None) -> None:
        super().__init__(palette=palette)
        self.setWindowModality(Qt.ApplicationModal)
        self._title_text = title
        self._message = message
        self._is_info = info
        self._build_ui()
        self.adjustSize()
        self.center_on_screen()

    def _build_ui(self):
        p = self._palette
        cl = self.content_layout

        # Title bar
        cl.addWidget(self._make_title_bar(self._title_text))

        # Icon + message row
        row = QHBoxLayout()
        icon = QLabel("⚠" if self._is_info else "✖")
        icon.setStyleSheet(
            f"color: {p.error}; font-size: 32px; font-weight: 700;"
        )
        icon.setFixedSize(40, 40)
        row.addWidget(icon)

        lbl_msg = QLabel(self._message)
        lbl_msg.setWordWrap(True)
        lbl_msg.setStyleSheet(
            f"color: {p.text_primary}; font-size: 13px; line-height: 1.4;"
        )
        lbl_msg.setMinimumWidth(300)
        row.addWidget(lbl_msg, 1)

        cl.addLayout(row)

        # Buttons
        btns = QHBoxLayout()
        btns.addStretch()

        btn_ok = QPushButton("OK")
        btn_ok.setStyleSheet(button_qss(p, "primary"))
        btn_ok.setFixedHeight(36)
        btn_ok.setCursor(Qt.PointingHandCursor)
        btn_ok.clicked.connect(self._on_ok)
        btns.addWidget(btn_ok)

        cl.addLayout(btns)

    def _make_title_bar(self, title: str) -> QWidget:
        p = self._palette
        bar = QWidget()
        bar.setFixedHeight(32)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 0, 0, 0)

        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {p.text_secondary}; font-size: 12px; font-weight: 500;"
        )
        layout.addWidget(lbl)
        layout.addStretch()

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(32, 32)
        btn_close.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {p.text_tertiary};
                border: none;
                font-size: 14px; font-weight: 400;
            }}
            QPushButton:hover {{
                color: white; background: {p.error}; border-radius: 10px;
            }}
        """)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self._on_ok)
        layout.addWidget(btn_close)

        bar.mousePressEvent = self._title_mouse_press
        return bar

    def _on_ok(self):
        self.closed.emit()
        self.close()

    def _title_mouse_press(self, event):
        if self._allow_move and event.button() == Qt.LeftButton:
            self._pressed_pos = event.globalPosition().toPoint()
            self._pressed_pos = QPoint(
                self._pressed_pos.x() - self.x(),
                self._pressed_pos.y() - self.y(),
            )
            self.setCursor(QCursor(Qt.SizeAllCursor))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed_pos = None
            self.setCursor(QCursor(Qt.ArrowCursor))
        super().mouseReleaseEvent(event)
