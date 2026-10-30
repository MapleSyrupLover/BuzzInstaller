#!/usr/bin/env python
"""
BuzzInstaller Developer Studio.

A small GUI that can rebuild BuzzInstaller and publish a new release to
GitHub in one click.  Every action streams its output to the log panel.

Run with:
    python tools/dev_gui.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(ROOT / "src" / "BuzzInstaller"))

import build  # noqa: E402
import release  # noqa: E402

try:
    from ui.theme import DARK, app_qss
    from ui.widgets import Card, CheckBox, divider
except Exception as exc:  # pragma: no cover - dev tooling fallback
    raise SystemExit(f"Could not load BuzzInstaller theme: {exc}")

APP_MAIN = ROOT / "src" / "BuzzInstaller" / "main.py"
DEV_LOG = ROOT / "build" / "devtools" / "dev_gui.log"


class TaskWorker(QThread):
    """Run a callable in the background, streaming log lines."""

    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, task: Callable[[Callable[[str], None]], object], parent=None) -> None:
        super().__init__(parent)
        self._task = task

    def run(self) -> None:
        try:
            result = self._task(self.log.emit)
            self.done.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


class StatusRow(QWidget):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        key = QLabel(label)
        key.setObjectName("muted")
        key.setMinimumWidth(92)
        row.addWidget(key)
        self.value = QLabel("—")
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.value, 1)


class DeveloperStudio(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BuzzInstaller Developer Studio")
        icon = build.APP_ICON
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1180, 800)
        self.setMinimumSize(980, 680)

        self._worker: Optional[TaskWorker] = None
        self._current_version = "0.0.0"
        self._build_ui()
        self.refresh_status()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(20, 18, 20, 18)
        row.setSpacing(16)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)

        title = QLabel("Developer Studio")
        title.setObjectName("pageTitle")
        left.addWidget(title)
        subtitle = QLabel("Build, version and publish BuzzInstaller without leaving this window.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        left.addWidget(subtitle)

        left.addWidget(self._build_status_card())
        left.addWidget(self._build_release_card())
        left.addWidget(self._build_actions_card())
        left.addStretch(1)

        left_wrap = QWidget()
        left_wrap.setLayout(left)
        left_wrap.setMinimumWidth(430)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setFixedWidth(468)
        left_scroll.setWidget(left_wrap)
        row.addWidget(left_scroll)

        row.addWidget(self._build_log_card(), 1)

    def _build_status_card(self) -> Card:
        card = Card("Repository")
        self.row_version = StatusRow("Version")
        self.row_branch = StatusRow("Branch")
        self.row_tree = StatusRow("Working tree")
        self.row_tag = StatusRow("Last tag")
        self.row_gh = StatusRow("GitHub CLI")
        for widget in (
            self.row_version, self.row_branch, self.row_tree,
            self.row_tag, self.row_gh,
        ):
            card.body.addWidget(widget)

        refresh = QPushButton("Refresh status")
        refresh.setObjectName("secondary")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self.refresh_status)
        card.body.addWidget(refresh)
        return card

    def _build_release_card(self) -> Card:
        card = Card("Release pipeline", "Rebuild the app and publish it to GitHub.")

        version_row = QHBoxLayout()
        version_row.setSpacing(8)
        version_label = QLabel("Bump")
        version_label.setObjectName("muted")
        version_row.addWidget(version_label)
        self.bump_combo = QComboBox()
        self.bump_combo.addItems(["Patch", "Minor", "Major", "Keep version"])
        self.bump_combo.currentIndexChanged.connect(self._update_version_preview)
        version_row.addWidget(self.bump_combo, 1)
        card.body.addLayout(version_row)

        self.version_preview = QLabel("")
        self.version_preview.setObjectName("muted")
        card.body.addWidget(self.version_preview)

        notes_label = QLabel("Release notes (optional)")
        notes_label.setObjectName("muted")
        card.body.addWidget(notes_label)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(
            "Summarize what changed. Commit subjects are appended automatically."
        )
        self.notes.setFixedHeight(84)
        card.body.addWidget(self.notes)

        self.chk_build = CheckBox("Build app bundle (BuzzInstaller.zip)")
        self.chk_build.setChecked(True)
        self.chk_installer = CheckBox("Build installer (BuzzInstallerInstaller.exe)")
        self.chk_installer.setChecked(True)
        self.chk_stop = CheckBox("Stop running BuzzInstaller before building")
        self.chk_stop.setChecked(True)
        self.chk_commit = CheckBox("Commit and tag the release")
        self.chk_commit.setChecked(True)
        self.chk_push = CheckBox("Push source and tag to GitHub")
        self.chk_push.setChecked(True)
        self.chk_release = CheckBox("Create GitHub release and upload assets")
        self.chk_release.setChecked(True)
        self.chk_draft = CheckBox("Create as draft release")
        for widget in (
            self.chk_build, self.chk_installer, self.chk_stop,
            self.chk_commit, self.chk_push, self.chk_release, self.chk_draft,
        ):
            card.body.addWidget(widget)

        self.chk_release.toggled.connect(self._on_release_toggled)
        card.body.addWidget(divider())

        self.btn_publish = QPushButton("Rebuild & Publish")
        self.btn_publish.setObjectName("primary")
        self.btn_publish.setMinimumHeight(42)
        self.btn_publish.setCursor(Qt.PointingHandCursor)
        self.btn_publish.clicked.connect(self.run_publish)
        card.body.addWidget(self.btn_publish)

        secondary = QHBoxLayout()
        secondary.setSpacing(8)
        self.btn_build = QPushButton("Build only")
        self.btn_build.setObjectName("secondary")
        self.btn_build.setCursor(Qt.PointingHandCursor)
        self.btn_build.clicked.connect(self.run_build)
        secondary.addWidget(self.btn_build)

        self.btn_push = QPushButton("Push only")
        self.btn_push.setObjectName("secondary")
        self.btn_push.setCursor(Qt.PointingHandCursor)
        self.btn_push.clicked.connect(self.run_push)
        secondary.addWidget(self.btn_push)
        card.body.addLayout(secondary)
        return card

    def _build_actions_card(self) -> Card:
        card = Card()
        row = QHBoxLayout()
        row.setSpacing(8)
        for label, callback in (
            ("Run app", self.run_app),
            ("Open dist", self.open_dist),
            ("Open repo", self.open_repo),
        ):
            button = QPushButton(label)
            button.setObjectName("ghost")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _build_log_card(self) -> Card:
        card = Card("Build log")
        card.setMinimumWidth(480)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(8)
        self.log_status = QLabel("Ready.")
        self.log_status.setObjectName("muted")
        tools_row.addWidget(self.log_status, 1)
        for label, callback in (
            ("Copy", self.copy_log),
            ("Clear", self.clear_log),
            ("Open log", self.open_dev_log),
        ):
            button = QPushButton(label)
            button.setObjectName("ghost")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(callback)
            tools_row.addWidget(button)
        card.body.addLayout(tools_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        card.body.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("logConsole")
        self.log.setStyleSheet(
            "QPlainTextEdit#logConsole {"
            "background: #0A0E14; color: #C9D4E3; border: 1px solid #242D3D;"
            'border-radius: 8px; padding: 10px;'
            'font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px; }'
        )
        card.body.addWidget(self.log, 1)
        return card

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def refresh_status(self) -> None:
        try:
            app_version, installer_version = release.read_versions()
        except Exception as exc:
            self._append(f"Could not read version: {exc}")
            return
        self._current_version = app_version
        self.row_version.value.setText(
            f"{app_version} (installer {installer_version})"
        )
        try:
            self.row_branch.value.setText(release.git_branch())
            self.row_tree.value.setText(
                "modified — will be committed" if release.git_is_dirty() else "clean"
            )
            self.row_tag.value.setText(release.git_last_tag() or "(none)")
        except Exception as exc:
            self.row_branch.value.setText("—")
            self.row_tree.value.setText(str(exc)[:60])
        try:
            self.row_gh.value.setText(
                "authenticated" if release.gh_authenticated() else "not authenticated"
            )
        except Exception:
            self.row_gh.value.setText("unavailable")
        self._update_version_preview()

    def _update_version_preview(self) -> None:
        version = self._current_version
        index = self.bump_combo.currentIndex()
        try:
            if index == 0:
                version = release.increment_version(version, "patch")
            elif index == 1:
                version = release.increment_version(version, "minor")
            elif index == 2:
                version = release.increment_version(version, "major")
        except Exception:
            version = self._current_version
        self.version_preview.setText(
            f"Current {self._current_version}  →  Release v{version}"
        )

    def _on_release_toggled(self, checked: bool) -> None:
        if checked:
            self.chk_commit.setChecked(True)
            self.chk_push.setChecked(True)
        self.chk_commit.setEnabled(not checked)
        self.chk_push.setEnabled(not checked)

    # ------------------------------------------------------------------
    # Task plumbing
    # ------------------------------------------------------------------
    def _append(self, line: str) -> None:
        self.log.appendPlainText(line.rstrip())
        try:
            DEV_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(DEV_LOG, "a", encoding="utf-8") as handle:
                handle.write(line.rstrip() + "\n")
        except OSError:
            pass

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self.progress.setVisible(busy)
        self.log_status.setText(label or ("Working…" if busy else "Ready."))
        for button in (
            self.btn_publish, self.btn_build, self.btn_push,
        ):
            button.setEnabled(not busy)

    def _start_task(self, label: str, task) -> None:
        if self._worker is not None:
            return
        self._append("")
        self._append("=" * 70)
        self._append(label)
        self._append("=" * 70)
        self._set_busy(True, label)
        self._worker = TaskWorker(task, parent=self)
        self._worker.log.connect(self._append)
        self._worker.done.connect(self._on_task_done)
        self._worker.failed.connect(self._on_task_failed)
        self._worker.finished.connect(self._on_task_finished)
        self._worker.start()

    def _on_task_done(self, result) -> None:
        self._append("")
        self._append("Finished successfully.")
        self._set_busy(False, "Finished.")
        self.refresh_status()
        if isinstance(result, dict) and result.get("release_url"):
            QMessageBox.information(
                self, "Release published",
                f"BuzzInstaller v{result.get('version')} was published.\n\n"
                f"{result['release_url']}",
            )

    def _on_task_failed(self, message: str) -> None:
        self._append("")
        self._append(message)
        self._set_busy(False, "Failed — see the log.")
        QMessageBox.critical(
            self, "Operation failed",
            "The operation failed. See the log panel for details.",
        )

    def _on_task_finished(self) -> None:
        self._worker = None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _bump_key(self) -> str:
        return {
            0: "patch", 1: "minor", 2: "major", 3: "keep",
        }.get(self.bump_combo.currentIndex(), "patch")

    def run_publish(self) -> None:
        notes = self.notes.toPlainText().strip()
        do_build = self.chk_build.isChecked()
        build_installer = self.chk_installer.isChecked()
        do_commit = self.chk_commit.isChecked()
        do_push = self.chk_push.isChecked()
        do_release = self.chk_release.isChecked()
        draft = self.chk_draft.isChecked()
        kill = self.chk_stop.isChecked()
        bump = self._bump_key()

        if not do_build and not build.artifact_paths():
            QMessageBox.warning(
                self, "No build",
                "Build app is unchecked and no artifacts exist in dist/. "
                "Enable 'Build app bundle' or build once first.",
            )
            return
        if do_release and not (do_commit and do_push):
            QMessageBox.warning(
                self, "Release needs a push",
                "To publish a GitHub release the changes and tag must be committed and pushed.",
            )
            return
        if do_release:
            answer = QMessageBox.question(
                self, "Publish release",
                "This will bump the version, build, commit, tag, push and "
                "create a public GitHub release.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        def task(log):
            return release.publish(
                bump=bump,
                notes=notes,
                do_build=do_build,
                build_installer=build_installer,
                make_zip=True,
                do_commit=do_commit,
                do_push=do_push,
                do_release=do_release,
                draft=draft,
                kill_running=kill,
                log=log,
            )

        self._start_task("Rebuild & Publish", task)

    def run_build(self) -> None:
        def task(log):
            return build.build_all(
                log=log,
                build_application=True,
                build_updater=self.chk_installer.isChecked(),
                make_archive=True,
                kill_running=self.chk_stop.isChecked(),
            )
        self._start_task("Build only", task)

    def run_push(self) -> None:
        message = self.notes.toPlainText().strip().splitlines()
        message = message[0] if message else "Update source"

        def task(log):
            return release.push_changes(message=message, log=log)

        self._start_task("Commit & push", task)

    def run_app(self) -> None:
        try:
            subprocess.Popen(
                [sys.executable, str(APP_MAIN)],
                cwd=str(APP_MAIN.parent),
            )
            self._append("Launched BuzzInstaller from source.")
        except OSError as exc:
            self._append(f"Could not launch the app: {exc}")

    def open_dist(self) -> None:
        target = build.DIST if build.DIST.exists() else ROOT
        os.startfile(str(target))  # type: ignore[attr-defined]

    def open_repo(self) -> None:
        os.startfile(str(ROOT))  # type: ignore[attr-defined]

    def open_dev_log(self) -> None:
        try:
            DEV_LOG.parent.mkdir(parents=True, exist_ok=True)
            DEV_LOG.touch(exist_ok=True)
            os.startfile(str(DEV_LOG))  # type: ignore[attr-defined]
        except OSError as exc:
            self._append(f"Could not open the log: {exc}")

    def copy_log(self) -> None:
        QApplication.clipboard().setText(self.log.toPlainText())
        self.log_status.setText("Log copied.")

    def clear_log(self) -> None:
        self.log.clear()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("BuzzInstaller Developer Studio")
    app.setStyleSheet(app_qss(DARK))
    window = DeveloperStudio()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
