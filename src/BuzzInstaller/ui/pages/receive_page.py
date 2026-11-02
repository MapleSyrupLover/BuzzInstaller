"""Receive page — paste a BZ link, download it and open the result."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from config.config_manager import load_config, save_config
from config.paths import get_transfers_dir
from core.buzzheavier_api import extract_code, is_valid_code
from core.history import HistoryEntry, history
from core.transfer_db import default_store
from system.clipboard_service import clipboard_service
from ui.transfer_widgets import InterruptedTransfersCard, TransferProgressCard
from ui.widgets import Card, CheckBox, PageHeader, divider
from ui.workers import DownloadWorker
from utils.logging import get_logger

logger = get_logger(__name__)


class ReceivePage(QWidget):
    """Download workflow with progress, speed/ETA and result actions."""

    downloadFinished = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Optional[DownloadWorker] = None
        self._result = None
        self._last_target = None
        self._paused_uuid = ""
        self._active_uuid = ""
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
        self.progress_card = TransferProgressCard("Downloading")
        self.progress_card.pauseRequested.connect(self._pause_download)
        self.progress_card.resumeRequested.connect(self._resume_current)
        self.progress_card.cancelRequested.connect(self._cancel)
        self.progress_card.setVisible(False)
        outer.addWidget(self.progress_card)

        # Interrupted transfers (resume after pause, crash or restart)
        self.interrupted_card = InterruptedTransfersCard("download")
        self.interrupted_card.resumeRequested.connect(self._resume_transfer)
        self.interrupted_card.removeRequested.connect(self._remove_transfer)
        outer.addWidget(self.interrupted_card)

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
        self.interrupted_card.refresh()

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
        self._start_worker(code, cfg)

    def _start_worker(self, code: str, cfg: dict,
                      resume_uuid: str = "") -> None:
        if self._worker is not None:
            self._toast("A download is already running.", error=True)
            return
        self.result_card.setVisible(False)
        self.progress_card.setVisible(True)
        self.progress_card.begin(code or "Download", 0)
        self.progress_card.set_status(
            "Resuming download…" if resume_uuid else "Resolving download link…"
        )
        self.progress_card.progress_bar.setRange(0, 0)
        self.btn_download.setEnabled(False)
        self.btn_download.setText("Downloading…")
        self._paused_uuid = ""
        self._active_uuid = str(resume_uuid or "")

        self._worker = DownloadWorker(
            code, config=cfg, parent=self, resume_uuid=resume_uuid or None,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.status.connect(self.progress_card.set_status)
        self._worker.chunk.connect(self._on_chunk)
        self._worker.transferStarted.connect(self._on_transfer_started)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.paused.connect(self._on_paused)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_transfer_started(self, uuid: str) -> None:
        self._active_uuid = str(uuid or "")
        self.progress_card.set_pausable(True)
        try:
            transfer = default_store().get_transfer(self._active_uuid)
            if transfer is not None:
                self.progress_card.name_label.setText(
                    str(transfer.get("name", "") or self._active_uuid)
                )
                self.progress_card.set_size(int(transfer.get("size", 0) or 0))
                self.progress_card.set_total_chunks(
                    int(transfer.get("total_chunks", 0) or 0)
                )
        except Exception:
            logger.exception("Could not read transfer metadata")

    def _on_chunk(self, index, state: str, detail: str) -> None:
        self.progress_card.set_chunk(int(index), str(state), str(detail))

    def _on_progress(self, done: int, total: int) -> None:
        self.progress_card.update_progress(done, total)

    def _on_success(self, result) -> None:
        self._result = result
        self._paused_uuid = ""
        self.progress_card.finish("Download complete.")
        self.progress_card.setVisible(False)
        self.btn_download.setText("Download")
        self.result_card.setVisible(True)
        self.interrupted_card.refresh()

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

    def _on_paused(self, uuid: str) -> None:
        self._paused_uuid = str(uuid or self._active_uuid)
        self.progress_card.set_paused(True)
        self.progress_card.set_status("Paused — verified chunks are saved.")
        self.interrupted_card.refresh()
        self._toast("Download paused. Resume it from Interrupted transfers.")

    def _resume_current(self) -> None:
        if self._paused_uuid:
            self._resume_transfer(self._paused_uuid)

    def _resume_transfer(self, uuid: str) -> None:
        if self._worker is not None:
            self._toast("A download is already running.", error=True)
            return
        transfer = default_store().get_transfer(uuid)
        if transfer is None:
            self._toast("That transfer no longer exists.", error=True)
            self.interrupted_card.refresh()
            return
        self._load_options()
        cfg = load_config()
        cfg["auto_extract_zip"] = self.chk_extract.isChecked()
        cfg["delete_zip_after_extract"] = self.chk_delete_zip.isChecked()
        cfg["launch_folder_after_download"] = self.chk_open_folder.isChecked()
        code = str(
            transfer.get("manifest_code", "")
            or transfer.get("source_path", "")
            or ""
        )
        self._start_worker(code, cfg, resume_uuid=uuid)

    def _remove_transfer(self, uuid: str) -> None:
        if self._worker is not None:
            self._toast("Wait for the current download to finish.", error=True)
            return
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, "Remove interrupted transfer",
            "Remove this interrupted download and its saved chunks?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            default_store().delete_transfer(uuid)
            staging = get_transfers_dir() / str(uuid)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        except Exception as exc:
            logger.warning("Could not remove transfer %s: %s", uuid, exc)
            self._toast("Could not remove the transfer.", error=True)
            return
        if self._paused_uuid == uuid:
            self._paused_uuid = ""
            self.progress_card.setVisible(False)
        self.interrupted_card.refresh()
        self._toast("Interrupted transfer removed.")

    def _on_failed(self, title: str, message: str) -> None:
        self.progress_card.setVisible(False)
        self.btn_download.setText("Download")
        self._paused_uuid = ""
        self.interrupted_card.refresh()
        self._toast(f"{title}: {message}", error=True)

    def _on_cancelled(self) -> None:
        self.progress_card.setVisible(False)
        self.btn_download.setText("Download")
        self._paused_uuid = ""
        self.interrupted_card.refresh()
        self._toast("Download cancelled. Verified chunks were kept.")

    def _on_worker_finished(self) -> None:
        self._worker = None
        self.btn_download.setEnabled(True)
        self.progress_card.btn_cancel.setEnabled(True)
        self.progress_card.btn_pause.setEnabled(True)
        if self._paused_uuid:
            self.progress_card.set_paused(True)
        self.interrupted_card.refresh()

    def _pause_download(self) -> None:
        if self._worker is not None:
            self._worker.pause()
            self.progress_card.set_status("Pausing…")
            self.progress_card.btn_pause.setEnabled(False)

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_card.set_status("Cancelling…")
            self.progress_card.btn_cancel.setEnabled(False)

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
        self.interrupted_card.refresh()
