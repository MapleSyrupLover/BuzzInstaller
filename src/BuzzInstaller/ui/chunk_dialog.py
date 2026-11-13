"""Chunk choice dialog shown before an upload when "Ask" is enabled.

The dialog keeps the decision simple: automatic, a couple of presets with
the resulting per-chunk size, or a fully custom chunk count / chunk size.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from core import chunking
from utils.file_utils import format_size


class ChunkChoiceDialog(QDialog):
    """Ask the user how a single upload should be split into chunks."""

    PRESETS = (2, 4, 8)

    def __init__(self, total_size: int, config: Optional[dict] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose chunking")
        self.setModal(True)
        self.setMinimumWidth(440)
        self._total = max(0, int(total_size))
        self._config = dict(config or {})
        self._choice: dict = {"mode": "auto"}
        self._preset_radios: dict[int, QRadioButton] = {}
        self._preset_labels: dict[int, QLabel] = {}
        self._build_ui()
        self._update_preview()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)

        title = QLabel("How should this upload be split?")
        title.setObjectName("cardTitle")
        outer.addWidget(title)

        subtitle = QLabel(
            f"This upload is {format_size(self._total)}. Chunked uploads can "
            "resume if the connection drops."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

        self.radio_auto = QRadioButton(
            "Automatic  ·  use the chunk size from Settings"
        )
        self.radio_auto.setChecked(True)
        self.group.addButton(self.radio_auto, 0)
        outer.addWidget(self.radio_auto)

        for count in self.PRESETS:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            radio = QRadioButton(f"Split into {count} chunks")
            self.group.addButton(radio, count)
            self._preset_radios[count] = radio
            row_layout.addWidget(radio)
            hint = QLabel("")
            hint.setObjectName("caption")
            row_layout.addWidget(hint)
            row_layout.addStretch(1)
            outer.addWidget(row)
            self._preset_labels[count] = hint

        self.radio_custom = QRadioButton("Custom")
        self.group.addButton(self.radio_custom, 99)
        outer.addWidget(self.radio_custom)

        custom = QWidget()
        custom_layout = QHBoxLayout(custom)
        custom_layout.setContentsMargins(24, 0, 0, 0)
        custom_layout.setSpacing(8)
        custom_layout.addWidget(QLabel("Chunks"))
        self.spin_count = QSpinBox()
        self.spin_count.setRange(1, 64)
        self.spin_count.setValue(4)
        self.spin_count.setMinimumWidth(78)
        custom_layout.addWidget(self.spin_count)
        custom_layout.addSpacing(10)
        custom_layout.addWidget(QLabel("Chunk size"))
        self.combo_size = QComboBox()
        for label, size in (
            ("10 MB", 10 * 1024**2),
            ("50 MB", 50 * 1024**2),
            ("100 MB", 100 * 1024**2),
            ("250 MB", 250 * 1024**2),
            ("500 MB", 500 * 1024**2),
            ("1 GB", 1024**3),
            ("2 GB", 2 * 1024**3),
            ("5 GB", 5 * 1024**3),
        ):
            self.combo_size.addItem(label, size)
        self.combo_size.setMinimumWidth(110)
        custom_layout.addWidget(self.combo_size)
        custom_layout.addStretch(1)
        outer.addWidget(custom)

        self.preview = QLabel("")
        self.preview.setObjectName("muted")
        self.preview.setWordWrap(True)
        outer.addWidget(self.preview)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("secondary")
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(btn_cancel)
        btn_ok = QPushButton("Upload")
        btn_ok.setObjectName("primary")
        btn_ok.setCursor(Qt.PointingHandCursor)
        btn_ok.clicked.connect(self._accept)
        buttons.addWidget(btn_ok)
        outer.addLayout(buttons)

        # Wire changes last so the initial state is clean.
        self.group.idToggled.connect(lambda *_: self._update_preview())
        self.spin_count.valueChanged.connect(self._sync_size_from_count)
        self.combo_size.currentIndexChanged.connect(self._sync_count_from_size)

    # ------------------------------------------------------------------
    def _selected_count(self) -> Optional[int]:
        checked = self.group.checkedId()
        if checked in self.PRESETS:
            return checked
        if checked == 99:
            return self.spin_count.value()
        return None

    def _sync_size_from_count(self, _value: int = 0) -> None:
        if self.radio_custom.isChecked() and self._total > 0:
            size = chunking.chunk_size_for_count(self._total, self.spin_count.value())
            index = self._closest_size_index(size)
            self.combo_size.blockSignals(True)
            self.combo_size.setCurrentIndex(index)
            self.combo_size.blockSignals(False)
        self._update_preview()

    def _sync_count_from_size(self, _index: int = 0) -> None:
        if self.radio_custom.isChecked() and self._total > 0:
            size = self._custom_chunk_size()
            count = max(1, chunking.chunk_count(self._total, size))
            self.spin_count.blockSignals(True)
            self.spin_count.setValue(count)
            self.spin_count.blockSignals(False)
        self._update_preview()

    def _closest_size_index(self, size: int) -> int:
        best = 0
        best_delta = None
        for index in range(self.combo_size.count()):
            value = int(self.combo_size.itemData(index))
            delta = abs(value - size)
            if best_delta is None or delta < best_delta:
                best, best_delta = index, delta
        return best

    def _custom_chunk_size(self) -> int:
        return int(self.combo_size.currentData() or 10 * 1024**2)

    def _chunk_size(self) -> int:
        if self.radio_custom.isChecked():
            return chunking.clamp_chunk_size(self._custom_chunk_size())
        count = self._selected_count()
        if count is not None and self.radio_custom.isChecked() is False and count in self.PRESETS:
            return chunking.chunk_size_for_count(self._total, count)
        return chunking.chunk_size_from_config(self._config)

    def _update_preview(self, *_args) -> None:
        custom = self.radio_custom.isChecked()
        self.spin_count.setEnabled(custom)
        self.combo_size.setEnabled(custom)

        for count, label in self._preset_labels.items():
            size = chunking.chunk_size_for_count(self._total, count)
            label.setText(f"≈ {format_size(size)} each")

        size = self._chunk_size()
        count, last = chunking.describe_chunk_plan(self._total, size)
        if count == 0:
            self.preview.setText("The chosen files are empty.")
            return
        tail = f" · last chunk {format_size(last)}" if last != size else ""
        self.preview.setText(
            f"{count} chunk{'s' if count != 1 else ''} × "
            f"{format_size(size)}{tail}"
        )

    def _accept(self) -> None:
        if self.radio_custom.isChecked():
            self._choice = {
                "mode": "custom",
                "chunk_size": chunking.clamp_chunk_size(self._custom_chunk_size()),
            }
        else:
            count = self._selected_count()
            if self.radio_auto.isChecked() or count is None:
                self._choice = {"mode": "auto"}
            else:
                self._choice = {"mode": "count", "count": count}
        self.accept()

    # ------------------------------------------------------------------
    def choice(self) -> dict:
        """Return ``{"mode": ..., "count"/"chunk_size": ...}``."""
        return dict(self._choice)


def choose_chunks(total_size: int, config: Optional[dict] = None,
                  parent=None) -> Optional[dict]:
    """Show the dialog and return the selected choice (None = cancelled)."""
    dialog = ChunkChoiceDialog(total_size, config, parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.choice()
