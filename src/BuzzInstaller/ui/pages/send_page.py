"""Send page — choose files, upload them and share the resulting BZ links."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from core.history import HistoryEntry, history
from system.clipboard_service import clipboard_service
from ui.theme import get_palette_from_config
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
        self.progress_card = Card("Uploading")
        self.progress_status = QLabel("Preparing…")
        self.progress_status.setObjectName("muted")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        self.progress_card.body.addWidget(self.progress_status)
        self.progress_card.body.addWidget(self.progress_bar)
        progress_row = QHBoxLayout()
        self.progress_bytes = QLabel("")
        self.progress_bytes.setObjectName("caption")
        self.progress_files = QLabel("")
        self.progress_files.setObjectName("caption")
        progress_row.addWidget(self.progress_bytes)
        progress_row.addStretch(1)
        progress_row.addWidget(self.progress_files)
        self.progress_card.body.addLayout(progress_row)

        cancel_row = QHBoxLayout()
        cancel_row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel upload")
        self.btn_cancel.setObjectName("secondary")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self._cancel_upload)
        cancel_row.addWidget(self.btn_cancel)
        self.progress_card.body.addLayout(cancel_row)
        self.progress_card.setVisible(False)
        outer.addWidget(self.progress_card)

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
        self._results = []
        self.results_card.setVisible(False)
        self.progress_card.setVisible(True)
        self.progress_status.setText("Connecting to BuzzHeavier…")
        self.progress_bar.setRange(0, 0)
        self.progress_bytes.setText("")
        self.progress_files.setText("")
        self.btn_upload.setEnabled(False)
        self.btn_upload.setText("Uploading…")
        self.busyChanged.emit(True)

        self._worker = UploadWorker(list(self._files), parent=self)
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
            self.progress_bytes.setText(
                f"{format_size(done)} of {format_size(total)}"
            )
        else:
            self.progress_bar.setRange(0, 0)
            self.progress_bytes.setText(format_size(done))

    def _on_success(self, results) -> None:
        self._results = list(results)
        self.progress_card.setVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
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

    def _cancel_upload(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_status.setText("Cancelling…")
            self.btn_cancel.setEnabled(False)

    def _on_failed(self, title: str, message: str) -> None:
        self.progress_card.setVisible(False)
        self.btn_upload.setText("Upload")
        self._toast(f"{title}: {message}", error=True)

    def _on_cancelled(self) -> None:
        self.progress_card.setVisible(False)
        self.btn_upload.setText("Upload")
        self._toast("Upload cancelled.")

    def _on_worker_finished(self) -> None:
        self._worker = None
        self.btn_cancel.setEnabled(True)
        self._rebuild_file_list()
        self.busyChanged.emit(False)

    # ------------------------------------------------------------------
    def _toast(self, message: str, error: bool = False) -> None:
        window = self.window()
        if hasattr(window, "show_toast"):
            window.show_toast(message, error=error)

    def refresh(self) -> None:
        """Re-apply theme-sensitive pieces (called after a theme change)."""
        self._rebuild_file_list()
        if self._results:
            clear_layout(self.results_layout)
            for result in self._results:
                self.results_layout.addWidget(self._make_result_row(result))
