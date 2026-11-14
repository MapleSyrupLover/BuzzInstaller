"""Qt interface for ``BuzzInstallerSetup.exe``."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from buzzcommon import metadata
from buzzcommon.versions import SETUP_VERSION

try:  # running as a script (PyInstaller / main.py)
    import setup_core
except ImportError:  # imported as a package during development/tests
    from . import setup_core

SURFACE = "#1E1E1E"
SURFACE_ALT = "#252526"
BORDER = "#3A3A3C"
TEXT = "#F2F2F2"
TEXT_MUTED = "#A0A0A5"
ACCENT = "#0078D4"
ACCENT_HOVER = "#1082FF"
DANGER = "#D13438"
SUCCESS = "#4CC26A"

WINDOW_QSS = f"""
QWidget {{
    background: {SURFACE};
    color: {TEXT};
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}}
QLabel#heading {{
    font-size: 22px;
    font-weight: 700;
}}
QLabel#subheading {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#section {{
    font-size: 12px;
    font-weight: 600;
    color: {TEXT_MUTED};
}}
QLabel#status {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#error {{
    color: {DANGER};
    font-size: 12px;
}}
QLineEdit {{
    background: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 7px 10px;
    color: {TEXT};
    font-size: 12px;
}}
QCheckBox {{
    color: {TEXT};
    font-size: 12px;
    spacing: 8px;
}}
QProgressBar {{
    background: {SURFACE_ALT};
    border: none;
    border-radius: 4px;
    height: 8px;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 4px;
}}
QPushButton {{
    background: {SURFACE_ALT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton:hover {{
    border-color: {ACCENT};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}
QPushButton#primary {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: white;
}}
QPushButton#primary:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#danger {{
    background: transparent;
    border-color: {DANGER};
    color: {DANGER};
}}
QPushButton#danger:hover {{
    background: rgba(209, 52, 56, 0.12);
}}
"""


def _icon_path() -> Optional[str]:
    base = getattr(sys, "_MEIPASS", None)
    candidates = []
    if base:
        candidates.append(os.path.join(base, "assets", "buzzinstaller.ico"))
    candidates.append(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "BuzzInstaller", "assets", "buzzinstaller.ico",
        )
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def _format_size(num: int) -> str:
    value = float(max(num, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class SetupWorker(QThread):
    """Run a setup action without freezing the window."""

    status = Signal(str)
    progress = Signal(int, int)
    installed = Signal(object)      # InstallOutcome
    deleted = Signal(bool, str)
    cleaned = Signal(bool, str)
    failed = Signal(str)

    def __init__(
        self,
        action: str,
        install_dir: Path,
        path_install: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._action = action
        self._install_dir = Path(install_dir)
        self._path_install = bool(path_install)

    def run(self) -> None:
        try:
            if self._action == "install":
                outcome = setup_core.install_latest(
                    self._install_dir,
                    path_install=self._path_install,
                    status_cb=self.status.emit,
                    progress_cb=self.progress.emit,
                )
                self.installed.emit(outcome)
            elif self._action == "delete":
                ok = setup_core.delete_installation(self._install_dir)
                self.deleted.emit(bool(ok), "" if ok else "Deletion failed.")
            elif self._action == "clean":
                results = setup_core.clean_installation()
                ok = all(bool(value) for value in results.values())
                self.cleaned.emit(ok, "" if ok else str(results))
        except setup_core.SetupError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class SetupWindow(QWidget):
    """Fresh-install window with delete and clean actions."""

    def __init__(self, install_dir: Optional[Path] = None, parent=None) -> None:
        super().__init__(parent)
        self._worker: Optional[SetupWorker] = None
        self._busy = False
        self._installed = False
        self._launch_exe: Optional[Path] = None
        self._install_dir = Path(install_dir) if install_dir else metadata.get_install_dir()

        self.setWindowTitle("BuzzInstaller Setup")
        icon = _icon_path()
        if icon:
            self.setWindowIcon(QIcon(icon))
        self.setStyleSheet(WINDOW_QSS)
        self.setMinimumWidth(620)
        self._build_ui()
        self._refresh_state()
        self.adjustSize()
        self.resize(640, self.sizeHint().height())

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(10)

        heading = QLabel("BuzzInstaller")
        heading.setObjectName("heading")
        layout.addWidget(heading)

        subheading = QLabel(
            "Setup installs the latest BuzzInstaller and its updater. "
            "Fresh installs only."
        )
        subheading.setObjectName("subheading")
        subheading.setWordWrap(True)
        layout.addWidget(subheading)

        layout.addSpacing(8)

        self.info = QLabel("")
        self.info.setObjectName("status")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        location_title = QLabel("Installation location")
        location_title.setObjectName("section")
        layout.addWidget(location_title)
        self.location_title = location_title

        location_row = QHBoxLayout()
        location_row.setSpacing(8)
        self.path_edit = QLineEdit(str(self._install_dir))
        self.path_edit.setReadOnly(True)
        location_row.addWidget(self.path_edit, 1)
        self.btn_browse = QPushButton("Browse…")
        self.btn_browse.clicked.connect(self._browse)
        location_row.addWidget(self.btn_browse)
        self.location_row = location_row
        layout.addLayout(location_row)

        self.chk_path = QCheckBox("Add BuzzInstaller to my PATH")
        layout.addWidget(self.chk_path)

        layout.addSpacing(6)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        layout.addStretch(1)

        # Primary actions
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_launch = QPushButton("Launch BuzzInstaller")
        self.btn_launch.setObjectName("primary")
        self.btn_launch.clicked.connect(self._launch_app)
        self.btn_launch.setVisible(False)
        actions.addWidget(self.btn_launch)

        actions.addStretch(1)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        actions.addWidget(self.btn_close)

        self.btn_install = QPushButton("Install BuzzInstaller")
        self.btn_install.setObjectName("primary")
        self.btn_install.clicked.connect(self._start_install)
        actions.addWidget(self.btn_install)
        layout.addLayout(actions)

        # Danger zone
        danger_row = QHBoxLayout()
        danger_row.setSpacing(8)
        danger_label = QLabel("Maintenance")
        danger_label.setObjectName("section")
        danger_row.addWidget(danger_label)
        danger_row.addStretch(1)
        self.btn_delete = QPushButton("Delete BuzzInstaller")
        self.btn_delete.setObjectName("danger")
        self.btn_delete.clicked.connect(self._start_delete)
        danger_row.addWidget(self.btn_delete)
        self.btn_clean = QPushButton("Clean all traces")
        self.btn_clean.setObjectName("danger")
        self.btn_clean.clicked.connect(self._start_clean)
        danger_row.addWidget(self.btn_clean)
        layout.addLayout(danger_row)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def _refresh_state(self) -> None:
        existing = setup_core.detect_existing(self._install_dir)
        self._installed = existing.installed
        has_progress = self._busy
        show_location = not existing.installed and not has_progress

        self.location_title.setVisible(show_location)
        self.path_edit.setVisible(show_location)
        self.btn_browse.setVisible(show_location)
        self.chk_path.setVisible(show_location)
        self.btn_install.setVisible(show_location)
        self.btn_delete.setVisible(existing.installed and not has_progress)

        if existing.installed:
            version = existing.version or "an unknown version"
            self.info.setText(
                f"BuzzInstaller {version} is already installed. "
                "This setup performs fresh installs only - use the "
                "update button inside BuzzInstaller to update it."
            )
            self.chk_path.setChecked(metadata.is_path_install())
        else:
            self.info.setText("BuzzInstaller is not installed on this PC.")
            self.chk_path.setChecked(metadata.is_path_install())

        self.adjustSize()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _browse(self) -> None:
        current = self.path_edit.text() or str(metadata.get_default_install_dir())
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose an installation folder", current
        )
        if chosen:
            self.path_edit.setText(chosen)
            self._install_dir = Path(chosen)
            self._refresh_state()

    def _start_install(self) -> None:
        if self._busy:
            return
        install_dir = Path(self.path_edit.text() or self._install_dir)
        try:
            install_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self, "Invalid folder",
                f"The installation folder cannot be created:\n{exc}",
            )
            return

        self._install_dir = install_dir
        self._busy = True
        self.status.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        self.btn_install.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.btn_clean.setEnabled(False)
        self.btn_launch.setVisible(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.bar.setValue(0)
        self.status.setText("Checking the latest release…")

        self._worker = SetupWorker(
            "install", install_dir, self.chk_path.isChecked(), parent=self
        )
        self._worker.status.connect(self._on_status)
        self._worker.progress.connect(self._on_progress)
        self._worker.installed.connect(self._on_installed)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _start_delete(self) -> None:
        if self._busy:
            return
        answer = QMessageBox.question(
            self,
            "Delete BuzzInstaller",
            "Delete BuzzInstaller?\n\n"
            "This removes the installation folder (including config, "
            "logs and cached transfers), shortcuts, PATH entry and the "
            "Windows integration.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._begin_maintenance("delete", "Deleting BuzzInstaller…")

    def _start_clean(self) -> None:
        if self._busy:
            return
        answer = QMessageBox.question(
            self,
            "Clean all BuzzInstaller traces",
            "This removes EVERY BuzzInstaller trace:\n\n"
            "  - %LOCALAPPDATA%\\BuzzInstaller\n"
            "  - Installer metadata\n"
            "  - C:\\BuzzInstaller shortcut folder\n"
            "  - BZ:// protocol and Explorer menu entries\n"
            "  - Autostart entries and PATH entries\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._begin_maintenance("clean", "Cleaning BuzzInstaller…")

    def _begin_maintenance(self, action: str, status: str) -> None:
        self._busy = True
        self.status.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        self.status.setText(status)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.btn_install.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.btn_clean.setEnabled(False)
        self.btn_launch.setVisible(False)

        self._worker = SetupWorker(action, self._install_dir, parent=self)
        self._worker.status.connect(self._on_status)
        self._worker.deleted.connect(self._on_deleted)
        self._worker.cleaned.connect(self._on_cleaned)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _launch_app(self) -> None:
        if self._launch_exe is None:
            return
        try:
            setup_core.launch_installed(self._launch_exe)
        except Exception as exc:
            self.status.setText(f"Could not launch BuzzInstaller: {exc}")

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------
    def _on_status(self, message: str) -> None:
        self.status.setText(message)

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(min(100, int(done * 100 / total)))
        else:
            self.bar.setRange(0, 0)

    def _on_installed(self, outcome) -> None:
        self._busy = False
        self.bar.setVisible(False)
        self._launch_exe = outcome.exe_path
        self.status.setStyleSheet(f"color: {SUCCESS}; font-size: 12px;")
        self.status.setText(outcome.message)
        self.btn_install.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_clean.setEnabled(True)
        self.btn_launch.setVisible(True)
        self.btn_launch.setObjectName("primary")
        self._refresh_state()
        self._launch_app()

    def _on_deleted(self, ok: bool, error: str) -> None:
        self._busy = False
        self.bar.setVisible(False)
        if ok:
            self.status.setStyleSheet(f"color: {SUCCESS}; font-size: 12px;")
            self.status.setText("BuzzInstaller has been deleted.")
        else:
            self.status.setStyleSheet(f"color: {DANGER}; font-size: 12px;")
            self.status.setText(f"Deletion failed: {error}")
        self._reset_controls()
        self._refresh_state()

    def _on_cleaned(self, ok: bool, error: str) -> None:
        self._busy = False
        self.bar.setVisible(False)
        if ok:
            self.status.setStyleSheet(f"color: {SUCCESS}; font-size: 12px;")
            self.status.setText("BuzzInstaller has been cleaned.")
        else:
            self.status.setStyleSheet(f"color: {DANGER}; font-size: 12px;")
            self.status.setText(f"Clean failed: {error}")
        self._reset_controls()
        self._refresh_state()

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.bar.setVisible(False)
        self.status.setStyleSheet(f"color: {DANGER}; font-size: 12px;")
        self.status.setText(message)
        self._reset_controls()
        self._refresh_state()

    def _reset_controls(self) -> None:
        self.btn_install.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.btn_delete.setEnabled(True)
        self.btn_clean.setEnabled(True)

    def _on_finished(self) -> None:
        self._worker = None

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._busy:
            event.ignore()
            self.status.setText("Please wait for the current action to finish.")
            return
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def version_text(self) -> str:
        return SETUP_VERSION
