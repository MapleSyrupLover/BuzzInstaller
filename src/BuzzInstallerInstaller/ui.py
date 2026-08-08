"""
Minimal UI for BuzzInstallerInstaller.

Features:
  * Automatic version check on startup
  * Clear state display (CHECKING, NOT_INSTALLED, UP_TO_DATE,
    UPDATE_AVAILABLE, DOWNLOADING, ERROR)
  * Installation location selection (PATH or custom directory)
  * Download progress with percentage and size
  * Install/Update, Cancel, and Delete buttons
  * Error display with Retry
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QFrame, QFileDialog, QMessageBox,
    QRadioButton, QButtonGroup,
)

from installer import Installer, InstallerError, delete_buzzinstaller
from metadata import get_default_install_dir
from downloader import DownloadFailedError, DownloadCancelledError

# ---------------------------------------------------------------------------
# Worker threads
# ---------------------------------------------------------------------------
class InstallWorker(QThread):
    """Runs the installer in a background thread."""

    status_changed = Signal(str)
    progress_changed = Signal(int, int)  # (received, total)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, installer: Installer) -> None:
        super().__init__()
        self.installer = installer
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            self.installer.status_cb = self.status_changed.emit
            self.installer.progress_cb = self._on_progress
            self.installer.cancelled = lambda: self._cancelled

            result = self.installer.run()
            if result == 0:
                self.finished_ok.emit()
            else:
                self.failed.emit("Update failed.")
        except Exception as exc:
            self.failed.emit(str(exc))

    def _on_progress(self, received: int, total: int) -> None:
        self.progress_changed.emit(received, total)


class CheckWorker(QThread):
    """Runs the version check in a background thread."""

    finished_check = Signal(dict)

    def __init__(self, installer: Installer) -> None:
        super().__init__()
        self.installer = installer

    def run(self) -> None:
        try:
            result = self.installer.check_for_updates()
            self.finished_check.emit(result)
        except Exception as exc:
            self.finished_check.emit({
                "release": None,
                "installed": False,
                "installed_version": None,
                "latest_version": None,
                "state": "error",
                "message": f"Unable to check for updates: {exc}",
            })


class DeleteWorker(QThread):
    """Runs the deletion in a background thread."""

    finished_delete = Signal(bool, str)

    def run(self) -> None:
        try:
            ok = delete_buzzinstaller()
            self.finished_delete.emit(ok, "" if ok else "Deletion failed.")
        except Exception as exc:
            self.finished_delete.emit(False, str(exc))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class InstallerWindow(QWidget):
    """Main installer window."""

    STATE_CHECKING = "checking"
    STATE_NOT_INSTALLED = "not_installed"
    STATE_UP_TO_DATE = "up_to_date"
    STATE_UPDATE_AVAILABLE = "update_available"
    STATE_NEWER_INSTALLED = "newer_installed"
    STATE_DOWNLOADING = "downloading"
    STATE_ERROR = "error"

    def __init__(self, installer: Installer) -> None:
        super().__init__()
        self.installer = installer
        self.worker: Optional[InstallWorker] = None
        self.check_worker: Optional[CheckWorker] = None
        self.delete_worker: Optional[DeleteWorker] = None
        self._current_state = self.STATE_CHECKING
        self._check_result: dict = {}
        self._setup_ui()
        self._apply_theme()
        self._start_auto_check()

    def _setup_ui(self) -> None:
        self.setWindowTitle("BuzzInstaller Installer")
        self.setFixedSize(480, 420)
        self.setWindowFlags(
            Qt.Window
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
            | Qt.WindowCloseButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        # Title
        title = QLabel("BuzzInstaller")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Subtitle
        subtitle = QLabel("Installer & Updater")
        subtitle.setStyleSheet("font-size: 12px; color: #6B6B6B;")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #E0E0E0;")
        layout.addWidget(sep)

        # Status text
        self.status_label = QLabel("Checking for the latest version...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(self.status_label)

        # Version info text
        self.version_label = QLabel("")
        self.version_label.setWordWrap(True)
        self.version_label.setStyleSheet("font-size: 12px; color: #6B6B6B;")
        layout.addWidget(self.version_label)

        # --- Installation location ---
        self.location_frame = QFrame()
        location_layout = QVBoxLayout(self.location_frame)
        location_layout.setContentsMargins(8, 8, 8, 8)
        location_layout.setSpacing(6)

        location_title = QLabel("Installation location")
        location_title.setStyleSheet("font-size: 12px; font-weight: 600;")
        location_layout.addWidget(location_title)

        # PATH radio
        self.path_radio = QRadioButton("Install to PATH (recommended)")
        self.path_radio.setToolTip(
            "Installs to a per-user directory and adds it to your PATH "
            "so 'bz' works from any terminal."
        )
        self.path_radio.setChecked(True)
        location_layout.addWidget(self.path_radio)

        # Custom radio + browse
        custom_row = QHBoxLayout()
        self.custom_radio = QRadioButton("Custom directory")
        custom_row.addWidget(self.custom_radio)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.setEnabled(False)
        self.browse_btn.setStyleSheet("""
            QPushButton {
                background: #F4F4F4;
                color: #1F1F1F;
                border: 1px solid #E0E0E0;
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
            }
            QPushButton:hover { background: #E8E8E8; }
            QPushButton:disabled { color: #9A9A9A; }
        """)
        self.browse_btn.clicked.connect(self._browse_directory)
        custom_row.addWidget(self.browse_btn)
        custom_row.addStretch()
        location_layout.addLayout(custom_row)

        # Selected directory label
        self.dir_label = QLabel(str(get_default_install_dir()))
        self.dir_label.setWordWrap(True)
        self.dir_label.setStyleSheet(
            "font-size: 11px; color: #6B6B6B; border: 1px solid #E0E0E0; "
            "border-radius: 4px; padding: 4px 8px;"
        )
        location_layout.addWidget(self.dir_label)

        self._radio_group = QButtonGroup(self)
        self._radio_group.addButton(self.path_radio)
        self._radio_group.addButton(self.custom_radio)
        self.path_radio.toggled.connect(self._on_radio_changed)
        self.custom_radio.toggled.connect(self._on_radio_changed)

        layout.addWidget(self.location_frame)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                background: #E6E6E6;
                border-radius: 4px;
                height: 10px;
                text-align: center;
                color: #1F1F1F;
                font-size: 11px;
            }
            QProgressBar::chunk {
                background: #0078D4;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.progress_bar)

        # Progress detail label
        self.progress_detail = QLabel("")
        self.progress_detail.setAlignment(Qt.AlignCenter)
        self.progress_detail.setStyleSheet("font-size: 11px; color: #6B6B6B;")
        self.progress_detail.setVisible(False)
        layout.addWidget(self.progress_detail)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        # Install/Update button
        self.install_btn = QPushButton("Install BuzzInstaller")
        self.install_btn.setStyleSheet("""
            QPushButton {
                background: #0078D4;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover { background: #1082FF; }
            QPushButton:pressed { background: #005A9E; }
            QPushButton:disabled { background: #9A9A9A; }
        """)
        self.install_btn.clicked.connect(self._start_install)
        btn_layout.addWidget(self.install_btn)

        # Delete button
        self.delete_btn = QPushButton("Delete BuzzInstaller")
        self.delete_btn.setStyleSheet("""
            QPushButton {
                background: #D13438;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover { background: #E81123; }
            QPushButton:pressed { background: #A4262C; }
            QPushButton:disabled { background: #9A9A9A; }
        """)
        self.delete_btn.clicked.connect(self._start_delete)
        self.delete_btn.setVisible(False)
        btn_layout.addWidget(self.delete_btn)

        # Cancel button — grey background, white text
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: #6C6C6C;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover { background: #7A7A7A; }
            QPushButton:pressed { background: #555555; }
            QPushButton:disabled { background: #9A9A9A; }
        """)
        self.cancel_btn.clicked.connect(self._cancel)
        btn_layout.addWidget(self.cancel_btn)

        layout.addLayout(btn_layout)

        # Retry button
        self.retry_btn = QPushButton("Retry")
        self.retry_btn.setStyleSheet("""
            QPushButton {
                background: #0078D4;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover { background: #1082FF; }
            QPushButton:pressed { background: #005A9E; }
        """)
        self.retry_btn.clicked.connect(self._start_auto_check)
        self.retry_btn.setVisible(False)
        layout.addWidget(self.retry_btn)

        self._center_on_screen()

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2,
        )

    def _apply_theme(self) -> None:
        try:
            icon_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "BuzzInstaller", "assets", "buzzinstaller.ico",
            )
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_size(num: int) -> str:
        if num < 1024:
            return f"{num} B"
        if num < 1024 * 1024:
            return f"{num / 1024:.1f} KB"
        if num < 1024 * 1024 * 1024:
            return f"{num / (1024 * 1024):.1f} MB"
        return f"{num / (1024 * 1024 * 1024):.2f} GB"

    def _on_radio_changed(self) -> None:
        self.browse_btn.setEnabled(self.custom_radio.isChecked())
        if self.path_radio.isChecked():
            self.dir_label.setText(str(get_default_install_dir()))

    def _browse_directory(self) -> None:
        current = self.dir_label.text()
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select Installation Directory",
            current or str(get_default_install_dir()),
        )
        if selected:
            self.dir_label.setText(selected)

    def _get_selected_install_dir(self) -> Path:
        text = self.dir_label.text().strip()
        return Path(text) if text else get_default_install_dir()

    def _is_path_install(self) -> bool:
        return self.path_radio.isChecked()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        self._current_state = state

        # Defaults
        self.progress_bar.setVisible(False)
        self.progress_detail.setVisible(False)
        self.retry_btn.setVisible(False)
        self.location_frame.setVisible(False)
        self.delete_btn.setVisible(False)
        self.install_btn.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.install_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)

        if state == self.STATE_CHECKING:
            self.install_btn.setEnabled(False)
            self.cancel_btn.setEnabled(False)
            self.status_label.setText("Checking for the latest version...")
            self.version_label.setText("")

        elif state == self.STATE_NOT_INSTALLED:
            self.location_frame.setVisible(True)
            self.install_btn.setText("Install BuzzInstaller")
            self.status_label.setText("BuzzInstaller is not installed.")
            self.version_label.setText("")

        elif state == self.STATE_UP_TO_DATE:
            self.delete_btn.setVisible(True)
            self.install_btn.setText("Reinstall")
            self.status_label.setText("BuzzInstaller is up to date.")
            self.version_label.setText("")

        elif state == self.STATE_UPDATE_AVAILABLE:
            self.delete_btn.setVisible(True)
            self.install_btn.setText("Update BuzzInstaller")
            self.status_label.setText(
                self._check_result.get("message", "Update available.")
            )
            inst = self._check_result.get("installed_version")
            lat = self._check_result.get("latest_version")
            parts = []
            if inst:
                parts.append(f"Installed: {inst}")
            if lat:
                parts.append(f"Latest: {lat}")
            self.version_label.setText("\n".join(parts))

        elif state == self.STATE_NEWER_INSTALLED:
            self.delete_btn.setVisible(True)
            self.install_btn.setText("Reinstall")
            self.status_label.setText(
                "Installed version is newer than the latest release."
            )
            inst = self._check_result.get("installed_version")
            lat = self._check_result.get("latest_version")
            parts = []
            if inst:
                parts.append(f"Installed: {inst}")
            if lat:
                parts.append(f"Latest: {lat}")
            self.version_label.setText("\n".join(parts))

        elif state == self.STATE_DOWNLOADING:
            self.install_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)
            self.progress_bar.setVisible(True)
            self.progress_detail.setVisible(True)
            self.progress_bar.setValue(0)
            self.progress_bar.setRange(0, 100)

        elif state == self.STATE_ERROR:
            self.retry_btn.setVisible(True)
            self.status_label.setText(
                self._check_result.get("message", "Unable to check for updates.")
            )
            self.version_label.setText("")

    # ------------------------------------------------------------------
    # Auto-check
    # ------------------------------------------------------------------
    def _start_auto_check(self) -> None:
        self._set_state(self.STATE_CHECKING)
        self._check_result = {}
        self.check_worker = CheckWorker(self.installer)
        self.check_worker.finished_check.connect(self._on_check_finished)
        self.check_worker.start()

    def _on_check_finished(self, result: dict) -> None:
        self._check_result = result
        self._set_state(result.get("state", "error"))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _start_install(self) -> None:
        self.installer.install_dir = self._get_selected_install_dir()
        self.installer.path_install = self._is_path_install()

        self._set_state(self.STATE_DOWNLOADING)
        self.status_label.setText("Downloading BuzzInstaller...")

        self.worker = InstallWorker(self.installer)
        self.worker.status_changed.connect(self._on_status)
        self.worker.progress_changed.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _start_delete(self) -> None:
        reply = QMessageBox.question(
            self,
            "Delete BuzzInstaller",
            "Are you sure you want to delete BuzzInstaller?\n\n"
            "This will remove the executable and Windows integration.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.install_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Deleting BuzzInstaller...")

        self.delete_worker = DeleteWorker()
        self.delete_worker.finished_delete.connect(self._on_delete_finished)
        self.delete_worker.start()

    def _cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.status_label.setText("Cancelling...")
            self.cancel_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------
    def _on_status(self, msg: str) -> None:
        self.status_label.setText(msg)

    def _on_progress(self, received: int, total: int) -> None:
        if total > 0:
            pct = int((received * 100) / total)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(pct)
            self.progress_bar.setFormat(f"{pct}%")
            self.progress_detail.setText(
                f"{self._format_size(received)} / {self._format_size(total)}"
            )
        else:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("")
            self.progress_detail.setText(self._format_size(received))

    def _on_finished(self) -> None:
        self.install_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("100%")
        self.progress_detail.setText("")
        self.status_label.setText("Done.")
        self._start_auto_check()

    def _on_failed(self, msg: str) -> None:
        self.install_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_detail.setVisible(False)
        self._check_result = {"state": "error", "message": f"Error: {msg}"}
        self._set_state(self.STATE_ERROR)

    def _on_delete_finished(self, ok: bool, error: str) -> None:
        if ok:
            self.status_label.setText("BuzzInstaller has been deleted.")
            self._check_result = {
                "state": "not_installed",
                "message": "BuzzInstaller is not installed.",
            }
            self._set_state(self.STATE_NOT_INSTALLED)
        else:
            self.status_label.setText(f"Deletion failed: {error}")
            self.install_btn.setEnabled(True)
            self.delete_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_ui(installer: Installer) -> int:
    """Run the installer UI.  Returns exit code."""
    app = QApplication.instance() or QApplication(sys.argv)
    win = InstallerWindow(installer)
    win.show()
    return app.exec()