"""Compact recent-transfer list used on the Uploads and Downloads pages."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QWidget,
)

from core.history import format_relative_time, history
from system.clipboard_service import clipboard_service
from ui.widgets import Card, NestedCard, clear_layout
from utils.file_utils import format_size


class RecentTransfersCard(Card):
    """Show the latest few history entries for one transfer direction."""

    def __init__(self, kind: str, title: str, empty_text: str,
                 limit: int = 5, parent=None) -> None:
        super().__init__(title, parent=parent)
        self._kind = str(kind)
        self._empty_text = empty_text
        self._limit = max(1, int(limit))
        self._toast = None
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("ghost")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self.clear_history)
        self.add_header_widget(self.btn_clear)
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        clear_layout(self.body)
        try:
            entries = history.recent(self._limit, kind=self._kind)
        except Exception:
            entries = []
        if not entries:
            label = QLabel(self._empty_text)
            label.setObjectName("muted")
            self.body.addWidget(label)
            self.btn_clear.setVisible(False)
            return
        self.btn_clear.setVisible(True)
        for entry in entries:
            self.body.addWidget(self._make_row(entry))

    def clear_history(self) -> None:
        try:
            for entry in history.entries():
                if entry.kind == self._kind:
                    history.remove(entry.uid)
        except Exception:
            return
        self.refresh()
        window = self.window()
        if hasattr(window, "show_toast"):
            label = "uploads" if self._kind == "upload" else "downloads"
            window.show_toast(f"Cleared recent {label}.")

    # ------------------------------------------------------------------
    def _make_row(self, entry) -> QWidget:
        card = NestedCard()
        card.body.setContentsMargins(10, 8, 10, 8)
        row = QHBoxLayout()
        row.setSpacing(10)

        glyph = QLabel("↑" if entry.kind == "upload" else "↓")
        glyph.setStyleSheet("font-size: 15px; font-weight: 700;")
        glyph.setFixedWidth(16)
        row.addWidget(glyph)

        text = QWidget()
        from PySide6.QtWidgets import QVBoxLayout
        text_layout = QVBoxLayout(text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)
        name = QLabel(entry.name or entry.code or "Transfer")
        name.setStyleSheet("font-weight: 600;")
        name.setToolTip(entry.bz_url or entry.path)
        text_layout.addWidget(name)
        meta = QLabel(
            f"{format_relative_time(entry.timestamp)} · {format_size(entry.size)}"
        )
        meta.setObjectName("caption")
        text_layout.addWidget(meta)
        row.addWidget(text, 1)

        if entry.kind == "upload" and entry.bz_url:
            copy = QPushButton("Copy link")
            copy.setObjectName("secondary")
            copy.setFixedHeight(28)
            copy.setCursor(Qt.PointingHandCursor)
            copy.clicked.connect(
                lambda _=False, url=entry.bz_url: self._copy(url)
            )
            row.addWidget(copy)
        elif entry.kind == "download":
            target = entry.extracted_to or entry.path
            if target:
                open_btn = QPushButton("Open")
                open_btn.setObjectName("secondary")
                open_btn.setFixedHeight(28)
                open_btn.setCursor(Qt.PointingHandCursor)
                open_btn.clicked.connect(
                    lambda _=False, path=target: self._open(path)
                )
                row.addWidget(open_btn)

        card.body.addLayout(row)
        return card

    def _copy(self, value: str) -> None:
        window = self.window()
        if clipboard_service.copy_auto(value):
            if hasattr(window, "show_toast"):
                window.show_toast("Link copied to clipboard.")
        elif hasattr(window, "show_toast"):
            window.show_toast("Could not copy the link.", error=True)

    def _open(self, value: str) -> None:
        try:
            path = Path(value)
            if path.is_dir():
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif path.exists():
                import subprocess
                subprocess.Popen(["explorer.exe", "/select,", str(path)])
        except Exception:
            window = self.window()
            if hasattr(window, "show_toast"):
                window.show_toast("Could not open the file.", error=True)
