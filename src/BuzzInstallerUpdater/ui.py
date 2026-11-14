"""Qt interface for BuzzInstallerUpdater."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from buzzcommon import install
from buzzcommon.release import REPO_URL
from buzzcommon.versions import UPDATER_VERSION

try:  # running as a script (PyInstaller / main.py)
    from updater_core import UpdateError, UpdateResult, run_update
except ImportError:  # imported as a package during development/tests
    from .updater_core import UpdateError, UpdateResult, run_update

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
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
    font-size: 21px;
    font-weight: 700;
}}
QLabel#subheading {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#status {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#error {{
    color: {DANGER};
    font-size: 12px;
}}
QTextEdit {{
    background: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px;
    color: {TEXT};
    font-size: 12px;
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
    background: {SURFACE};
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
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _open_url(url: str) -> None:
    if not url:
        return
    try:
        os.startfile(url)  # type: ignore[attr-defined]
    except Exception:
        try:
            webbrowser.open(url)
        except Exception:
            pass


class UpdateWorker(QThread):
    """Run the update pipeline without blocking the UI."""

    status = Signal(str)
    progress = Signal(int, int)
    succeeded = Signal(object)   # UpdateResult
    failed = Signal(str)

    def __init__(
        self,
        install_dir: Path,
        wait_pid: int,
        current_version: str,
        launch_exe: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._install_dir = Path(install_dir)
        self._wait_pid = int(wait_pid or 0)
        self._current_version = current_version
        self._launch_exe = Path(launch_exe)

    def run(self) -> None:
        try:
            result = run_update(
                self._install_dir,
                current_version=self._current_version or None,
                wait_pid=self._wait_pid or None,
                launch_exe=self._launch_exe,
                status_cb=self.status.emit,
                progress_cb=self.progress.emit,
            )
            self.succeeded.emit(result)
        except UpdateError as exc:
            self._relaunch_app()
            self.failed.emit(str(exc))
        except Exception as exc:  # unexpected - never leave the app closed
            self._relaunch_app()
            self.failed.emit(f"Unexpected error: {exc}")

    def _relaunch_app(self) -> None:
        try:
            install.launch_app(self._launch_exe)
        except install.InstallError:
            pass


class UpdaterWindow(QWidget):
    """Small progress window shown while the update is applied."""

    def __init__(
        self,
        install_dir: Path,
        wait_pid: int,
        current_version: str,
        target_version: str,
        launch_exe: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._install_dir = Path(install_dir)
        self._wait_pid = int(wait_pid or 0)
        self._current_version = current_version or "unknown"
        self._target_version = target_version or ""
        self._launch_exe = Path(launch_exe)
        self._worker: Optional[UpdateWorker] = None
        self._busy = False
        self._changelog_url = ""

        self.setWindowTitle("BuzzInstaller Updater")
        icon = _icon_path()
        if icon:
            self.setWindowIcon(QIcon(icon))
        self.setStyleSheet(WINDOW_QSS)
        self.setMinimumWidth(560)
        self._build_ui()
        self.adjustSize()
        self.resize(580, self.sizeHint().height())

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(10)

        self.heading = QLabel("Updating BuzzInstaller")
        self.heading.setObjectName("heading")
        layout.addWidget(self.heading)

        self.subheading = QLabel(
            f"Version {self._current_version} -> {self._target_version or 'latest'}"
        )
        self.subheading.setObjectName("subheading")
        layout.addWidget(self.subheading)

        layout.addSpacing(4)

        self.status = QLabel("Starting...")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        layout.addWidget(self.bar)

        self.detail = QLabel("")
        self.detail.setObjectName("status")
        layout.addWidget(self.detail)

        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setFixedHeight(150)
        self.notes.setVisible(False)
        layout.addWidget(self.notes)

        layout.addSpacing(6)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.btn_changelog = QPushButton("View changelogs")
        self.btn_changelog.clicked.connect(self._open_changelog)
        self.btn_changelog.setVisible(False)
        buttons.addWidget(self.btn_changelog)

        self.btn_github = QPushButton("View GitHub")
        self.btn_github.clicked.connect(lambda: _open_url(REPO_URL))
        self.btn_github.setVisible(False)
        buttons.addWidget(self.btn_github)

        buttons.addStretch(1)

        self.btn_retry = QPushButton("Try again")
        self.btn_retry.setObjectName("primary")
        self.btn_retry.clicked.connect(self._start)
        self.btn_retry.setVisible(False)
        buttons.addWidget(self.btn_retry)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        self.btn_close.setVisible(False)
        buttons.addWidget(self.btn_close)

        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    # Update lifecycle
    # ------------------------------------------------------------------
    def start_update(self) -> None:
        self._start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._busy and self._worker is None:
            self.start_update()

    def _start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._busy = True
        self._changelog_url = ""
        self.btn_retry.setVisible(False)
        self.btn_close.setVisible(False)
        self.btn_changelog.setVisible(False)
        self.btn_github.setVisible(False)
        self.notes.setVisible(False)
        self.heading.setText("Updating BuzzInstaller")
        self.heading.setStyleSheet("")
        self.status.setObjectName("status")
        self.status.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        self.status.setText("Checking for the latest version...")
        self.detail.setText("")
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)

        self._worker = UpdateWorker(
            self._install_dir,
            self._wait_pid,
            self._current_version,
            self._launch_exe,
            parent=self,
        )
        self._worker.status.connect(self._on_status)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_status(self, message: str) -> None:
        self.status.setText(message)

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(min(100, int(done * 100 / total)))
            self.detail.setText(
                f"{_format_size(done)} of {_format_size(total)}"
            )
        else:
            self.bar.setRange(0, 0)
            self.detail.setText(_format_size(done))

    def _on_succeeded(self, result: UpdateResult) -> None:
        self._busy = False
        self.bar.setVisible(False)
        self.detail.setText("")
        self._changelog_url = result.release_url or ""
        self.btn_github.setVisible(True)
        self.btn_close.setVisible(True)
        self.btn_close.setObjectName("primary")

        if result.updated:
            self.heading.setText("Update installed")
            self.heading.setStyleSheet(f"color: {SUCCESS};")
            self.status.setText(result.message)
            if result.notes.strip():
                self.notes.setPlainText(result.notes[:8000])
                self.notes.setVisible(True)
            self.btn_changelog.setVisible(True)
        else:
            self.heading.setText("You're up to date")
            self.heading.setStyleSheet(f"color: {SUCCESS};")
            self.status.setText(
                f"BuzzInstaller {result.installed_version} is already "
                "the latest version. The app has been restarted."
            )
            self.btn_changelog.setVisible(True)
            self.btn_close.setObjectName("primary")

        self.adjustSize()

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.bar.setVisible(False)
        self.detail.setText("")
        self.heading.setText("Update failed")
        self.heading.setStyleSheet(f"color: {DANGER};")
        self.status.setText(message)
        self.btn_retry.setVisible(True)
        self.btn_github.setVisible(True)
        self.btn_close.setVisible(True)
        self.btn_close.setObjectName("")
        self.adjustSize()

    def _on_finished(self) -> None:
        self._worker = None

    # ------------------------------------------------------------------
    # Buttons / window
    # ------------------------------------------------------------------
    def _open_changelog(self) -> None:
        from buzzcommon.release import RELEASES_PAGE

        _open_url(self._changelog_url or RELEASES_PAGE)

    def closeEvent(self, event) -> None:
        if self._busy:
            event.ignore()
            self.status.setText(
                "The update is still running. Please wait for it to finish."
            )
            return
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def version_text(self) -> str:
        return UPDATER_VERSION
