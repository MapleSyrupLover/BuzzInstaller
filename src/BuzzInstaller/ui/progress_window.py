"""
Progress window — a tiny, always-on-top dialog shown during upload or
download operations.

Provides:
* A title bar (draggable, with close button)
* A file-name / status label
* A styled progress bar with byte and percentage labels
* A real-time transfer-rate + ETA indicator
* A Cancel button

All display methods (``update_progress``, ``set_status``, ``finish_ok``,
``finish_error``) are safe to call from a worker thread — internally they
emit Qt signals which are queued to the GUI thread automatically.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QLabel, QProgressBar, QPushButton,
    QHBoxLayout, QVBoxLayout,
)

from ui.base_window import BaseWindow
from ui.theme import Palette, button_qss, progress_bar_qss
from utils.file_utils import format_size
from utils.logging import get_logger

logger = get_logger(__name__)

UPLOAD = "upload"
DOWNLOAD = "download"

TITLES = {UPLOAD: "Quick Send", DOWNLOAD: "Quick Receive"}


class ProgressWindow(BaseWindow):
    """Tiny progress dialog for uploads and downloads."""

    # Signals — emitted from any thread, auto-queued to the GUI thread.
    # NOTE: must be Signal(object, object), NOT Signal(int, int).
    # PySide6's Signal(int, int) maps to C++ 32-bit int, which overflows
    # for files > 2 GiB (3,221,225,472 > 2^31-1 = 2,147,483,647), making
    # totalBytes negative and the progress bar stuck at 0%.  Passing
    # Python ints as objects avoids any 32-bit truncation entirely.
    progress_update = Signal(object, object)
    status_update = Signal(str)
    finished_ok = Signal(object)
    finished_error = Signal(str, str)
    finished_cancelled = Signal()
    result_shown = Signal(object)
    error_shown = Signal(str, str)

    def __init__(self, mode: str = UPLOAD,
                 file_name: str = "",
                 palette: Optional[Palette] = None) -> None:
        super().__init__(palette=palette)
        self.mode = mode
        self._file_name = file_name
        self._cancel_requested = False
        self._total_bytes = 0
        self._last_emit_time = 0.0
        # Rolling window of (timestamp, uploadedBytes) samples used for
        # speed/ETA.  Entries older than 3 seconds are discarded.
        self._samples: deque = deque()

        self._build_ui()
        self._connect_signals()
        self.adjustSize()
        self.setMinimumWidth(400)
        self.center_on_screen()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        p = self._palette
        cl = self.content_layout

        # Title bar
        cl.addWidget(self._make_title_bar(TITLES.get(self.mode, "Progress")))

        # File name
        self.lbl_file = QLabel(self._file_name or "")
        self.lbl_file.setStyleSheet(f"color: {p.text_primary}; font-weight: 600; font-size: 14px;")
        cl.addWidget(self.lbl_file)

        # Status
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet(f"color: {p.text_secondary}; font-size: 13px;")
        cl.addWidget(self.lbl_status)

        # Progress bar
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        self.bar.setFixedHeight(10)
        self.bar.setStyleSheet(progress_bar_qss(p))
        cl.addWidget(self.bar)

        # Bytes + rate/eta row
        details = QHBoxLayout()
        self.lbl_bytes = QLabel("")
        self.lbl_bytes.setStyleSheet(f"color: {p.text_tertiary}; font-size: 11px;")
        self.lbl_rate_eta = QLabel("")
        self.lbl_rate_eta.setStyleSheet(f"color: {p.text_tertiary}; font-size: 11px;")
        details.addWidget(self.lbl_bytes)
        details.addStretch()
        details.addWidget(self.lbl_rate_eta)
        cl.addLayout(details)

        # Cancel button
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(button_qss(p, "secondary"))
        self.btn_cancel.setFixedHeight(36)
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        cl.addWidget(self.btn_cancel)

        # Default status
        self.lbl_status.setText(
            "Preparing upload…" if self.mode == UPLOAD else "Preparing download…"
        )

    def _make_title_bar(self, title: str) -> QWidget:
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
        btn_close.clicked.connect(self._on_close_clicked)
        layout.addWidget(btn_close)

        # Drag support from the title bar
        bar.mousePressEvent = self._title_mouse_press
        return bar

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.progress_update.connect(self._on_progress)
        self.status_update.connect(self._on_status)
        self.finished_ok.connect(self._on_finished_ok)
        self.finished_error.connect(self._on_finished_error)
        self.finished_cancelled.connect(self._on_finished_cancelled)

    # ------------------------------------------------------------------
    # Public API (thread-safe)
    # ------------------------------------------------------------------
    def update_progress(self, done: int, total: int) -> bool:
        """Thread-safe. Returns False if the user cancelled.

        Progress signals are **throttled** to at most ~10 per second to
        avoid flooding the GUI event queue.  For large files the transport
        layer calls back every ~8 KiB, which for a 330 MB upload would
        otherwise mean ~42,000 queued signals — each requiring a mutex
        lock, an event queue insert, and GUI-thread widget updates that
        slow the transfer significantly.
        """
        now = time.monotonic()
        is_final = total > 0 and done >= total
        # Emit at most every 0.1 s, but always emit the final 100%.
        if is_final or (now - self._last_emit_time) >= 0.1:
            self._last_emit_time = now
            self.progress_update.emit(done, total)
        return not self._cancel_requested

    def set_status(self, text: str) -> None:
        self.status_update.emit(text)

    def set_file_name(self, name: str) -> None:
        self.lbl_file.setText(name)

    def finish_ok(self, result=None) -> None:
        self.finished_ok.emit(result)

    def finish_error(self, title: str, message: str) -> None:
        self.finished_error.emit(title, message)

    def finish_cancelled(self) -> None:
        self.finished_cancelled.emit()

    def is_cancelled(self) -> bool:
        return self._cancel_requested

    def make_progress_callback(self):
        """Return a callback suitable for passing to the API services."""
        def cb(done: int, total: int) -> bool:
            self.update_progress(done, total)
            return not self._cancel_requested
        return cb

    def make_cancelled_checker(self):
        """Return a no-arg callable for the *cancelled* parameter."""
        return self.is_cancelled

    # ------------------------------------------------------------------
    # GUI-thread slots
    # ------------------------------------------------------------------
    @Slot(object, object)
    def _on_progress(self, done: int, total: int):
        self._total_bytes = total
        # Floating-point percentage — no integer division, no truncation.
        pct = int((done * 100.0) / total) if total > 0 else 0
        self.bar.setValue(pct)

        if total > 0:
            self.lbl_bytes.setText(f"{format_size(done)} / {format_size(total)}")
        else:
            self.lbl_bytes.setText(format_size(done))

        # --- Rolling 3-second window speed/ETA statistics ----------------
        # Every progress callback appends (timestamp, uploadedBytes).
        # Entries older than 3 s are discarded.  Speed is the bytes
        # actually uploaded across the window divided by the window
        # duration — NOT an average since start, NOT a chunk estimate.
        now = time.monotonic()
        self._samples.append((now, done))
        cutoff = now - 3.0
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        rate = 0.0
        if len(self._samples) >= 2:
            oldest_t, oldest_b = self._samples[0]
            dt = now - oldest_t
            if dt > 0:
                rate = (done - oldest_b) / dt
        self._update_rate_eta(rate, done, total)

        # Debug log every progress update (throttled to ~10/s by the
        # emitter, so this is bounded).
        logger.debug(
            "progress: done=%d total=%d pct=%.2f%% rate=%.0f B/s "
            "eta=%s",
            done, total,
            (done * 100.0) / total if total > 0 else 0.0,
            rate,
            self._format_eta((total - done) / rate) if rate > 0 and total > done else "-",
        )

    def _update_rate_eta(self, rate_bps: float, done: int, total: int):
        if rate_bps > 0:
            rate_str = format_size(int(rate_bps)) + "/s"
            if total > done and total > 0:
                remaining = (total - done) / rate_bps
                self.lbl_rate_eta.setText(f"{rate_str} · ETA {self._format_eta(remaining)}")
            else:
                self.lbl_rate_eta.setText(rate_str)
        else:
            self.lbl_rate_eta.setText("")

    @staticmethod
    def _format_eta(seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)}s"
        m = int(seconds // 60)
        s = int(seconds % 60)
        if m < 60:
            return f"{m}m {s}s"
        h = m // 60
        m = m % 60
        return f"{h}h {m}m"

    @Slot(str)
    def _on_status(self, text: str):
        self.lbl_status.setText(text)

    @Slot(object)
    def _on_finished_ok(self, result):
        self.hide()
        self.result_shown.emit(result)

    @Slot(str, str)
    def _on_finished_error(self, title: str, message: str):
        self.error_shown.emit(title, message)

    @Slot()
    def _on_finished_cancelled(self):
        self.error_shown.emit("Cancelled", "The operation was cancelled.")

    # ------------------------------------------------------------------
    # Close / cancel
    # ------------------------------------------------------------------
    def _on_close_clicked(self):
        self._cancel_requested = True
        self.hide()

    def _on_cancel_clicked(self):
        self._cancel_requested = True
        self.set_status("Cancelling…")
        self.finished_cancelled.emit()

    def _title_mouse_press(self, event):
        if self._allow_move and event.button() == Qt.LeftButton:
            self._pressed_pos = event.globalPosition().toPoint()
            from PySide6.QtCore import QPoint
            self._pressed_pos = QPoint(
                self._pressed_pos.x() - self.x(),
                self._pressed_pos.y() - self.y(),
            )
            from PySide6.QtGui import QCursor
            self.setCursor(QCursor(Qt.SizeAllCursor))
