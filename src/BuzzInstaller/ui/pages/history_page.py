"""History page — searchable list of uploads and downloads with actions."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from config.config_manager import load_config
from config.paths import get_default_download_dir
from core.history import format_relative_time, history
from system.clipboard_service import clipboard_service
from ui.widgets import Card, PageHeader
from utils.file_utils import format_size


class HistoryPage(QWidget):
    """Browse, filter and act on every recorded transfer."""

    redownloadRequested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list = []
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(16)

        header = PageHeader(
            "History",
            "Every upload and download is kept locally — links can be copied again at any time.",
        )
        self.btn_folder = QPushButton("Open downloads")
        self.btn_folder.setObjectName("secondary")
        self.btn_folder.setCursor(Qt.PointingHandCursor)
        self.btn_folder.clicked.connect(self._open_downloads)
        header.add_action(self.btn_folder)

        self.btn_clear = QPushButton("Clear history")
        self.btn_clear.setObjectName("danger")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear)
        header.add_action(self.btn_clear)
        outer.addWidget(header)

        card = Card()
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, code or path…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filters)
        filters.addWidget(self.search, 1)

        self.kind_filter = QComboBox()
        self.kind_filter.addItems(["All transfers", "Uploads", "Downloads"])
        self.kind_filter.currentIndexChanged.connect(self._apply_filters)
        filters.addWidget(self.kind_filter)
        card.body.addLayout(filters)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Type", "Name", "Size", "Link / location", "When", "Actions"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setMinimumHeight(360)

        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.Stretch)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        card.body.addWidget(self.table)

        self.summary = QLabel("")
        self.summary.setObjectName("caption")
        card.body.addWidget(self.summary)
        outer.addWidget(card, 1)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self._entries = history.entries()
        self._apply_filters()

    def _filtered(self) -> list:
        query = self.search.text().strip().lower()
        mode = self.kind_filter.currentIndex()
        items = self._entries
        if mode == 1:
            items = [e for e in items if e.kind == "upload"]
        elif mode == 2:
            items = [e for e in items if e.kind == "download"]
        if query:
            items = [
                e for e in items
                if query in e.name.lower()
                or query in e.code.lower()
                or query in e.bz_url.lower()
                or query in e.path.lower()
            ]
        return items

    def _apply_filters(self) -> None:
        items = self._filtered()
        self.table.setRowCount(0)
        for entry in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 44)

            type_item = QTableWidgetItem("Upload" if entry.kind == "upload" else "Download")
            self.table.setItem(row, 0, type_item)

            name_item = QTableWidgetItem(entry.name)
            name_item.setData(Qt.UserRole, entry.uid)
            self.table.setItem(row, 1, name_item)

            self.table.setItem(row, 2, QTableWidgetItem(format_size(entry.size)))

            location = entry.bz_url if entry.kind == "upload" else (entry.path or entry.bz_url)
            location_item = QTableWidgetItem(location)
            location_item.setToolTip(location)
            self.table.setItem(row, 3, location_item)

            self.table.setItem(row, 4, QTableWidgetItem(format_relative_time(entry.timestamp)))
            self.table.setCellWidget(row, 5, self._make_actions(entry))

        total = len(self._entries)
        shown = len(items)
        self.summary.setText(
            f"Showing {shown} of {total} transfer{'s' if total != 1 else ''}."
        )
        self.btn_clear.setEnabled(total > 0)

    def _make_actions(self, entry) -> QWidget:
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(4)

        if entry.bz_url:
            copy_btn = QPushButton("Copy")
            copy_btn.setObjectName("ghost")
            copy_btn.setFixedHeight(26)
            copy_btn.setCursor(Qt.PointingHandCursor)
            copy_btn.clicked.connect(
                lambda _=False, text=entry.bz_url: self._copy(text)
            )
            row.addWidget(copy_btn)

        target = entry.extracted_to or entry.path
        if entry.kind == "download":
            open_btn = QPushButton("Open")
            open_btn.setObjectName("ghost")
            open_btn.setFixedHeight(26)
            open_btn.setEnabled(bool(target and Path(target).exists()))
            open_btn.setCursor(Qt.PointingHandCursor)
            open_btn.clicked.connect(lambda _=False, p=target: self._open(p))
            row.addWidget(open_btn)

            again_btn = QPushButton("Get again")
            again_btn.setObjectName("ghost")
            again_btn.setFixedHeight(26)
            again_btn.setEnabled(bool(entry.code))
            again_btn.setCursor(Qt.PointingHandCursor)
            again_btn.clicked.connect(
                lambda _=False, code=entry.code: self.redownloadRequested.emit(code)
            )
            row.addWidget(again_btn)
        elif entry.url:
            web_btn = QPushButton("Web")
            web_btn.setObjectName("ghost")
            web_btn.setFixedHeight(26)
            web_btn.setCursor(Qt.PointingHandCursor)
            web_btn.clicked.connect(lambda _=False, url=entry.url: self._open(url))
            row.addWidget(web_btn)

        return wrapper

    # ------------------------------------------------------------------
    def _copy(self, text: str) -> None:
        if clipboard_service.copy(text):
            self._toast("Copied to clipboard.")
        else:
            self._toast("Could not copy to clipboard.", error=True)

    def _open(self, target: str) -> None:
        if not target:
            return
        try:
            if os.path.isdir(target) or target.startswith("http"):
                os.startfile(target)  # type: ignore[attr-defined]
            elif os.path.exists(target):
                subprocess.Popen(["explorer.exe", "/select,", target])
        except Exception:
            self._toast("Could not open the item.", error=True)

    def _open_downloads(self) -> None:
        cfg = load_config()
        folder = cfg.get("download_folder") or str(get_default_download_dir())
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
        except Exception:
            self._toast("Could not open the downloads folder.", error=True)

    def _clear(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, "Clear history",
            "Remove all recorded transfers? This does not delete any files.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            history.clear()
            self.refresh()
            self._toast("History cleared.")

    def _toast(self, message: str, error: bool = False) -> None:
        window = self.window()
        if hasattr(window, "show_toast"):
            window.show_toast(message, error=error)
