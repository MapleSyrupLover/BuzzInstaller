"""Dashboard home page — quick actions, stats and recent activity."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from core.buzzheavier_api import extract_code, is_valid_code
from core.history import format_relative_time, history
from core import updater
from ui.theme import get_palette_from_config
from ui.widgets import (
    Card, DropZone, PageHeader, StatCard, clear_layout, divider,
)

HOTKEY_LABELS = {True: "Active", False: "Disabled"}


def icon_tile(glyph: str, size: int = 42) -> QFrame:
    tile = QFrame()
    tile.setObjectName("iconTile")
    tile.setFixedSize(size, size)
    layout = QVBoxLayout(tile)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(glyph)
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet(f"font-size: {int(size * 0.48)}px;")
    layout.addWidget(label)
    return tile


class HomePage(QWidget):
    """Landing page with two primary actions and a live activity feed."""

    navigate = Signal(str)
    sendRequested = Signal(list)
    receiveRequested = Signal(str)
    updateRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._update_info = None
        self._history_version = -1
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(18)

        header = PageHeader(
            "Overview",
            "Send files, receive BZ links and keep an eye on recent transfers.",
        )
        self.btn_history = QPushButton("View history")
        self.btn_history.setObjectName("secondary")
        self.btn_history.setCursor(Qt.PointingHandCursor)
        self.btn_history.clicked.connect(lambda: self.navigate.emit("history"))
        header.add_action(self.btn_history)
        outer.addWidget(header)

        # Update banner ---------------------------------------------------
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        banner_row = QHBoxLayout(self.banner)
        banner_row.setContentsMargins(14, 10, 14, 10)
        banner_row.setSpacing(10)
        self.banner_label = QLabel("")
        self.banner_label.setObjectName("muted")
        banner_row.addWidget(self.banner_label, 1)
        self.btn_update = QPushButton("Install update")
        self.btn_update.setObjectName("primary")
        self.btn_update.setFixedHeight(30)
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self.btn_update.clicked.connect(self.updateRequested.emit)
        banner_row.addWidget(self.btn_update)
        self.btn_dismiss = QPushButton("Later")
        self.btn_dismiss.setObjectName("ghost")
        self.btn_dismiss.setFixedHeight(30)
        self.btn_dismiss.setCursor(Qt.PointingHandCursor)
        self.btn_dismiss.clicked.connect(self.dismiss_update)
        banner_row.addWidget(self.btn_dismiss)
        self.banner.setVisible(False)
        outer.addWidget(self.banner)

        # Hero actions ----------------------------------------------------
        hero = QHBoxLayout()
        hero.setSpacing(16)

        # Send card
        send_card = Card(
            "Send files", "Upload anything to BuzzHeavier and get a BZ link."
        )
        send_card.setMinimumHeight(300)
        self.drop_zone = DropZone(
            "Drop files or folders",
            "Release to upload — folders are zipped automatically",
        )
        self.drop_zone.setMinimumHeight(138)
        self.drop_zone.filesDropped.connect(self._on_files_dropped)
        self.drop_zone.browseRequested.connect(self._browse_files)
        send_card.body.addWidget(self.drop_zone)
        send_buttons = QHBoxLayout()
        send_buttons.setSpacing(8)
        btn_browse = QPushButton("Choose files…")
        btn_browse.setObjectName("secondary")
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.clicked.connect(self._browse_files)
        send_buttons.addWidget(btn_browse)
        btn_send_open = QPushButton("Open Send page")
        btn_send_open.setObjectName("ghost")
        btn_send_open.setCursor(Qt.PointingHandCursor)
        btn_send_open.clicked.connect(lambda: self.navigate.emit("send"))
        send_buttons.addWidget(btn_send_open)
        send_buttons.addStretch(1)
        send_card.body.addLayout(send_buttons)
        hero.addWidget(send_card, 1)

        # Receive card
        receive_card = Card(
            "Receive a file", "Paste a BZ link or code and download it instantly."
        )
        receive_card.setMinimumHeight(300)
        self.receive_input = QLineEdit()
        self.receive_input.setPlaceholderText("BZ://h0l6lgmru052")
        self.receive_input.setMinimumHeight(44)
        self.receive_input.returnPressed.connect(self._on_receive)
        receive_card.body.addWidget(self.receive_input)

        receive_buttons = QHBoxLayout()
        receive_buttons.setSpacing(8)
        btn_paste = QPushButton("Paste")
        btn_paste.setObjectName("secondary")
        btn_paste.setMinimumHeight(38)
        btn_paste.setCursor(Qt.PointingHandCursor)
        btn_paste.clicked.connect(self._paste)
        receive_buttons.addWidget(btn_paste)
        self.btn_receive = QPushButton("Download")
        self.btn_receive.setObjectName("primary")
        self.btn_receive.setMinimumHeight(38)
        self.btn_receive.clicked.connect(self._on_receive)
        receive_buttons.addWidget(self.btn_receive)
        receive_card.body.addLayout(receive_buttons)

        receive_card.body.addWidget(divider())

        hint = QLabel(
            "Tip: press the global hotkey with a BZ link selected to download "
            "without opening this window."
        )
        hint.setObjectName("caption")
        hint.setWordWrap(True)
        receive_card.body.addWidget(hint)
        receive_card.body.addStretch(1)
        hero.addWidget(receive_card, 1)

        outer.addLayout(hero)

        # Stats -----------------------------------------------------------
        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.stat_uploads = StatCard("Uploads", "0", "all time")
        self.stat_downloads = StatCard("Downloads", "0", "all time")
        self.stat_hotkey = StatCard("Global hotkey", "—", "checking")
        stats.addWidget(self.stat_uploads)
        stats.addWidget(self.stat_downloads)
        stats.addWidget(self.stat_hotkey)
        outer.addLayout(stats)

        # Recent activity --------------------------------------------------
        self.recent_card = Card("Recent activity", "Your last transfers.")
        self.recent_body = self.recent_card.body
        self.btn_all = QPushButton("See all")
        self.btn_all.setObjectName("ghost")
        self.btn_all.setCursor(Qt.PointingHandCursor)
        self.btn_all.clicked.connect(lambda: self.navigate.emit("history"))
        self.recent_card.add_header_widget(self.btn_all)
        outer.addWidget(self.recent_card)
        outer.addStretch(1)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose files to send", os.path.expanduser("~"),
            "All files (*.*)",
        )
        if paths:
            self.sendRequested.emit(paths)

    def _on_files_dropped(self, paths: list[str]) -> None:
        if paths:
            self.sendRequested.emit(paths)

    def _paste(self) -> None:
        from PySide6.QtWidgets import QApplication
        text = QApplication.clipboard().text().strip()
        if text:
            self.receive_input.setText(text)

    def _on_receive(self) -> None:
        raw = self.receive_input.text().strip()
        if not raw:
            self._toast("Paste a BZ link or code first.", error=True)
            return
        try:
            code = extract_code(raw)
        except Exception:
            code = ""
        if not is_valid_code(code):
            self._toast("That doesn't look like a valid BZ link.", error=True)
            return
        self.receive_input.clear()
        self.receiveRequested.emit(code)

    def _toast(self, message: str, error: bool = False) -> None:
        window = self.window()
        if hasattr(window, "show_toast"):
            window.show_toast(message, error=error)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def set_update_available(self, info) -> None:
        self._update_info = info
        if info is None:
            self.banner.setVisible(False)
            return
        self.banner_label.setText(
            f"BuzzInstaller {info.version} is ready to install "
            f"({info.size_str}). You are on {updater.current_version()}."
        )
        self.banner.setVisible(True)

    def dismiss_update(self) -> None:
        self.banner.setVisible(False)
        window = self.window()
        if hasattr(window, "dismiss_update"):
            window.dismiss_update()

    def refresh(self) -> None:
        from config.config_manager import load_config
        cfg = load_config()

        self.stat_uploads.set_value(str(history.count("upload")))
        self.stat_downloads.set_value(str(history.count("download")))

        enabled = bool(cfg.get("enable_global_hotkey", True))
        hotkey = cfg.get("global_hotkey", "Alt+F1")
        self.stat_hotkey.set_value(
            hotkey if enabled else "Off",
            "active" if enabled else "disabled in settings",
        )

        clear_layout(self.recent_body)
        entries = history.recent(5)
        if not entries:
            empty = QLabel(
                "Nothing here yet — send a file or download a link to get started."
            )
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self.recent_body.addWidget(empty)
            return

        for entry in entries:
            self.recent_body.addWidget(self._make_activity_row(entry))

    def _make_activity_row(self, entry) -> QWidget:
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 5, 0, 5)
        row.setSpacing(12)

        glyph = "↑" if entry.kind == "upload" else "↓"
        row.addWidget(icon_tile(glyph, 36))

        texts = QVBoxLayout()
        texts.setSpacing(1)
        name = QLabel(entry.name)
        name.setStyleSheet("font-weight: 600;")
        texts.addWidget(name)
        meta_text = entry.bz_url if entry.kind == "upload" else (entry.path or entry.code)
        meta = QLabel(meta_text or entry.code)
        meta.setObjectName("caption")
        texts.addWidget(meta)
        row.addLayout(texts, 1)

        kind_label = QLabel("Upload" if entry.kind == "upload" else "Download")
        kind_label.setObjectName("caption")
        row.addWidget(kind_label)

        when = QLabel(format_relative_time(entry.timestamp))
        when.setObjectName("caption")
        when.setMinimumWidth(80)
        when.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(when)
        return row_widget
