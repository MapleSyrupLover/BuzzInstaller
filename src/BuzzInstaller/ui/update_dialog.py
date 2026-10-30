"""
Update dialog — shows release notes and installs an update in-app.

The dialog owns the download worker and, when the download completes,
hands the bundle to :mod:`core.updater` which replaces the installation
after this process exits.
"""

from __future__ import annotations

import os
import time
import webbrowser
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from core import updater
from ui.base_window import BaseWindow
from ui.close_button import CloseButton
from ui.theme import Palette, button_qss, get_palette_from_config, progress_bar_qss
from ui.workers import UpdateDownloadWorker
from utils.file_utils import format_size
from utils.logging import get_logger

logger = get_logger(__name__)


class UpdateDialog(BaseWindow):
    """A small frameless dialog with release notes and a download bar."""

    update_installed = Signal()

    def __init__(self, info, palette: Optional[Palette] = None,
                 parent=None) -> None:
        super().__init__(parent=parent, palette=palette or get_palette_from_config())
        self.info = info
        self._worker: Optional[UpdateDownloadWorker] = None
        self._bundle_path = None
        self._build_ui()
        self.adjustSize()
        self.setMinimumWidth(520)
        self.center_on_screen()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        p = self._palette
        cl = self.content_layout

        title_bar = QWidget()
        title_bar.setFixedHeight(28)
        row = QHBoxLayout(title_bar)
        row.setContentsMargins(4, 0, 0, 0)
        label = QLabel("Software Update")
        label.setStyleSheet(f"color: {p.text_secondary}; font-size: 12px;")
        row.addWidget(label)
        row.addStretch(1)
        close = CloseButton(p)
        close.clicked.connect(self.close)
        row.addWidget(close)
        cl.addWidget(title_bar)

        heading = QLabel(f"BuzzInstaller {self.info.version} is available")
        heading.setStyleSheet(
            f"color: {p.text_primary}; font-size: 19px; font-weight: 700;"
        )
        cl.addWidget(heading)

        sub = QLabel(
            f"You are running {updater.current_version()} · "
            f"Download size {self.info.size_str}"
        )
        sub.setStyleSheet(f"color: {p.text_secondary}; font-size: 12px;")
        cl.addWidget(sub)

        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText((self.info.notes or "No release notes were provided.")[:8000])
        notes.setFixedHeight(180)
        notes.setStyleSheet(
            f"QTextEdit {{ background: {p.input_bg}; border: 1px solid {p.border};"
            f"border-radius: 8px; padding: 8px; color: {p.text_primary};"
            "font-size: 12px; }"
        )
        cl.addWidget(notes)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {p.text_secondary}; font-size: 12px;")
        cl.addWidget(self.status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setFixedHeight(8)
        self.bar.setTextVisible(False)
        self.bar.setStyleSheet(progress_bar_qss(p))
        self.bar.setVisible(False)
        cl.addWidget(self.bar)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.btn_web = QPushButton("Release page")
        self.btn_web.setStyleSheet(button_qss(p, "text"))
        self.btn_web.setCursor(Qt.PointingHandCursor)
        self.btn_web.clicked.connect(self._open_release_page)
        buttons.addWidget(self.btn_web)
        buttons.addStretch(1)

        self.btn_later = QPushButton("Remind me later")
        self.btn_later.setStyleSheet(button_qss(p, "secondary"))
        self.btn_later.setFixedHeight(36)
        self.btn_later.setCursor(Qt.PointingHandCursor)
        self.btn_later.clicked.connect(self.close)
        buttons.addWidget(self.btn_later)

        self.btn_install = QPushButton("Install update")
        self.btn_install.setStyleSheet(button_qss(p, "primary"))
        self.btn_install.setFixedHeight(36)
        self.btn_install.setCursor(Qt.PointingHandCursor)
        self.btn_install.clicked.connect(self._start_install)
        buttons.addWidget(self.btn_install)
        cl.addLayout(buttons)

        if not updater.can_self_update():
            self.btn_install.setEnabled(False)
            self.status.setText(
                "Self-update is only available in the installed build — "
                "use the release page to download it."
            )

    # ------------------------------------------------------------------
    def _open_release_page(self) -> None:
        url = self.info.html_url or updater.RELEASES_PAGE
        try:
            os.startfile(url)  # type: ignore[attr-defined]
        except Exception:
            webbrowser.open(url)

    def _start_install(self) -> None:
        if self._worker is not None:
            return
        self.btn_install.setEnabled(False)
        self.btn_later.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.status.setText("Preparing download…")

        self._worker = UpdateDownloadWorker(self.info, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.status.connect(self.status.setText)
        self._worker.finished_ok.connect(self._on_downloaded)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_thread_finished)
        self._worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.bar.setValue(int(done * 100 / total))
            remaining = max(total - done, 0)
            self.status.setText(
                f"{format_size(done)} of {format_size(total)} · "
                f"{format_size(remaining)} remaining"
            )
        else:
            self.bar.setRange(0, 0)
            self.status.setText(f"{format_size(done)} downloaded")

    def _on_downloaded(self, path) -> None:
        self._bundle_path = path
        self.bar.setRange(0, 100)
        self.bar.setValue(100)
        self.status.setText("Update downloaded. Restarting to install…")
        QTimer.singleShot(400, self._apply_and_quit)

    def _apply_and_quit(self) -> None:
        try:
            log_path = updater.apply_update(self._bundle_path)
            logger.info("Applying update; helper log: %s", log_path)
        except Exception as exc:
            logger.exception("Could not apply update")
            self.status.setText(f"Could not install the update: {exc}")
            self.btn_install.setEnabled(True)
            self.btn_later.setEnabled(True)
            return
        self.update_installed.emit()
        QTimer.singleShot(900, self._quit_app)

    @staticmethod
    def _quit_app() -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _on_failed(self, message: str) -> None:
        self.bar.setVisible(False)
        self.status.setText(f"Update failed: {message}")
        self.btn_install.setEnabled(True)
        self.btn_later.setEnabled(True)

    def _on_thread_finished(self) -> None:
        self._worker = None
