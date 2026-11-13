"""
Reusable widgets for the BuzzInstaller dashboard.

Everything here is styled through the global application stylesheet
(``ui.theme.app_qss``) so widgets pick up live theme changes without any
per-widget style strings.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QSlider, QSpinBox, QStyle, QStyleOptionButton, QVBoxLayout, QWidget,
)


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFrameShape(QFrame.NoFrame)
    return line


def clear_layout(layout) -> None:
    """Delete all children from a layout."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


class PageHeader(QWidget):
    """Large page title with an optional subtitle and right-side widget."""

    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("pageTitle")
        texts.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setObjectName("pageSubtitle")
        self.subtitle_label.setWordWrap(True)
        texts.addWidget(self.subtitle_label)
        row.addLayout(texts)
        row.addStretch(1)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        row.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)


class Card(QFrame):
    """A rounded surface card with an optional title and header action."""

    def __init__(self, title: str = "", description: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(12)

        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        # Parent the labels to the card *before* toggling visibility.
        # A parentless QLabel is a top-level window, so setVisible() would
        # briefly flash a small native window during construction.
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("cardTitle")
        self.title_label.setVisible(bool(title))
        text_col.addWidget(self.title_label)
        self.description_label = QLabel(description, self)
        self.description_label.setObjectName("muted")
        self.description_label.setWordWrap(True)
        self.description_label.setVisible(bool(description))
        text_col.addWidget(self.description_label)
        self.header.addLayout(text_col)
        self.header.addStretch(1)
        outer.addLayout(self.header)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)

    def add_header_widget(self, widget: QWidget) -> None:
        self.header.addWidget(widget)


class NestedCard(QFrame):
    """A slightly inset card used inside another card."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("nestedCard")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)


class StatCard(Card):
    """Small metric card for the dashboard."""

    def __init__(self, label: str, value: str = "—", hint: str = "",
                 parent=None) -> None:
        super().__init__(parent=parent)
        self.title_label.setVisible(False)
        self.description_label.setVisible(False)
        self.body.setSpacing(2)

        self.value_label = QLabel(value, self)
        self.value_label.setObjectName("bigValue")
        self.label = QLabel(label, self)
        self.label.setObjectName("muted")
        self.hint_label = QLabel(hint, self)
        self.hint_label.setObjectName("caption")
        self.hint_label.setVisible(bool(hint))

        self.body.addWidget(self.value_label)
        self.body.addWidget(self.label)
        self.body.addWidget(self.hint_label)

    def set_value(self, value: str, hint: Optional[str] = None) -> None:
        self.value_label.setText(value)
        if hint is not None:
            self.hint_label.setText(hint)
            self.hint_label.setVisible(bool(hint))


class Badge(QLabel):
    """Small rounded status pill."""

    def __init__(self, text: str = "", tone: str = "neutral", parent=None) -> None:
        super().__init__(text, parent)
        self.set_tone(tone)
        self.setAlignment(Qt.AlignCenter)

    def set_tone(self, tone: str) -> None:
        from ui.theme import get_palette_from_config
        p = get_palette_from_config()
        colors = {
            "neutral": (p.surface_alt, p.text_secondary, p.border),
            "accent": (p.accent_soft, p.accent, p.accent),
            "success": ("rgba(21, 128, 61, 0.14)", p.success, p.success),
            "warning": ("rgba(180, 83, 9, 0.14)", p.warning, p.warning),
            "error": ("rgba(220, 38, 38, 0.14)", p.error, p.error),
        }
        bg, fg, border = colors.get(tone, colors["neutral"])
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid {border};"
            "border-radius: 9px; padding: 2px 9px; font-size: 11px;"
            "font-weight: 600;"
        )


class NavButton(QPushButton):
    """Sidebar navigation button with a leading glyph."""

    def __init__(self, glyph: str, text: str, parent=None) -> None:
        super().__init__(f"{glyph}   {text}", parent)
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(40)


class CheckBox(QCheckBox):
    """Checkbox that keeps the themed indicator *and* draws a checkmark.

    A plain QSS-styled indicator loses the native tick, leaving only a colour
    change.  Painting the tick ourselves keeps the look consistent across
    Windows themes and both app palettes.
    """

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.isChecked():
            return

        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(
            QStyle.SE_CheckBoxIndicator, option, self
        )
        if not rect.isValid():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor("#FFFFFF"), 2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        x, y = rect.x(), rect.y()
        width, height = rect.width(), rect.height()
        painter.drawLine(
            int(x + width * 0.24), int(y + height * 0.53),
            int(x + width * 0.43), int(y + height * 0.72),
        )
        painter.drawLine(
            int(x + width * 0.43), int(y + height * 0.72),
            int(x + width * 0.78), int(y + height * 0.30),
        )
        painter.end()


class DropZone(QFrame):
    """A dashed drop target that accepts files and folders via drag & drop."""

    filesDropped = Signal(list)
    browseRequested = Signal()

    def __init__(self, title: str = "Drop files here",
                 subtitle: str = "or click to browse — folders are zipped automatically",
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(150)
        self.setProperty("dragActive", False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignCenter)

        icon = QLabel("⬆", self)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 30px;")
        layout.addWidget(icon)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("cardTitle")
        self.title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_label)

        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setObjectName("muted")
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

    # -- drag & drop -----------------------------------------------------
    def _set_active(self, active: bool) -> None:
        if self.property("dragActive") != active:
            self.setProperty("dragActive", active)
            style = self.style()
            style.unpolish(self)
            style.polish(self)
            self.update()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_active(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_active(False)
        paths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            event.acceptProposedAction()
            self.filesDropped.emit(paths)
        else:
            event.ignore()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.browseRequested.emit()
        super().mouseReleaseEvent(event)


class Toast(QFrame):
    """A lightweight in-window toast notification."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setVisible(False)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 9, 14, 9)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        row.addWidget(self.label)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, error: bool = False,
                     timeout_ms: int = 3200) -> None:
        from ui.theme import get_palette_from_config
        p = get_palette_from_config()
        color = p.error if error else p.success
        self.label.setText(text)
        self.label.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.setStyleSheet(
            f"QFrame#card {{ background: {p.surface}; border: 1px solid {color};"
            "border-radius: 10px; }}"
        )
        self.adjustSize()
        parent = self.parentWidget()
        if parent:
            width = min(max(self.sizeHint().width(), 240), max(parent.width() - 60, 240))
            self.setFixedWidth(width)
            self.adjustSize()
            self.move(
                (parent.width() - self.width()) // 2,
                parent.height() - self.height() - 26,
            )
        self.raise_()
        self.setVisible(True)
        self._timer.start(timeout_ms)


# ---------------------------------------------------------------------------
# Settings controls
# ---------------------------------------------------------------------------
class SettingRow(QWidget):
    """Label + description on the left, a control on the right."""

    def __init__(self, title: str, description: str = "",
                 control: Optional[QWidget] = None, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(16)

        text = QVBoxLayout()
        text.setSpacing(1)
        label = QLabel(title, self)
        label.setStyleSheet("font-weight: 600;")
        text.addWidget(label)
        if description:
            hint = QLabel(description, self)
            hint.setObjectName("caption")
            hint.setWordWrap(True)
            text.addWidget(hint)
        row.addLayout(text, 1)

        if control is not None:
            control.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            row.addWidget(control, 0, Qt.AlignVCenter)


class SliderSpin(QWidget):
    """A slider paired with a directly editable number box.

    The slider gives fast coarse control; the spin box gives exact values
    and accepts typed input.  Both stay in sync and the whole row is
    comfortable to grab with the mouse.
    """

    valueChanged = Signal(int)

    def __init__(self, minimum: int, maximum: int, value: int = 0,
                 step: int = 1, suffix: str = "", width: int = 96,
                 parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(max(step, (maximum - minimum) // 10 or step))
        self.slider.setMinimumWidth(150)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.setValue(value)
        row.addWidget(self.slider, 1)

        self.spin = QSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setSingleStep(step)
        self.spin.setValue(value)
        self.spin.setSuffix(suffix)
        self.spin.setMinimumWidth(width)
        self.spin.setKeyboardTracking(False)
        row.addWidget(self.spin, 0)

        self.slider.valueChanged.connect(self._on_slider)
        self.spin.valueChanged.connect(self._on_spin)

    def _on_slider(self, value: int) -> None:
        if self.spin.value() != value:
            self.spin.blockSignals(True)
            self.spin.setValue(value)
            self.spin.blockSignals(False)
        self.valueChanged.emit(int(value))

    def _on_spin(self, value: int) -> None:
        if self.slider.value() != value:
            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)
        self.valueChanged.emit(int(value))

    def value(self) -> int:
        return int(self.spin.value())

    def setValue(self, value: int) -> None:
        self.slider.blockSignals(True)
        self.spin.blockSignals(True)
        self.slider.setValue(value)
        self.spin.setValue(value)
        self.slider.blockSignals(False)
        self.spin.blockSignals(False)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 (Qt style)
        super().setEnabled(enabled)
        self.slider.setEnabled(enabled)
        self.spin.setEnabled(enabled)


# ---------------------------------------------------------------------------
# Path helpers used by the pages
# ---------------------------------------------------------------------------
def human_paths_summary(paths: Iterable[str]) -> str:
    paths = list(paths)
    if not paths:
        return "No files selected"
    if len(paths) == 1:
        return os.path.basename(paths[0].rstrip("\\/"))
    return f"{len(paths)} items selected"
