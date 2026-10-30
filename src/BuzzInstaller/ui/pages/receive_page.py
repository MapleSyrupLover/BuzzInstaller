"""Receive page — paste a BZ link, download it and open the result."""

from __future__ import annotations

import os
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from config.config_manager import load_config, save_config
from core.buzzheavier_api import extract_code, is_valid_code
from core.history import HistoryEntry, history
from system.clipboard_service import clipboard_service
from ui.widgets import Card, CheckBox, PageHeader, divider
from ui.workers import DownloadWorker
from utils.file_utils import format_size
from utils.logging import get_logger

logger = get_logger(__name__)


class ReceivePage(QWidget):
    """Download workflow with progress, speed/ETA and result actions."""

    downloadFinished = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Optional[DownloadWorker] = None
        self._samples: deque = deque()
        self._result = None
        self._last_target = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(16)

        header = PageHeader(
            "Receive a file",
            "Open a BZ:// link or paste a code to download it to your Downloads folder.",
        )
        outer.addWidget(header)

        # Link card
        link_card = Card("BZ link")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.link_input = QLineEdit()
        self.link_input.setPlaceholderText("BZ://h0l6lgmru052")
        self.link_input.setMinimumHeight(42)
        self.link_input.returnPressed.connect(self._on_download_clicked)
        row.addWidget(self.link_input, 1)

        self.btn_paste = QPushButton("Paste")
        self.btn_paste.setObjectName("secondary")
        self.btn_paste.setMinimumHeight(42)
        self.btn_paste.setCursor(Qt.PointingHandCursor)
        self.btn_paste.clicked.connect(self._paste)
        row.addWidget(self.btn_paste)

        self.btn_download = QPushButton("Download")
        self.btn_download.setObjectName("primary")
        self.btn_download.setMinimumHeight(42)
        self.btn_download.setMinimumWidth(130)
        self.btn_download.setCursor(Qt.PointingHandCursor)
        self.btn_download.clicked.connect(self._on_download_clicked)
        row.addWidget(self.btn_download)
        link_card.body.addLayout(row)
        outer.addWidget(link_card)

        # Options card
        options_card = Card("Download options")
        self.chk_extract = CheckBox("Automatically extract ZIP archives")
        self.chk_delete_zip = CheckBox("Delete ZIP after extraction")
        self.chk_open_folder = CheckBox("Open the folder when the download finishes")
        for chk in (self.chk_extract, self.chk_delete_zip, self.chk_open_folder):
            options_card.body.addWidget(chk)
            chk.toggled.connect(self._save_options)
        outer.addWidget(options_card)

        # Progress card
        self.progress_card = Card("Downloading")
        self.progress_status = QLabel("Preparing download…")
        self.progress_status.setObjectName("muted")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        self.progress_detail = QLabel("")
        self.progress_detail.setObjectName("caption")
        self.progress_card.body.addWidget(self.progress_status)
        self.progress_card.body.addWidget(self.progress_bar)
        detail_row = QHBoxLayout()
        detail_row.addWidget(self.progress_detail)
        detail_row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("ghost")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self._cancel)
        detail_row.addWidget(self.btn_cancel)
        self.progress_card.body.addLayout(detail_row)
        self.progress_card.setVisible(False)
        outer.addWidget(self.progress_card)

        # Result card
        self.result_card = Card("Download complete")
        self.result_name = QLabel("")
        self.result_name.setObjectName("cardTitle")
        self.result_meta = QLabel("")
        self.result_meta.setObjectName("muted")
        self.result_meta.setWordWrap(True)
        self.result_card.body.addWidget(self.result_name)
        self.result_card.body.addWidget(self.result_meta)
        self.result_card.body.addWidget(divider())

        result_buttons = QHBoxLayout()
        result_buttons.setSpacing(8)
        self.btn_open_folder = QPushButton("Open folder")
        self.btn_open_folder.setObjectName("primary")
        self.btn_open_folder.setCursor(Qt.PointingHandCursor)
        self.btn_open_folder.clicked.connect(self._open_folder)
        result_buttons.addWidget(self.btn_open_folder)

        self.btn_copy_path = QPushButton("Copy path")
        self.btn_copy_path.setObjectName("secondary")
        self.btn_copy_path.setCursor(Qt.PointingHandCursor)
        self.btn_copy_path.clicked.connect(self._copy_path)
        result_buttons.addWidget(self.btn_copy_path)

        self.btn_again = QPushButton("Download another")
        self.btn_again.setObjectName("ghost")
        self.btn_again.setCursor(Qt.PointingHandCursor)
        self.btn_again.clicked.connect(self._reset)
        result_buttons.addWidget(self.btn_again)
        result_buttons.addStretch(1)
        self.result_card.body.addLayout(result_buttons)
        self.result_card.setVisible(False)
        outer.addWidget(self.result_card)

        tip = QLabel(
            "Shortcut: Alt+F1 with a BZ link selected downloads it instantly, "
            "even when this window is closed."
        )
        tip.setObjectName("caption")
        tip.setWordWrap(True)
        outer.addWidget(tip)
        outer.addStretch(1)

        self._load_options()

    # ------------------------------------------------------------------
    def _load_options(self) -> None:
        cfg = load_config()
        for chk, key, default in (
            (self.chk_extract, "auto_extract_zip", True),
            (self.chk_delete_zip, "delete_zip_after_extract", False),
            (self.chk_open_folder, "launch_folder_after_download", True),
        ):
            chk.blockSignals(True)
            chk.setChecked(bool(cfg.get(key, default)))
            chk.blockSignals(False)

    def _save_options(self) -> None:
        cfg = load_config()
        cfg["auto_extract_zip"] = self.chk_extract.isChecked()
        cfg["delete_zip_after_extract"] = self.chk_delete_zip.isChecked()
        cfg["launch_folder_after_download"] = self.chk_open_folder.isChecked()
        save_config(cfg)

    # ------------------------------------------------------------------
    def set_code(self, code: str) -> None:
        self.link_input.setText(f"BZ://{code}")

    def _paste(self) -> None:
        from PySide6.QtWidgets import QApplication
        text = QApplication.clipboard().text().strip()
        if text:
            self.link_input.setText(text)
            self.link_input.setFocus()
        else:
            self._toast("Clipboard is empty.", error=True)

    def _on_download_clicked(self) -> None:
        raw = self.link_input.text().strip()
        if not raw:
            self._toast("Paste a BZ link or code first.", error=True)
            return
        try:
            code = extract_code(raw)
        except Exception:
            code = ""
        if not is_valid_code(code):
            self._toast("That doesn't look like a valid BZ link.", error=True)
            return
        self.start_download(code)

    # ------------------------------------------------------------------
    def start_download(self, code: str) -> None:
        if self._worker is not None:
            self._toast("A download is already running.", error=True)
            return
        self._load_options()
        cfg = load_config()
        cfg["auto_extract_zip"] = self.chk_extract.isChecked()
        cfg["delete_zip_after_extract"] = self.chk_delete_zip.isChecked()
        cfg["launch_folder_after_download"] = self.chk_open_folder.isChecked()

        self.result_card.setVisible(False)
        self.progress_card.setVisible(True)
        self.progress_status.setText("Resolving download link…")
        self.progress_bar.setRange(0, 0)
        self.progress_detail.setText("")
        self.btn_download.setEnabled(False)
        self.btn_download.setText("Downloading…")
        self.btn_cancel.setEnabled(True)
        self._samples.clear()

        self._worker = DownloadWorker(code, config=cfg, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.status.connect(self.progress_status.setText)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(done * 100 / total))
        else:
            self.progress_bar.setRange(0, 0)

        now = time.monotonic()
        self._samples.append((now, done))
        cutoff = now - 3.0
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        rate = 0.0
        if len(self._samples) >= 2:
            oldest_t, oldest_b = self._samples[0]
            if now > oldest_t:
                rate = (done - oldest_b) / (now - oldest_t)

        parts = []
        if total > 0:
            parts.append(f"{format_size(done)} / {format_size(total)}")
        else:
            parts.append(format_size(done))
        if rate > 0:
            parts.append(f"{format_size(int(rate))}/s")
            if total > done > 0:
                seconds = (total - done) / rate
                parts.append(f"ETA {self._format_eta(seconds)}")
        self.progress_detail.setText(" · ".join(parts))

    @staticmethod
    def _format_eta(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m {seconds % 60}s"
        return f"{minutes // 60}h {minutes % 60}m"

    def _on_success(self, result) -> None:
        self._result = result
        self.progress_card.setVisible(False)
        self.btn_download.setText("Download")
        self.result_card.setVisible(True)

        target = result.extracted_to if getattr(result, "was_extracted", False) else result.file_path
        self.result_name.setText(result.file_name)
        detail = f"{result.size_str} · saved to {result.download_folder or Path(result.file_path).parent}"
        if getattr(result, "was_extracted", False):
            detail += f"\nExtracted to {result.extracted_to}"
        self.result_meta.setText(detail)

        history.add(HistoryEntry(
            kind="download",
            name=result.file_name,
            size=result.size,
            code=result.code,
            bz_url=f"BZ://{result.code}",
            path=str(result.file_path),
            extracted_to=str(result.extracted_to or ""),
        ))
        self._toast("Download complete.")
        self.downloadFinished.emit()
        self._last_target = target

    def _on_failed(self, title: str, message: str) -> None:
        self.progress_card.setVisible(False)
        self.btn_download.setText("Download")
        self._toast(f"{title}: {message}", error=True)

    def _on_cancelled(self) -> None:
        self.progress_card.setVisible(False)
        self.btn_download.setText("Download")
        self._toast("Download cancelled.")

    def _on_worker_finished(self) -> None:
        self._worker = None
        self.btn_download.setEnabled(True)
        self.btn_cancel.setEnabled(True)

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_status.setText("Cancelling…")
            self.btn_cancel.setEnabled(False)

    # ------------------------------------------------------------------
    def _open_folder(self) -> None:
        target = getattr(self, "_last_target", None)
        if target is None:
            return
        try:
            target = Path(target)
            if target.is_dir():
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["explorer.exe", "/select,", str(target)])
        except Exception as exc:
            logger.warning("Could not open folder: %s", exc)
            self._toast("Could not open the folder.", error=True)

    def _copy_path(self) -> None:
        target = getattr(self, "_last_target", None)
        if target and clipboard_service.copy(str(target)):
            self._toast("Path copied.")

    def _reset(self) -> None:
        self.result_card.setVisible(False)
        self.link_input.clear()
        self.link_input.setFocus()

    def _toast(self, message: str, error: bool = False) -> None:
        window = self.window()
        if hasattr(window, "show_toast"):
            window.show_toast(message, error=error)

    def refresh(self) -> None:
        self._load_options()
