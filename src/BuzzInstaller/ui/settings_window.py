"""
Settings window — a modern, frameless dialog for configuring BuzzInstaller.

Options
-------
* Default Download Folder (with Browse)
* Auto Extract ZIP
* Delete ZIP After Extract
* Launch Folder After Download
* Start With Windows
* Dark Mode
* Minimize to Tray
* Register Windows Integration (button)

Settings are persisted as JSON; the registry entries are updated live
for Start-With-Windows.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QCursor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QCheckBox, QLineEdit, QFileDialog, QFormLayout, QFrame,
    QComboBox,
)

from system.hotkey_service import parse_hotkey, format_hotkey, key_code_for_name

from ui.base_window import BaseWindow
from ui.theme import Palette, button_qss, get_palette_from_config
from config.config_manager import load_config, save_config
from config.paths import get_default_download_dir
from system.context_menu import WindowsIntegrationManager
from system.path_manager import (
    add_to_path, remove_from_path, is_in_path, PathError,
)
from utils.logging import get_logger

logger = get_logger(__name__)


class SettingsWindow(BaseWindow):
    """Configuration dialog."""

    saved = Signal(dict)
    integration_changed = Signal()
    hotkey_changed = Signal(str, bool)   # (hotkey_spec, enabled)

    def __init__(self, palette: Optional[Palette] = None) -> None:
        super().__init__(palette=palette)
        self._initial: dict = {}
        self._build_ui()
        self.adjustSize()
        self.center_on_screen()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        p = self._palette
        cl = self.content_layout

        # Title bar
        cl.addWidget(self._make_title_bar("Settings"))

        # Heading
        hdr = QLabel("BuzzInstaller Settings")
        hdr.setStyleSheet(
            f"color: {p.text_primary}; font-size: 18px; font-weight: 600;"
        )
        cl.addWidget(hdr)

        # Settings form
        self._form = self._build_form(p)
        cl.addWidget(self._form)

        # Register button
        self._btn_register = QPushButton("Register Windows Integration")
        self._btn_register.setStyleSheet(button_qss(p, "secondary"))
        self._btn_register.setFixedHeight(36)
        self._btn_register.setCursor(Qt.PointingHandCursor)
        self._btn_register.clicked.connect(self._on_register)
        cl.addWidget(self._btn_register)

        # Button row
        btns = QHBoxLayout()
        btns.addStretch()

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setStyleSheet(button_qss(p, "secondary"))
        self._btn_cancel.setFixedHeight(36)
        self._btn_cancel.setCursor(Qt.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._on_cancel)
        btns.addWidget(self._btn_cancel)

        self._btn_save = QPushButton("Save")
        self._btn_save.setStyleSheet(button_qss(p, "primary"))
        self._btn_save.setFixedHeight(36)
        self._btn_save.setCursor(Qt.PointingHandCursor)
        self._btn_save.clicked.connect(self._on_save)
        btns.addWidget(self._btn_save)

        cl.addLayout(btns)

        # Load current settings
        self._load_settings()

    def _build_form(self, p: Palette) -> QFrame:
        frame = QFrame()
        form = QFormLayout(frame)
        form.setSpacing(12)

        # Download folder
        self._txt_download = QLineEdit()
        self._txt_download.setFixedHeight(32)
        self._txt_download.setStyleSheet(self._input_qss(p))
        self._btn_browse = QPushButton("Browse…")
        self._btn_browse.setFixedHeight(32)
        self._btn_browse.setStyleSheet(button_qss(p, "secondary"))
        self._btn_browse.setCursor(Qt.PointingHandCursor)
        self._btn_browse.clicked.connect(self._on_browse)

        dl_layout = QHBoxLayout()
        dl_layout.setSpacing(8)
        dl_layout.addWidget(self._txt_download)
        dl_layout.addWidget(self._btn_browse)
        form.addRow("Download folder:", dl_layout)

        # Checkboxes
        self._chk_auto_extract = self._make_checkbox("Auto extract ZIP archives")
        self._chk_delete_zip = self._make_checkbox("Delete ZIP after extraction")
        self._chk_launch_folder = self._make_checkbox("Launch folder after download")
        self._chk_start_windows = self._make_checkbox("Start with Windows")
        self._chk_minimize_tray = self._make_checkbox("Minimize to tray")
        self._chk_dark_mode = self._make_checkbox("Dark mode (system = auto)")
        self._chk_add_to_path = self._make_checkbox("Add BuzzInstaller to PATH")

        form.addRow(self._chk_auto_extract)
        form.addRow(self._chk_delete_zip)
        form.addRow(self._chk_launch_folder)
        form.addRow(self._chk_start_windows)
        form.addRow(self._chk_minimize_tray)
        form.addRow(self._chk_dark_mode)
        form.addRow(self._chk_add_to_path)

        # --- Global hotkey --------------------------------------------
        self._chk_hotkey = self._make_checkbox("Enable Global Hotkey")
        self._chk_hotkey.toggled.connect(self._on_hotkey_toggled)
        form.addRow(self._chk_hotkey)

        self._btn_record = QPushButton("Record Hotkey…")
        self._btn_record.setFixedHeight(32)
        self._btn_record.setStyleSheet(button_qss(p, "secondary"))
        self._btn_record.setCursor(Qt.PointingHandCursor)
        self._btn_record.clicked.connect(self._on_record_hotkey)
        self._lbl_hotkey = QLabel("Alt+F1")
        self._lbl_hotkey.setStyleSheet(
            f"color: {p.text_primary}; font-size: 13px; font-weight: 600;"
        )
        hk_layout = QHBoxLayout()
        hk_layout.setSpacing(8)
        hk_layout.addWidget(self._lbl_hotkey)
        hk_layout.addWidget(self._btn_record)
        form.addRow("Hotkey:", hk_layout)
        self._recording = False

        # Hotkey mode: clipboard or selected text.
        self._combo_mode = QComboBox()
        self._combo_mode.addItem("Clipboard Mode", "clipboard")
        self._combo_mode.addItem("Selected Text Mode", "selected_text")
        self._combo_mode.setFixedHeight(32)
        self._combo_mode.setStyleSheet(
            f"QComboBox {{ background: {p.surface}; border: 1px solid {p.border}; "
            f"border-radius: 8px; padding: 4px 8px; color: {p.text_primary}; }}"
        )
        form.addRow("Hotkey Mode:", self._combo_mode)

        # Start background process at login.
        self._chk_background_login = self._make_checkbox(
            "Start background hotkey process at login"
        )
        form.addRow(self._chk_background_login)

        frame.setStyleSheet(
            f"background: transparent; border: none; "
            f"color: {p.text_primary}; font-size: 13px;"
        )
        return frame

    def _make_checkbox(self, text: str) -> QCheckBox:
        cb = QCheckBox(text)
        cb.setStyleSheet(
            "QCheckBox {{ color: {c}; font-size: 13px; }} "
            "QCheckBox::indicator {{ width: 16px; height: 16px; }}".format(
                c=self._palette.text_primary
            )
        )
        return cb

    @staticmethod
    def _input_qss(p: Palette) -> str:
        return f"""
            QLineEdit {{
                background: {p.surface};
                border: 1px solid {p.border};
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 13px;
                color: {p.text_primary};
            }}
            QLineEdit:focus {{
                border-color: {p.accent};
            }}
        """

    def _make_title_bar(self, title: str) -> QWidget:
        p = self._palette
        bar = QWidget()
        bar.setFixedHeight(32)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 0, 0, 0)

        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {p.text_secondary}; font-size: 12px; font-weight: 500;"
        )
        layout.addWidget(lbl)
        layout.addStretch()

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(32, 32)
        btn_close.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {p.text_tertiary};
                border: none;
                font-size: 14px; font-weight: 400;
            }}
            QPushButton:hover {{
                color: white; background: {p.error}; border-radius: 10px;
            }}
        """)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self._on_cancel)
        layout.addWidget(btn_close)

        bar.mousePressEvent = self._title_mouse_press
        return bar

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------
    def _load_settings(self):
        cfg = load_config()
        self._initial = dict(cfg)

        dl = cfg.get("download_folder", "")
        if not dl:
            dl = str(get_default_download_dir())
        self._txt_download.setText(dl)

        self._chk_auto_extract.setChecked(cfg.get("auto_extract_zip", True))
        self._chk_delete_zip.setChecked(cfg.get("delete_zip_after_extract", False))
        self._chk_launch_folder.setChecked(cfg.get("launch_folder_after_download", True))
        self._chk_start_windows.setChecked(cfg.get("start_with_windows", False))
        self._chk_minimize_tray.setChecked(cfg.get("minimize_to_tray", False))
        self._chk_hotkey.setChecked(cfg.get("enable_global_hotkey", True))
        self._lbl_hotkey.setText(cfg.get("global_hotkey", "Alt+F1"))
        mode = cfg.get("hotkey_mode", "clipboard")
        idx = self._combo_mode.findData(mode)
        if idx >= 0:
            self._combo_mode.setCurrentIndex(idx)
        self._chk_background_login.setChecked(cfg.get("start_background_at_login", False))
        # The PATH checkbox reflects the ACTUAL state of the user's PATH,
        # not just the saved config — if the user manually removed it,
        # the checkbox should show as disabled.
        try:
            self._chk_add_to_path.setChecked(is_in_path())
        except Exception:
            self._chk_add_to_path.setChecked(cfg.get("add_to_path", False))
        dm = cfg.get("dark_mode", False)
        if dm is None:
            self._chk_dark_mode.setTristate(True)
            self._chk_dark_mode.setCheckState(Qt.PartiallyChecked)
        else:
            self._chk_dark_mode.setChecked(bool(dm))

    def _save_settings(self):
        cfg = load_config()
        cfg["download_folder"] = self._txt_download.text().strip()
        cfg["auto_extract_zip"] = self._chk_auto_extract.isChecked()
        cfg["delete_zip_after_extract"] = self._chk_delete_zip.isChecked()
        cfg["launch_folder_after_download"] = self._chk_launch_folder.isChecked()
        cfg["start_with_windows"] = self._chk_start_windows.isChecked()
        cfg["minimize_to_tray"] = self._chk_minimize_tray.isChecked()
        cfg["enable_global_hotkey"] = self._chk_hotkey.isChecked()
        cfg["global_hotkey"] = self._lbl_hotkey.text()
        cfg["hotkey_mode"] = self._combo_mode.currentData()
        cfg["start_background_at_login"] = self._chk_background_login.isChecked()
        cfg["add_to_path"] = self._chk_add_to_path.isChecked()
        if self._chk_dark_mode.checkState() == Qt.PartiallyChecked:
            cfg["dark_mode"] = None
        else:
            cfg["dark_mode"] = self._chk_dark_mode.isChecked()
        save_config(cfg)

        # Sync Start-With-Windows registry
        self._sync_start_with_windows(cfg["start_with_windows"])
        # Sync the background-at-login registry entry
        self._sync_background_at_login(cfg["start_background_at_login"])
        # Sync the PATH entry
        self._sync_add_to_path(cfg["add_to_path"])

    def _sync_start_with_windows(self, enabled: bool):
        try:
            mgr = WindowsIntegrationManager()
            if enabled:
                mgr.register_start_with_windows()
            else:
                mgr.unregister_start_with_windows()
        except Exception as exc:
            logger.warning("Start-with-Windows sync failed: %s", exc)

    def _sync_background_at_login(self, enabled: bool):
        """Register/unregister the background hotkey process at login.

        Uses a separate registry entry so the user can opt into the
        background hotkey process independently of the full tray app.
        """
        try:
            mgr = WindowsIntegrationManager()
            if enabled:
                mgr.register_background_at_login()
            else:
                mgr.unregister_background_at_login()
        except Exception as exc:
            logger.warning("Background-at-login sync failed: %s", exc)

    def _sync_add_to_path(self, enabled: bool):
        """Add/remove the BuzzInstaller directory from the user PATH.

        Applies the change immediately.  If it fails, shows a clear
        error explaining why (permission, registry read/write, etc.).
        """
        try:
            if enabled:
                add_to_path()
            else:
                remove_from_path()
        except PathError as exc:
            logger.error("PATH sync failed: %s", exc)
            self._show_toast(f"PATH update failed:\n{exc}", error=True)
        except Exception as exc:
            logger.error("PATH sync failed: %s", exc)
            self._show_toast(f"PATH update failed:\n{exc}", error=True)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _on_browse(self):
        current = self._txt_download.text() or str(get_default_download_dir())
        folder = QFileDialog.getExistingDirectory(
            self, "Select Download Folder", current
        )
        if folder:
            self._txt_download.setText(folder)

    def _on_register(self):
        try:
            mgr = WindowsIntegrationManager()
            mgr.register_all()
            self.integration_changed.emit()
            self._show_toast("Integration registered successfully.")
        except Exception as exc:
            self._show_toast(f"Registration failed:\n{exc}", error=True)

    def _on_save(self):
        self._save_settings()
        self.saved.emit(load_config())
        self.hotkey_changed.emit(
            self._lbl_hotkey.text(), self._chk_hotkey.isChecked()
        )
        self.close()

    def _on_hotkey_toggled(self, checked: bool):
        self._btn_record.setEnabled(checked)

    def _on_record_hotkey(self):
        """Enter 'recording' mode: the next key combination is captured."""
        self._recording = True
        self._btn_record.setText("Press keys…")
        self._lbl_hotkey.setText("…")
        self.grabKeyboard()

    def keyPressEvent(self, event):
        if not self._recording:
            super().keyPressEvent(event)
            return
        # Build a hotkey spec from the pressed modifiers + key.
        mods = 0
        if event.modifiers() & Qt.AltModifier:
            mods |= 0x0001
        if event.modifiers() & Qt.ControlModifier:
            mods |= 0x0002
        if event.modifiers() & Qt.ShiftModifier:
            mods |= 0x0004
        if event.modifiers() & Qt.MetaModifier:
            mods |= 0x0008
        key = event.key()
        # Map Qt key to our VK naming.
        name = None
        if Qt.Key_F1 <= key <= Qt.Key_F12:
            name = f"F{key - Qt.Key_F1 + 1}"
        elif Qt.Key_0 <= key <= Qt.Key_9:
            name = chr(ord("0") + (key - Qt.Key_0))
        elif Qt.Key_A <= key <= Qt.Key_Z:
            name = chr(ord("A") + (key - Qt.Key_A))
        if name is None:
            return  # ignore modifier-only presses
        vk = key_code_for_name(name)
        if vk is None:
            return
        spec = format_hotkey(mods, vk)
        self._lbl_hotkey.setText(spec)
        self._recording = False
        self._btn_record.setText("Record Hotkey…")
        self.releaseKeyboard()
        # Validate the hotkey can be parsed.
        if parse_hotkey(spec) is None:
            self._lbl_hotkey.setText("Alt+F1")
            self._show_toast("Invalid hotkey — using Alt+F1", error=True)
        else:
            self.hotkey_changed.emit(spec, self._chk_hotkey.isChecked())

    def _on_cancel(self):
        self.close()

    def _show_toast(self, message: str, error: bool = False):
        from PySide6.QtWidgets import QToolTip
        from PySide6.QtCore import QRect
        # PySide6 signature: showText(pos, text, widget=None, rect=QRect(),
        # msecShowTime=-1).  Passing None for rect raises a TypeError.
        QToolTip.showText(
            self.mapToGlobal(self.rect().center()),
            message,
            self,
            QRect(),
            3000,
        )

    # ------------------------------------------------------------------
    # Drag
    # ------------------------------------------------------------------
    def _title_mouse_press(self, event):
        if self._allow_move and event.button() == Qt.LeftButton:
            self._pressed_pos = event.globalPosition().toPoint()
            self._pressed_pos = QPoint(
                self._pressed_pos.x() - self.x(),
                self._pressed_pos.y() - self.y(),
            )
            self.setCursor(QCursor(Qt.SizeAllCursor))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed_pos = None
            self.setCursor(QCursor(Qt.ArrowCursor))
        super().mouseReleaseEvent(event)
