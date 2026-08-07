"""
Result window — shown after a successful upload.

Layout::

    ┌──────────────────────────────────────────┐
    │                                    ✕    │
    ├──────────────────────────────────────────┤
    │             ✔ Upload Complete            │
    │                                          │
    │   BZ://h0l6lgmru052                      │
    │   Copied to clipboard                    │
    │                                          │
    │ [ Copy Again ] [ Open BuzzHeavier ] [Close]│
    └──────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QHBoxLayout,
)

from ui.base_window import BaseWindow
from ui.theme import Palette, button_qss
from ui.close_button import CloseButton
from core.buzzheavier_api import BASE_URL
from utils.logging import get_logger

logger = get_logger(__name__)


class ResultWindow(BaseWindow):
    """Post-upload success dialog."""

    copy_requested = Signal()
    open_requested = Signal(str)   # emits the URL
    close_requested = Signal()

    def __init__(self, bz_url: str = "", buzzheavier_url: str = "",
                 file_name: str = "", file_size_str: str = "",
                 palette: Optional[Palette] = None) -> None:
        super().__init__(palette=palette)
        self.bz_url = bz_url or ""
        if buzzheavier_url:
            self.buzzheavier_url = buzzheavier_url
        else:
            code = self._extract_code()
            self.buzzheavier_url = f"{BASE_URL}/{code}"
        self._status_lbl: Optional[QLabel] = None
        self._build_ui()
        self.adjustSize()
        self.center_on_screen()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _extract_code(self) -> str:
        if "://" in self.bz_url:
            return self.bz_url.split("://", 1)[1]
        return self.bz_url

    def _make_title_bar(self, title: str) -> QWidget:
        """Draggable title bar with a close (✕) button."""
        p = self._palette
        bar = QWidget()
        bar.setFixedHeight(32)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(0)

        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {p.text_secondary}; font-size: 12px; font-weight: 500;"
        )
        layout.addWidget(lbl)
        layout.addStretch()

        btn_close = CloseButton(p)
        btn_close.clicked.connect(self._on_close)
        layout.addWidget(btn_close)

        bar.mousePressEvent = self._title_mouse_press
        return bar

    def _make_code_box(self, text: str, p: Palette) -> QWidget:
        """A styled, selectable box containing the BZ:// code."""
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        lbl_code = QLabel(text)
        lbl_code.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        lbl_code.setStyleSheet(
            "color: {c}; font-family: 'Consolas', 'Courier New', monospace; "
            "font-size: 14px; font-weight: 500;".format(c=p.text_primary)
        )
        layout.addWidget(lbl_code)

        box.setStyleSheet(
            f"background: {p.hover_bg}; border-radius: 10px; "
            f"border: 1px solid {p.border};"
        )
        return box

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------
    def _build_ui(self):
        p = self._palette
        cl = self.content_layout

        cl.addWidget(self._make_title_bar("Quick Send"))

        # Checkmark
        check = QLabel("✔")
        check.setStyleSheet(
            f"color: {p.success}; font-size: 48px; font-weight: 700;"
        )
        check.setAlignment(Qt.AlignCenter)
        cl.addWidget(check)

        # "Upload Complete"
        lbl_done = QLabel("Upload Complete")
        lbl_done.setStyleSheet(
            f"color: {p.text_primary}; font-size: 18px; font-weight: 600;"
        )
        lbl_done.setAlignment(Qt.AlignCenter)
        cl.addWidget(lbl_done)

        # BZ:// code box
        cl.addWidget(self._make_code_box(self.bz_url, p))

        # Status note
        self._status_lbl = QLabel("Link copied to clipboard")
        self._status_lbl.setStyleSheet(
            f"color: {p.text_secondary}; font-size: 12px;"
        )
        self._status_lbl.setAlignment(Qt.AlignCenter)
        cl.addWidget(self._status_lbl)

        # Buttons
        buttons = QHBoxLayout()
        buttons.setSpacing(12)

        btn_copy = QPushButton("Copy Again")
        btn_copy.setStyleSheet(button_qss(p, "secondary"))
        btn_copy.setFixedHeight(36)
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(self._on_copy)
        buttons.addWidget(btn_copy)

        btn_open = QPushButton("Open BuzzHeavier Page")
        btn_open.setStyleSheet(button_qss(p, "primary"))
        btn_open.setFixedHeight(36)
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.clicked.connect(self._on_open)
        buttons.addWidget(btn_open)

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(button_qss(p, "secondary"))
        btn_close.setFixedHeight(36)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self._on_close)
        buttons.addWidget(btn_close)

        cl.addLayout(buttons)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_copy(self):
        from system.clipboard import copy_to_clipboard
        ok = False
        try:
            ok = copy_to_clipboard(self.bz_url) is True
        except Exception as exc:
            logger.warning("Copy Again failed: %s", exc)
        self.copy_requested.emit()
        if self._status_lbl:
            if ok:
                self._status_lbl.setText("Copied to clipboard.")
            else:
                self._status_lbl.setText("Copy failed — please copy manually.")

    def _on_open(self):
        import webbrowser
        webbrowser.open(self.buzzheavier_url)
        self.open_requested.emit(self.buzzheavier_url)

    def _on_close(self):
        self.close_requested.emit()
        self.close()

    # ------------------------------------------------------------------
    # Drag support (same pattern as BaseWindow)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Public update API
    # ------------------------------------------------------------------
    def set_bz_url(self, url: str):
        self.bz_url = url
        code = self._extract_code() if url else ""
        self.buzzheavier_url = f"{BASE_URL}/{code}" if code else ""
