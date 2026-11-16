"""
Base window — a reusable, modern frameless window with:

* Rounded corners (via inner card + QSS border-radius)
* Drop shadow on the card
* Click-to-drag moving
* Windows 11 style

All UI windows inherit from this.  The architecture separates the
transparent top-level (which hosts the shadow) from an inner ``QFrame``
card that carries the rounded background — this avoids the
mask/shadow conflict that plagues naive frameless implementations.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGraphicsDropShadowEffect,
)

from ui.theme import Palette, get_palette_from_config


def _always_on_top_setting() -> bool:
    """Return the user's always-on-top preference (default: enabled)."""
    try:
        from config.config_manager import load_config
        return bool(load_config().get("always_on_top", True))
    except Exception:
        return True


class BaseWindow(QWidget):
    """A frameless, rounded, shadowed, draggable window."""

    closed = Signal()

    def __init__(self, parent: Optional[QWidget] = None,
                 palette: Optional[Palette] = None) -> None:
        super().__init__(parent)
        self._palette = palette or get_palette_from_config()
        self._pressed_pos: Optional[QPoint] = None
        self._allow_move = True
        self._always_on_top = _always_on_top_setting()
        self._setup()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _setup(self) -> None:
        # Top-level: frameless + transparent so the card's shadow & rounded
        # corners are visible.  The always-on-top hint is optional so users
        # who dislike transfer windows covering other apps can turn it off.
        self._apply_window_flags()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        # Inner card with rounded corners
        from PySide6.QtWidgets import QFrame
        self.frame = QFrame()
        self.frame.setObjectName("windowFrame")
        self.frame.setStyleSheet(self._frame_qss())

        # Drop shadow on the card (visible in the transparent corners)
        self._shadow = QGraphicsDropShadowEffect()
        self._shadow.setBlurRadius(24)
        self._shadow.setXOffset(0)
        self._shadow.setYOffset(6)
        from PySide6.QtGui import QColor
        self._shadow.setColor(QColor(self._palette.shadow))
        self.frame.setGraphicsEffect(self._shadow)

        # Layout
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)
        self._main_layout.addWidget(self.frame)

        # Content area inside the card
        self._content_layout = QVBoxLayout(self.frame)
        self._content_layout.setContentsMargins(24, 24, 24, 24)
        self._content_layout.setSpacing(16)

    # ------------------------------------------------------------------
    # Window flags / always-on-top
    # ------------------------------------------------------------------
    def _apply_window_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Window | Qt.CustomizeWindowHint
        if self._always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def set_always_on_top(self, enabled: bool) -> None:
        """Turn the always-on-top hint on or off at runtime."""
        enabled = bool(enabled)
        if enabled == self._always_on_top:
            return
        was_visible = self.isVisible()
        self._always_on_top = enabled
        # Changing window flags on a visible widget hides it, so re-show it.
        self._apply_window_flags()
        if was_visible:
            self.show()

    @property
    def always_on_top(self) -> bool:
        return self._always_on_top

    def _frame_qss(self) -> str:
        p = self._palette
        radius = 16
        return f"""
        QFrame#windowFrame {{
            background: {p.surface};
            border-radius: {radius}px;
            border: 1px solid {p.border};
        }}
        """

    # ------------------------------------------------------------------
    # Drag support
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._allow_move:
            self._pressed_pos = event.globalPosition().toPoint()
            self._pressed_pos = QPoint(
                self._pressed_pos.x() - self.x(),
                self._pressed_pos.y() - self.y(),
            )
            self.setCursor(QCursor(Qt.SizeAllCursor))

    def mouseMoveEvent(self, event):
        if self._pressed_pos is not None and self._allow_move:
            pos = event.globalPosition().toPoint()
            self.move(QPoint(pos.x() - self._pressed_pos.x(),
                             pos.y() - self._pressed_pos.y()))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed_pos = None
            self.setCursor(QCursor(Qt.ArrowCursor))

    def leaveEvent(self, event):
        if self._pressed_pos is None:
            self.setCursor(QCursor(Qt.ArrowCursor))
        super().leaveEvent(event)

    # ------------------------------------------------------------------
    # Content API
    # ------------------------------------------------------------------
    def add_widget(self, widget) -> None:
        self._content_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._content_layout.addLayout(layout)

    @property
    def content_layout(self):
        return self._content_layout

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    @property
    def palette_obj(self) -> Palette:
        return self._palette

    def set_palette(self, p: Palette) -> None:
        self._palette = p
        self.frame.setStyleSheet(self._frame_qss())
        if self._shadow:
            from PySide6.QtGui import QColor
            self._shadow.setColor(QColor(p.shadow))
        self._apply_theme()

    def _apply_theme(self) -> None:
        """Override in subclasses to reapply styles on theme change."""
        pass

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def center_on_screen(self) -> None:
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2,
        )

    def allow_move(self, enabled: bool) -> None:
        """Enable/disable click-to-drag moving."""
        self._allow_move = enabled


def apply_always_on_top(enabled: bool) -> None:
    """Apply the always-on-top preference to every open :class:`BaseWindow`."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if isinstance(widget, BaseWindow):
            widget.set_always_on_top(enabled)
