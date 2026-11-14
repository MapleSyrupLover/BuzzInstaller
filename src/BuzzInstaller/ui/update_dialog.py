"""Update dialog - shows release notes and starts the dedicated updater.

Clicking **Update now** copies/starts ``BuzzInstallerUpdater.exe`` and
then shuts down BuzzInstaller completely.  The updater replaces the
installation, restarts the app and reports the result itself.
"""

from __future__ import annotations

import os
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
from ui.workers import UpdaterLaunchWorker
from utils.logging import get_logger

logger = get_logger(__name__)

WARNING_TEXT = (
    "Running the updater will shut down the app. It is recommended to "
    "pause all uploads and downloads to prevent errors."
)


class UpdateDialog(BaseWindow):
    """A small frameless dialog with release notes and an update button."""

    update_installed = Signal()

    def __init__(self, info, palette: Optional[Palette] = None,
                 parent=None) -> None:
        super().__init__(parent=parent, palette=palette or get_palette_from_config())
        self.info = info
        self._worker: Optional[UpdaterLaunchWorker] = None
        self._busy = False
        self._build_ui()
        self.adjustSize()
        self.setMinimumWidth(540)
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
        self.btn_x = CloseButton(p)
        self.btn_x.clicked.connect(self.close)
        row.addWidget(self.btn_x)
        cl.addWidget(title_bar)

        heading = QLabel(f"BuzzInstaller {self.info.version} is available")
        heading.setStyleSheet(
            f"color: {p.text_primary}; font-size: 19px; font-weight: 700;"
        )
        cl.addWidget(heading)

        sub = QLabel(
            f"You are running {updater.current_version()} - "
            f"Download size {self.info.size_str}"
        )
        sub.setStyleSheet(f"color: {p.text_secondary}; font-size: 12px;")
        cl.addWidget(sub)

        warning = QLabel(WARNING_TEXT)
        warning.setWordWrap(True)
        warning.setStyleSheet(
            f"color: {p.text_primary}; font-size: 12px; background: {p.input_bg};"
            f"border: 1px solid {p.border}; border-radius: 8px; padding: 10px;"
        )
        cl.addWidget(warning)

        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText((self.info.notes or "No release notes were provided.")[:8000])
        notes.setFixedHeight(170)
        notes.setStyleSheet(
            f"QTextEdit {{ background: {p.input_bg}; border: 1px solid {p.border};"
            f"border-radius: 8px; padding: 8px; color: {p.text_primary};"
            "font-size: 12px; }"
        )
        cl.addWidget(notes)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {p.text_secondary}; font-size: 12px;")
        cl.addWidget(self.status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
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
        self.btn_install.clicked.connect(self._start_update)
        buttons.addWidget(self.btn_install)
        cl.addLayout(buttons)

        if not updater.can_self_update():
            self.btn_install.setEnabled(False)
            self.status.setText(
                "Self-update is only available in the installed build - "
                "use the release page to download it."
            )

    # ------------------------------------------------------------------
    def _open_release_page(self) -> None:
        url = updater.changelog_url(self.info)
        try:
            os.startfile(url)  # type: ignore[attr-defined]
        except Exception:
            webbrowser.open(url)

    def _start_update(self) -> None:
        if self._busy or self._worker is not None:
            return
        self._busy = True
        self.btn_install.setEnabled(False)
        self.btn_later.setEnabled(False)
        self.btn_web.setEnabled(False)
        self.btn_x.setEnabled(False)
        self.bar.setVisible(True)
        self.status.setText("Preparing the updater...")

        self._worker = UpdaterLaunchWorker(self.info, parent=self)
        self._worker.status.connect(self.status.setText)
        self._worker.ready.connect(self._on_updater_started)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_thread_finished)
        self._worker.start()

    def _on_updater_started(self) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(100)
        self.status.setText(
            "The updater is starting. BuzzInstaller will close now and "
            "restart after the update."
        )
        self.update_installed.emit()
        QTimer.singleShot(300, self._quit_app)

    @staticmethod
    def _quit_app() -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.bar.setVisible(False)
        self.status.setText(f"Could not start the updater: {message}")
        self.btn_install.setEnabled(True)
        self.btn_later.setEnabled(True)
        self.btn_web.setEnabled(True)
        self.btn_x.setEnabled(True)

    def _on_thread_finished(self) -> None:
        self._worker = None

    def closeEvent(self, event) -> None:
        if self._busy:
            event.ignore()
            self.status.setText(
                "The updater is being prepared. Please wait a moment."
            )
            return
        super().closeEvent(event)
