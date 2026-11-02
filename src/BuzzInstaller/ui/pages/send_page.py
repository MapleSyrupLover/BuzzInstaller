"""Send page — choose files, upload them and share the resulting BZ links."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from config.paths import get_transfers_dir
from core.history import HistoryEntry, history
from core.transfer_db import default_store
from system.clipboard_service import clipboard_service
from ui.theme import get_palette_from_config
from ui.transfer_widgets import InterruptedTransfersCard, TransferProgressCard
from ui.widgets import (
    Card, DropZone, NestedCard, PageHeader, clear_layout, divider,
)
from ui.workers import UploadWorker
from utils.file_utils import format_size
from utils.logging import get_logger

logger = get_logger(__name__)


def _path_size(path: str) -> int:
    try:
        p = Path(path)
        if p.is_file():
            return p.stat().st_size
        if p.is_dir():
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        pass
    return 0


class SendPage(QWidget):
    """Upload workflow with drag & drop, progress and shareable results."""

    uploadFinished = Signal()
    busyChanged = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._files: list[str] = []
        self._worker: Optional[UploadWorker] = None
        self._results: list = []
        self._paused_uuid = ""
        self._active_uuid = ""
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(16)

        header = PageHeader(
            "Send files",
            "Upload files or folders to BuzzHeavier. Links are copied automatically.",
        )
        self.btn_clear_header = QPushButton("Clear")
        self.btn_clear_header.setObjectName("ghost")
        self.btn_clear_header.setCursor(Qt.PointingHandCursor)
        self.btn_clear_header.clicked.connect(self.clear_files)
        header.add_action(self.btn_clear_header)
        outer.addWidget(header)

        # Selection card
        self.select_card = Card(
            "Files to send",
            "Drop items anywhere in this card, or use the buttons below.",
        )
        self.drop_zone = DropZone()
        self.drop_zone.filesDropped.connect(self.add_files)
        self.drop_zone.browseRequested.connect(self._browse_files)
        self.select_card.body.addWidget(self.drop_zone)

        self.file_list_layout = QVBoxLayout()
        self.file_list_layout.setSpacing(6)
        self.select_card.body.addLayout(self.file_list_layout)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.total_label = QLabel("No files selected")
        self.total_label.setObjectName("muted")
        footer.addWidget(self.total_label)
        footer.addStretch(1)

        self.btn_folder = QPushButton("Add folder…")
        self.btn_folder.setObjectName("secondary")
        self.btn_folder.setCursor(Qt.PointingHandCursor)
        self.btn_folder.clicked.connect(self._browse_folder)
        footer.addWidget(self.btn_folder)

        self.btn_add = QPushButton("Add files…")
        self.btn_add.setObjectName("secondary")
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.clicked.connect(self._browse_files)
        footer.addWidget(self.btn_add)

        self.btn_upload = QPushButton("Upload")
        self.btn_upload.setObjectName("primary")
        self.btn_upload.setMinimumWidth(130)
        self.btn_upload.setCursor(Qt.PointingHandCursor)
        self.btn_upload.setEnabled(False)
        self.btn_upload.clicked.connect(self.start_upload)
        footer.addWidget(self.btn_upload)
        self.select_card.body.addLayout(footer)
        outer.addWidget(self.select_card)

        # Progress card
        self.progress_card = TransferProgressCard("Uploading")
        self.progress_card.pauseRequested.connect(self._pause_upload)
        self.progress_card.resumeRequested.connect(self._resume_current)
        self.progress_card.cancelRequested.connect(self._cancel_upload)
        self.progress_card.setVisible(False)
        outer.addWidget(self.progress_card)

        # Interrupted transfers (resume after pause, crash or restart)
        self.interrupted_card = InterruptedTransfersCard("upload")
        self.interrupted_card.resumeRequested.connect(self._resume_transfer)
        self.interrupted_card.removeRequested.connect(self._remove_transfer)
        outer.addWidget(self.interrupted_card)

        # Results card
        self.results_card = Card(
            "Ready to share", "The BZ link can be opened on any device."
        )
        self.btn_copy_all = QPushButton("Copy all links")
        self.btn_copy_all.setObjectName("secondary")
        self.btn_copy_all.setCursor(Qt.PointingHandCursor)
        self.btn_copy_all.clicked.connect(self._copy_all)
        self.results_card.add_header_widget(self.btn_copy_all)
        self.results_layout = self.results_card.body
        self.results_card.setVisible(False)
        outer.addWidget(self.results_card)
        outer.addStretch(1)

        self._rebuild_file_list()
        self.interrupted_card.refresh()

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------
    def add_files(self, paths: list[str]) -> None:
        added = 0
        for path in paths:
            if not path or not os.path.exists(path):
                continue
            normalized = os.path.abspath(path)
            if normalized in self._files:
                continue
            self._files.append(normalized)
            added += 1
        if added:
            self._rebuild_file_list()
            self._toast(f"Added {added} item{'s' if added != 1 else ''}.")

    def clear_files(self) -> None:
        if self._worker is not None:
            return
        self._files.clear()
        self._rebuild_file_list()
        self.results_card.setVisible(False)
        self._results = []

    def _remove_file(self, path: str) -> None:
        if self._worker is not None:
            return
        self._files = [p for p in self._files if p != path]
        self._rebuild_file_list()

    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose files to send", os.path.expanduser("~"),
            "All files (*.*)",
        )
        if paths:
            self.add_files(paths)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a folder to send", os.path.expanduser("~")
        )
        if folder:
            self.add_files([folder])

    def _rebuild_file_list(self) -> None:
        clear_layout(self.file_list_layout)
        if not self._files:
            self.total_label.setText("No files selected")
            self.btn_upload.setEnabled(False)
            return

        for path in self._files:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(10, 6, 6, 6)
            layout.setSpacing(10)

            glyph = QLabel("📁" if os.path.isdir(path) else "📄")
            glyph.setStyleSheet("font-size: 15px;")
            layout.addWidget(glyph)

            name = QLabel(os.path.basename(path.rstrip("\\/")) or path)
            name.setStyleSheet("font-weight: 600;")
            name.setToolTip(path)
            layout.addWidget(name, 1)

            size_label = QLabel(format_size(_path_size(path)))
            size_label.setObjectName("caption")
            layout.addWidget(size_label)

            remove = QPushButton("✕")
            remove.setObjectName("ghost")
            remove.setFixedSize(26, 26)
            remove.setCursor(Qt.PointingHandCursor)
            remove.setToolTip("Remove")
            remove.clicked.connect(lambda _=False, p=path: self._remove_file(p))
            layout.addWidget(remove)

            container = NestedCard()
            container.body.setContentsMargins(2, 2, 2, 2)
            container.body.addWidget(row)
            self.file_list_layout.addWidget(container)

        total = sum(_path_size(p) for p in self._files)
        self.total_label.setText(
            f"{len(self._files)} item{'s' if len(self._files) != 1 else ''} · "
            f"{format_size(total)}"
        )
        self.btn_upload.setEnabled(self._worker is None)

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    def start_upload(self) -> None:
        if self._worker is not None or not self._files:
            return
        total = sum(_path_size(p) for p in self._files)
        if len(self._files) == 1:
            name = os.path.basename(self._files[0].rstrip("\\/")) or self._files[0]
        else:
            name = f"{len(self._files)} items"
        self._start_worker(list(self._files), name=name, size=total)

    def _start_worker(self, paths: list[str], name: str, size: int,
                      resume_uuid: str = "") -> None:
        if self._worker is not None:
            self._toast("An upload is already running.", error=True)
            return
        self._results = []
        self._paused_uuid = ""
        self._active_uuid = str(resume_uuid or "")
        self.results_card.setVisible(False)
        self.progress_card.setVisible(True)
        self.progress_card.begin(name, size)
        self.progress_card.set_status(
            "Resuming upload…" if resume_uuid else "Connecting to BuzzHeavier…"
        )
        self.progress_card.progress_bar.setRange(0, 0)
        self.btn_upload.setEnabled(False)
        self.btn_upload.setText("Uploading…")
        self.busyChanged.emit(True)

        self._worker = UploadWorker(
            list(paths), parent=self, resume_uuid=resume_uuid or None,
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
                self.progress_card.set_total_chunks(
                    int(transfer.get("total_chunks", 0) or 0)
                )
        except Exception:
            logger.exception("Could not read transfer metadata")

    def _on_chunk(self, index, state: str, detail: str) -> None:
        self.progress_card.set_chunk(int(index), str(state), str(detail))

    def _on_progress(self, done: int, total: int) -> None:
        self.progress_card.update_progress(done, total)

    def _on_success(self, results) -> None:
        self._results = list(results)
        self._paused_uuid = ""
        self.progress_card.finish("Upload complete.")
        self.progress_card.setVisible(False)
        self.btn_upload.setText("Upload")
        self._files.clear()
        self._rebuild_file_list()

        clear_layout(self.results_layout)
        for result in self._results:
            self.results_layout.addWidget(self._make_result_row(result))
            history.add(HistoryEntry(
                kind="upload",
                name=result.file_name,
                size=result.size,
                code=result.code,
                bz_url=result.bz_url,
                url=result.url,
            ))
        self.results_card.setVisible(True)
        self.interrupted_card.refresh()
        self._toast(
            f"Uploaded {len(self._results)} item"
            f"{'s' if len(self._results) != 1 else ''} · link copied"
        )
        self.uploadFinished.emit()

    def _make_result_row(self, result) -> QWidget:
        card = NestedCard()
        top = QHBoxLayout()
        top.setSpacing(8)
        name = QLabel(f"{result.file_name}  ·  {result.size_str}")
        name.setStyleSheet("font-weight: 600;")
        top.addWidget(name, 1)

        for text, url in (("Open", result.url), ("Copy", result.bz_url)):
            btn = QPushButton(text)
            btn.setObjectName("secondary" if text == "Open" else "primary")
            btn.setFixedHeight(28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, u=url, t=text: self._open_or_copy(t, u))
            top.addWidget(btn)
        card.body.addLayout(top)

        link = QLineEdit(result.bz_url)
        link.setReadOnly(True)
        link.setCursor(Qt.IBeamCursor)
        card.body.addWidget(link)
        return card

    def _open_or_copy(self, action: str, value: str) -> None:
        if action == "Copy":
            if clipboard_service.copy(value):
                self._toast("Link copied to clipboard.")
            else:
                self._toast("Could not copy the link.", error=True)
        else:
            try:
                os.startfile(value)  # type: ignore[attr-defined]
            except Exception:
                self._toast("Could not open the link.", error=True)

    def _copy_all(self) -> None:
        links = "\n".join(r.bz_url for r in self._results)
        if links and clipboard_service.copy(links):
            self._toast(f"Copied {len(self._results)} link(s).")
        elif links:
            self._toast("Could not copy the links.", error=True)

    def _pause_upload(self) -> None:
        if self._worker is not None:
            self._worker.pause()
            self.progress_card.set_status("Pausing…")
            self.progress_card.btn_pause.setEnabled(False)

    def _resume_current(self) -> None:
        if self._paused_uuid:
            self._resume_transfer(self._paused_uuid)

    def _resume_transfer(self, uuid: str) -> None:
        if self._worker is not None:
            self._toast("An upload is already running.", error=True)
            return
        transfer = default_store().get_transfer(uuid)
        if transfer is None:
            self._toast("That transfer no longer exists.", error=True)
            self.interrupted_card.refresh()
            return
        source = Path(str(transfer.get("source_path", "") or ""))
        if not source.exists():
            self._toast(
                "The staged source is no longer available. Please send it again.",
                error=True,
            )
            self.interrupted_card.refresh()
            return
        self._start_worker(
            [str(source)],
            name=str(transfer.get("name", "") or ""),
            size=int(transfer.get("size", 0) or 0),
            resume_uuid=uuid,
        )

    def _remove_transfer(self, uuid: str) -> None:
        if self._worker is not None:
            self._toast("Wait for the current upload to finish.", error=True)
            return
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, "Remove interrupted transfer",
            "Remove this interrupted upload and its saved chunks?",
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

    def _cancel_upload(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_card.set_status("Cancelling…")
            self.progress_card.btn_cancel.setEnabled(False)

    def _on_paused(self, uuid: str) -> None:
        self._paused_uuid = str(uuid or self._active_uuid)
        self.progress_card.set_paused(True)
        self.progress_card.set_status("Paused — verified chunks are saved.")
        self.interrupted_card.refresh()
        self._toast("Upload paused. Resume it from Interrupted transfers.")

    def _on_failed(self, title: str, message: str) -> None:
        self.progress_card.setVisible(False)
        self.btn_upload.setText("Upload")
        self._paused_uuid = ""
        self.interrupted_card.refresh()
        self._toast(f"{title}: {message}", error=True)

    def _on_cancelled(self) -> None:
        self.progress_card.setVisible(False)
        self.btn_upload.setText("Upload")
        self._paused_uuid = ""
        self.interrupted_card.refresh()
        self._toast("Upload cancelled. Verified chunks were kept.")

    def _on_worker_finished(self) -> None:
        self._worker = None
        self.progress_card.btn_cancel.setEnabled(True)
        self.progress_card.btn_pause.setEnabled(True)
        if self._paused_uuid:
            self.progress_card.set_paused(True)
        self._rebuild_file_list()
        self.busyChanged.emit(False)
        self.interrupted_card.refresh()

    # ------------------------------------------------------------------
    def _toast(self, message: str, error: bool = False) -> None:
        window = self.window()
        if hasattr(window, "show_toast"):
            window.show_toast(message, error=error)

    def refresh(self) -> None:
        """Re-apply theme-sensitive pieces (called after a theme change)."""
        self._rebuild_file_list()
        self.interrupted_card.refresh()
        if self._results:
            clear_layout(self.results_layout)
            for result in self._results:
                self.results_layout.addWidget(self._make_result_row(result))
