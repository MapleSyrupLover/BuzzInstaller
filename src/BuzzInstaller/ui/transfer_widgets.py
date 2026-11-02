"""Shared widgets for resumable (chunked) transfers.

The Send and Receive pages both need the same pieces:

* a progress panel with byte counters, speed, ETA and per-chunk state;
* a list of interrupted transfers with Resume/Remove actions.

Keeping them here avoids duplicating layout and state-handling logic.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Iterable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ui.widgets import Card, NestedCard, clear_layout, divider
from utils.file_utils import format_size

_GLYPHS = {
    "pending": "•",
    "uploading": "↑",
    "downloading": "↓",
    "retrying": "↻",
    "completed": "✓",
    "failed": "✕",
}


def _format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m {seconds % 60}s"
    return f"{minutes // 60}h {minutes % 60}m"


class ChunkStatusList(QWidget):
    """A scrollable list of per-chunk states."""

    MAX_ROWS = 400

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: dict[int, QLabel] = {}
        self._empty: Optional[QLabel] = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self.reset(0)

    def reset(self, total_chunks: int = 0) -> None:
        clear_layout(self._layout)
        self._rows.clear()
        self._empty = QLabel("Chunk details will appear here.")
        self._empty.setObjectName("caption")
        self._layout.addWidget(self._empty)
        if total_chunks:
            self._ensure_rows(min(int(total_chunks), self.MAX_ROWS))

    def _ensure_rows(self, count: int) -> None:
        if count <= 0:
            return
        if self._empty is not None:
            self._empty.deleteLater()
            self._empty = None
        for index in range(len(self._rows), count):
            label = QLabel(f"• Chunk {index + 1}: pending")
            label.setObjectName("caption")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._layout.addWidget(label)
            self._rows[index] = label

    def set_chunk(self, index: int, state: str, detail: str = "") -> None:
        index = int(index)
        if index < 0 or index >= self.MAX_ROWS:
            return
        self._ensure_rows(index + 1)
        label = self._rows.get(index)
        if label is None:
            return
        glyph = _GLYPHS.get(str(state), "•")
        text = f"{glyph} Chunk {index + 1}: {state}"
        if detail and state != "completed":
            text += f" — {detail}"
        label.setText(text)
        label.setToolTip(str(detail or ""))

    def set_total(self, total_chunks: int) -> None:
        self._ensure_rows(min(max(0, int(total_chunks)), self.MAX_ROWS))


class TransferProgressCard(Card):
    """Progress panel used while an upload or download is running."""

    pauseRequested = Signal()
    resumeRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, title: str = "Transferring", parent=None) -> None:
        super().__init__(title, "", parent)
        self._samples: deque = deque()
        self._done = 0
        self._total = 0
        self._total_chunks = 0
        self._completed_chunks = 0
        self._completed_indexes: set[int] = set()
        self._paused = False
        self._pausable = False

        self.name_label = QLabel("")
        self.name_label.setStyleSheet("font-weight: 600;")
        self.name_label.setWordWrap(True)
        self.body.addWidget(self.name_label)

        self.status_label = QLabel("Preparing…")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.body.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        self.body.addWidget(self.progress_bar)

        detail_row = QHBoxLayout()
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("caption")
        self.chunk_summary = QLabel("")
        self.chunk_summary.setObjectName("caption")
        detail_row.addWidget(self.detail_label)
        detail_row.addStretch(1)
        detail_row.addWidget(self.chunk_summary)
        self.body.addLayout(detail_row)

        self.chunk_scroll = QScrollArea()
        self.chunk_scroll.setWidgetResizable(True)
        self.chunk_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chunk_scroll.setMaximumHeight(170)
        self.chunk_scroll.setVisible(False)
        self.chunk_list = ChunkStatusList()
        self.chunk_scroll.setWidget(self.chunk_list)
        self.body.addWidget(self.chunk_scroll)

        self.body.addWidget(divider())
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setObjectName("secondary")
        self.btn_pause.setCursor(Qt.PointingHandCursor)
        self.btn_pause.clicked.connect(self.pauseRequested.emit)
        buttons.addWidget(self.btn_pause)

        self.btn_resume = QPushButton("Resume")
        self.btn_resume.setObjectName("primary")
        self.btn_resume.setCursor(Qt.PointingHandCursor)
        self.btn_resume.setVisible(False)
        self.btn_resume.clicked.connect(self.resumeRequested.emit)
        buttons.addWidget(self.btn_resume)

        buttons.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("ghost")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.cancelRequested.emit)
        buttons.addWidget(self.btn_cancel)
        self.body.addLayout(buttons)

    # -- lifecycle ------------------------------------------------------
    def begin(self, name: str, size: int = 0, total_chunks: int = 0) -> None:
        self._samples.clear()
        self._done = 0
        self._total = max(0, int(size))
        self._total_chunks = max(0, int(total_chunks))
        self._completed_chunks = 0
        self._completed_indexes.clear()
        self._paused = False
        self._pausable = False
        self.name_label.setText(name or "")
        self.status_label.setText("Preparing…")
        self.progress_bar.setRange(0, 100 if self._total > 0 else 0)
        self.progress_bar.setValue(0)
        self.detail_label.setText("")
        self.chunk_summary.setText("")
        self.chunk_list.reset(self._total_chunks)
        self.chunk_scroll.setVisible(self._total_chunks > 0)
        self._set_paused_ui(False)
        self.btn_cancel.setEnabled(True)

    def set_status(self, text: str) -> None:
        self.status_label.setText(str(text or ""))

    def set_size(self, size: int) -> None:
        self._total = max(0, int(size))
        if self._total > 0 and self._done == 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)

    def set_total_chunks(self, total_chunks: int) -> None:
        self._total_chunks = max(0, int(total_chunks))
        self.chunk_list.set_total(self._total_chunks)
        self.chunk_scroll.setVisible(self._total_chunks > 0)
        self._update_chunk_summary()

    def set_chunk(self, index: int, state: str, detail: str = "") -> None:
        if index + 1 > self._total_chunks:
            self._total_chunks = index + 1
        self.chunk_list.set_chunk(index, state, detail)
        self.chunk_scroll.setVisible(True)
        if state == "completed":
            self._completed_indexes.add(int(index))
            self._completed_chunks = len(self._completed_indexes)
        self._update_chunk_summary()

    def update_progress(self, done: int, total: int) -> None:
        done = max(0, int(done))
        total = max(0, int(total))
        if total > 0:
            self._total = total
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(min(100, int(done * 100 / total)))
        else:
            self.progress_bar.setRange(0, 0)
        self._done = done

        now = time.monotonic()
        self._samples.append((now, done))
        cutoff = now - 3.0
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        rate = 0.0
        if len(self._samples) >= 2:
            oldest_t, oldest_b = self._samples[0]
            if now > oldest_t:
                rate = max(0.0, (done - oldest_b) / (now - oldest_t))

        parts = [
            f"{format_size(done)} / {format_size(total)}"
            if total > 0 else format_size(done)
        ]
        if rate > 0:
            parts.append(f"{format_size(int(rate))}/s")
            if total > done > 0:
                parts.append(f"ETA {_format_eta((total - done) / rate)}")
        self.detail_label.setText(" · ".join(parts))

    def set_paused(self, paused: bool = True) -> None:
        self._paused = bool(paused)
        self._set_paused_ui(self._paused)

    def set_pausable(self, pausable: bool = True) -> None:
        """Show the Pause control once a resumable transfer is running."""
        self._pausable = bool(pausable)
        self._set_paused_ui(self._paused)

    def _set_paused_ui(self, paused: bool) -> None:
        self.btn_pause.setVisible(bool(self._pausable and not paused))
        self.btn_pause.setEnabled(bool(self._pausable and not paused))
        self.btn_resume.setVisible(paused)
        self.btn_resume.setEnabled(paused)
        self.btn_cancel.setEnabled(not paused)

    def finish(self, status: str = "") -> None:
        if status:
            self.status_label.setText(status)
        self.btn_pause.setVisible(False)
        self.btn_resume.setVisible(False)
        self.btn_cancel.setVisible(False)

    def _update_chunk_summary(self) -> None:
        if self._total_chunks <= 0:
            self.chunk_summary.setText("")
            return
        self.chunk_summary.setText(
            f"Chunks {min(self._completed_chunks, self._total_chunks)}"
            f"/{self._total_chunks}"
        )


class InterruptedTransfersCard(Card):
    """Lists paused/interrupted/failed transfers with resume actions."""

    resumeRequested = Signal(str)
    removeRequested = Signal(str)

    def __init__(self, direction: str, parent=None) -> None:
        super().__init__(
            "Interrupted transfers",
            "Verified chunks are kept. Resume to continue where you left off.",
        )
        self._direction = str(direction)
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(8)
        self.body.addLayout(self.rows_layout)
        self.setVisible(False)

    def refresh(self, transfers: Optional[Iterable[dict]] = None) -> None:
        if transfers is None:
            try:
                from core.transfer_db import default_store
                transfers = default_store().interrupted(self._direction)
            except Exception:
                transfers = []
        items = list(transfers or [])
        clear_layout(self.rows_layout)
        self.setVisible(bool(items))
        if not items:
            return

        for transfer in items:
            self.rows_layout.addWidget(self._make_row(transfer))

    def _make_row(self, transfer: dict) -> QWidget:
        card = NestedCard()
        top = QHBoxLayout()
        top.setSpacing(8)

        name = QLabel(str(transfer.get("name") or "Transfer"))
        name.setStyleSheet("font-weight: 600;")
        name.setWordWrap(True)
        top.addWidget(name, 1)

        size = int(transfer.get("size", 0) or 0)
        completed = int(transfer.get("completed_chunks", 0) or 0)
        total = int(transfer.get("total_chunks", 0) or 0)
        status = str(transfer.get("status", "") or "")
        meta = QLabel(
            f"{format_size(size)} · {completed}/{total} chunks · {status}"
        )
        meta.setObjectName("caption")
        top.addWidget(meta)
        card.body.addLayout(top)

        error = str(transfer.get("last_error", "") or "")
        if error:
            error_label = QLabel(error)
            error_label.setObjectName("caption")
            error_label.setWordWrap(True)
            card.body.addWidget(error_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        uuid = str(transfer.get("uuid", "") or "")
        resume = QPushButton("Resume")
        resume.setObjectName("primary")
        resume.setCursor(Qt.PointingHandCursor)
        resume.clicked.connect(
            lambda _=False, u=uuid: self.resumeRequested.emit(u)
        )
        buttons.addWidget(resume)

        remove = QPushButton("Remove")
        remove.setObjectName("ghost")
        remove.setCursor(Qt.PointingHandCursor)
        remove.clicked.connect(
            lambda _=False, u=uuid: self.removeRequested.emit(u)
        )
        buttons.addWidget(remove)
        card.body.addLayout(buttons)
        return card
