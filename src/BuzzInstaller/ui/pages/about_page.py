"""About page — version, updates, links and diagnostics."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from config.paths import get_app_dir, get_log_path
from core import updater
from ui.theme import get_palette_from_config
from ui.widgets import Card, PageHeader, divider
from utils.logging import get_logger

logger = get_logger(__name__)

REPO_URL = "https://github.com/MapleSyrupLover/BuzzInstaller"
ISSUES_URL = f"{REPO_URL}/issues"
BUZZ_URL = "https://buzzheavier.com"


def _logo_pixmap() -> Optional[QPixmap]:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parents[2]
    for name in ("buzzinstaller.png", "buzzinstaller.ico"):
        path = base / "assets" / name
        if path.is_file():
            pix = QPixmap(str(path))
            if not pix.isNull():
                return pix.scaled(
                    72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
    return None


class AboutPage(QWidget):
    """Product information and manual update controls."""

    checkUpdatesRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(14)

        outer.addWidget(PageHeader("About", "BuzzInstaller — built for the BuzzHeavier community."))

        hero = Card()
        row = QHBoxLayout()
        row.setSpacing(16)

        logo = _logo_pixmap()
        if logo is not None:
            logo_label = QLabel()
            logo_label.setPixmap(logo)
            logo_label.setFixedSize(72, 72)
            logo_label.setAlignment(Qt.AlignCenter)
            row.addWidget(logo_label)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title = QLabel("BuzzInstaller")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        text_col.addWidget(title)
        version = QLabel(f"Version {updater.current_version()}")
        version.setObjectName("muted")
        text_col.addWidget(version)
        mode = "Installed build" if updater.is_frozen() else "Running from source"
        mode_label = QLabel(mode)
        mode_label.setObjectName("caption")
        text_col.addWidget(mode_label)
        row.addLayout(text_col, 1)

        self.btn_check = QPushButton("Check for updates")
        self.btn_check.setObjectName("primary")
        self.btn_check.setCursor(Qt.PointingHandCursor)
        self.btn_check.clicked.connect(self.checkUpdatesRequested.emit)
        row.addWidget(self.btn_check)
        hero.body.addLayout(row)
        outer.addWidget(hero)

        # Update status ---------------------------------------------------
        update_card = Card("Updates")
        self.update_status = QLabel("Updates are checked automatically when enabled.")
        self.update_status.setObjectName("muted")
        self.update_status.setWordWrap(True)
        update_card.body.addWidget(self.update_status)
        update_card.body.addWidget(divider())

        links = QHBoxLayout()
        links.setSpacing(8)
        for label, url in (
            ("Open releases", updater.RELEASES_PAGE),
            ("Source code", REPO_URL),
            ("Report an issue", ISSUES_URL),
            ("BuzzHeavier", BUZZ_URL),
        ):
            btn = QPushButton(label)
            btn.setObjectName("secondary")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, u=url: self._open_url(u))
            links.addWidget(btn)
        links.addStretch(1)
        update_card.body.addLayout(links)
        outer.addWidget(update_card)

        # Diagnostics -----------------------------------------------------
        diag = Card("Files & diagnostics", "Open the folders where BuzzInstaller keeps its data.")
        self._add_path_row(diag, "Config folder", str(get_app_dir()))
        self._add_path_row(diag, "Logs", str(get_log_path().parent))
        if updater.is_frozen():
            self._add_path_row(diag, "Install folder", str(updater.get_install_dir()))
        diag.body.addWidget(divider())

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for label, path in (
            ("Open config folder", get_app_dir()),
            ("Open logs", get_log_path().parent),
        ):
            btn = QPushButton(label)
            btn.setObjectName("secondary")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, p=str(path): self._open_path(p))
            buttons.addWidget(btn)
        buttons.addStretch(1)
        diag.body.addLayout(buttons)
        outer.addWidget(diag)

        footer = QLabel(
            "MIT licensed · Uses PySide6 and the public BuzzHeavier API · "
            "Not affiliated with BuzzHeavier."
        )
        footer.setObjectName("caption")
        footer.setWordWrap(True)
        outer.addWidget(footer)
        outer.addStretch(1)

    def _add_path_row(self, card: Card, label: str, value: str) -> None:
        row = QHBoxLayout()
        row.setSpacing(10)
        key = QLabel(label)
        key.setObjectName("muted")
        key.setMinimumWidth(110)
        row.addWidget(key)
        value_label = QLabel(value)
        value_label.setObjectName("caption")
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value_label.setWordWrap(True)
        row.addWidget(value_label, 1)
        card.body.addLayout(row)

    # ------------------------------------------------------------------
    def _open_url(self, url: str) -> None:
        try:
            os.startfile(url)  # type: ignore[attr-defined]
        except Exception:
            webbrowser.open(url)

    def _open_path(self, path: str) -> None:
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Could not open %s: %s", path, exc)

    def set_update_status(self, text: str, tone: str = "muted") -> None:
        self.update_status.setText(text)
        p = get_palette_from_config()
        color = {
            "error": p.error,
            "success": p.success,
            "warning": p.warning,
            "accent": p.accent,
        }.get(tone, p.text_secondary)
        self.update_status.setStyleSheet(f"color: {color};")
