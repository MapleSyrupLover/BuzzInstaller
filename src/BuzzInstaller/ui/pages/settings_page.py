"""Settings page — grouped, immediately-applied configuration."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from config.config_manager import DEFAULTS, load_config, save_config
from config.paths import get_default_download_dir
from system.context_menu import WindowsIntegrationManager
from system.hotkey_service import format_hotkey, key_code_for_name, parse_hotkey
from system.path_manager import PathError, add_to_path, is_in_path, remove_from_path
from ui.theme import get_palette_from_config
from ui.widgets import Card, CheckBox, PageHeader, divider
from utils.logging import get_logger

logger = get_logger(__name__)


def _int_setting(cfg: dict, key: str, default: int,
                 low: int, high: int) -> int:
    try:
        value = int(cfg.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


class SettingsPage(QWidget):
    """All application settings, applied live."""

    settingsChanged = Signal(dict)
    themeChanged = Signal()
    hotkeyChanged = Signal(str, bool)
    checkUpdatesRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._recording = False
        self._loading = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self.save)

        self._build_ui()
        self.load()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 18)
        outer.setSpacing(14)

        header = PageHeader(
            "Settings",
            "Changes are saved and applied immediately — no restart required.",
        )
        self.btn_reset = QPushButton("Reset to defaults")
        self.btn_reset.setObjectName("ghost")
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.clicked.connect(self._reset_defaults)
        header.add_action(self.btn_reset)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(14)

        layout.addWidget(self._build_download_card())
        layout.addWidget(self._build_clipboard_card())
        layout.addWidget(self._build_transfer_card())
        layout.addWidget(self._build_hotkey_card())
        layout.addWidget(self._build_integration_card())
        layout.addWidget(self._build_appearance_card())
        layout.addWidget(self._build_update_card())
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.status = QLabel("")
        self.status.setObjectName("caption")
        outer.addWidget(self.status)

    # -- download -------------------------------------------------------
    def _build_download_card(self) -> Card:
        card = Card("Downloads", "Where files are saved and what happens afterwards.")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.txt_download = QLineEdit()
        self.txt_download.setPlaceholderText(str(get_default_download_dir()))
        self.txt_download.editingFinished.connect(self._on_download_folder_changed)
        row.addWidget(self.txt_download, 1)
        btn_browse = QPushButton("Browse…")
        btn_browse.setObjectName("secondary")
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.clicked.connect(self._browse_download_folder)
        row.addWidget(btn_browse)
        card.body.addLayout(row)

        self.chk_extract = CheckBox("Automatically extract ZIP archives")
        self.chk_delete_zip = CheckBox("Delete the ZIP after extraction")
        self.chk_open_folder = CheckBox("Open the folder when a download finishes")
        for chk in (self.chk_extract, self.chk_delete_zip, self.chk_open_folder):
            chk.toggled.connect(self._on_toggle)
            card.body.addWidget(chk)
        return card

    # -- clipboard ------------------------------------------------------
    def _build_clipboard_card(self) -> Card:
        card = Card(
            "Clipboard",
            "After a successful send, the BZ:// link is copied automatically. "
            "Choose how long it should stay on the clipboard.",
        )
        self.combo_clipboard_mode = QComboBox()
        self.combo_clipboard_mode.addItem(
            "Keep until something else is copied", "never"
        )
        self.combo_clipboard_mode.addItem("Clear after a delay", "timeout")
        self.combo_clipboard_mode.addItem(
            "Clear after the first paste", "first_paste"
        )
        self.combo_clipboard_mode.currentIndexChanged.connect(
            self._on_clipboard_mode_changed
        )
        card.body.addWidget(self.combo_clipboard_mode)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.lbl_clipboard_seconds = QLabel("Clear after")
        self.lbl_clipboard_seconds.setObjectName("muted")
        row.addWidget(self.lbl_clipboard_seconds)
        self.spin_clipboard_seconds = QSpinBox()
        self.spin_clipboard_seconds.setRange(1, 3600)
        self.spin_clipboard_seconds.setSuffix(" seconds")
        self.spin_clipboard_seconds.setValue(10)
        self.spin_clipboard_seconds.valueChanged.connect(self._on_toggle)
        row.addWidget(self.spin_clipboard_seconds)
        row.addStretch(1)
        card.body.addLayout(row)

        self.lbl_clipboard_hint = QLabel("")
        self.lbl_clipboard_hint.setObjectName("caption")
        self.lbl_clipboard_hint.setWordWrap(True)
        card.body.addWidget(self.lbl_clipboard_hint)
        return card

    # -- transfers -----------------------------------------------------
    def _build_transfer_card(self) -> Card:
        card = Card(
            "Resumable transfers",
            "Large files and folders are split into independently verifiable "
            "chunks. Completed chunks are kept when a transfer is paused or "
            "interrupted, and are reused on the next attempt.",
        )
        self.chk_chunked = CheckBox("Enable resumable chunked transfers")
        self.chk_chunked.toggled.connect(self._on_toggle)
        card.body.addWidget(self.chk_chunked)

        chunk_row = QHBoxLayout()
        chunk_row.setSpacing(8)
        chunk_label = QLabel("Chunk size")
        chunk_label.setObjectName("muted")
        chunk_row.addWidget(chunk_label)
        self.spin_chunk_mb = QSpinBox()
        self.spin_chunk_mb.setRange(1, 102400)
        self.spin_chunk_mb.setSuffix(" MB")
        self.spin_chunk_mb.setSingleStep(1024)
        self.spin_chunk_mb.setValue(10240)
        self.spin_chunk_mb.valueChanged.connect(self._on_toggle)
        chunk_row.addWidget(self.spin_chunk_mb)
        chunk_row.addStretch(1)
        card.body.addLayout(chunk_row)

        concurrency_row = QHBoxLayout()
        concurrency_row.setSpacing(8)
        concurrency_row.addWidget(QLabel("Upload chunks at once"))
        self.spin_upload_concurrency = QSpinBox()
        self.spin_upload_concurrency.setRange(1, 8)
        self.spin_upload_concurrency.valueChanged.connect(self._on_toggle)
        concurrency_row.addWidget(self.spin_upload_concurrency)
        concurrency_row.addStretch(1)
        concurrency_row.addWidget(QLabel("Download chunks at once"))
        self.spin_download_concurrency = QSpinBox()
        self.spin_download_concurrency.setRange(1, 8)
        self.spin_download_concurrency.valueChanged.connect(self._on_toggle)
        concurrency_row.addWidget(self.spin_download_concurrency)
        card.body.addLayout(concurrency_row)

        retry_row = QHBoxLayout()
        retry_row.setSpacing(8)
        retry_row.addWidget(QLabel("Retries per chunk"))
        self.spin_retry_limit = QSpinBox()
        self.spin_retry_limit.setRange(1, 20)
        self.spin_retry_limit.valueChanged.connect(self._on_toggle)
        retry_row.addWidget(self.spin_retry_limit)
        retry_row.addStretch(1)
        card.body.addLayout(retry_row)

        card.body.addWidget(divider())
        self.txt_account_id = QLineEdit()
        self.txt_account_id.setPlaceholderText("Optional BuzzHeavier account ID")
        self.txt_account_id.editingFinished.connect(self._schedule_save)
        card.body.addWidget(self.txt_account_id)
        self.txt_parent_id = QLineEdit()
        self.txt_parent_id.setPlaceholderText("Optional remote directory ID for bundle files")
        self.txt_parent_id.editingFinished.connect(self._schedule_save)
        card.body.addWidget(self.txt_parent_id)

        self.chk_keep_archives = CheckBox(
            "Keep temporary folder archives after a successful upload"
        )
        self.chk_keep_archives.toggled.connect(self._on_toggle)
        card.body.addWidget(self.chk_keep_archives)
        return card

    # -- hotkey ---------------------------------------------------------
    def _build_hotkey_card(self) -> Card:
        card = Card(
            "Global hotkey",
            "Select a BZ link anywhere in Windows and press the hotkey to download it.",
        )
        self.chk_hotkey = CheckBox("Enable the global hotkey")
        self.chk_hotkey.toggled.connect(self._on_hotkey_enabled)
        card.body.addWidget(self.chk_hotkey)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.lbl_hotkey = QLabel("Alt+F1")
        self.lbl_hotkey.setStyleSheet("font-size: 16px; font-weight: 700;")
        row.addWidget(self.lbl_hotkey)
        self.btn_record = QPushButton("Record…")
        self.btn_record.setObjectName("secondary")
        self.btn_record.setCursor(Qt.PointingHandCursor)
        self.btn_record.clicked.connect(self._start_recording)
        row.addWidget(self.btn_record)
        row.addStretch(1)
        card.body.addLayout(row)

        self.chk_selected_text = CheckBox(
            "Read the highlighted text instead of the clipboard"
        )
        self.chk_selected_text.toggled.connect(self._on_toggle)
        card.body.addWidget(self.chk_selected_text)

        self.chk_background_login = CheckBox(
            "Start the background hotkey process at login"
        )
        self.chk_background_login.toggled.connect(self._on_background_login)
        card.body.addWidget(self.chk_background_login)
        return card

    # -- integration ----------------------------------------------------
    def _build_integration_card(self) -> Card:
        card = Card("Windows integration", "Explorer menu, BZ:// protocol and startup.")
        self.chk_start_windows = CheckBox("Start BuzzInstaller with Windows")
        self.chk_start_windows.toggled.connect(self._on_start_with_windows)
        card.body.addWidget(self.chk_start_windows)

        self.chk_close_to_tray = CheckBox(
            "Keep running in the background when the window is closed"
        )
        self.chk_close_to_tray.toggled.connect(self._on_toggle)
        card.body.addWidget(self.chk_close_to_tray)

        self.chk_add_path = CheckBox("Add BuzzInstaller to PATH (enables the bz command)")
        self.chk_add_path.toggled.connect(self._on_add_to_path)
        card.body.addWidget(self.chk_add_path)

        card.body.addWidget(divider())
        row = QHBoxLayout()
        row.setSpacing(8)
        self.lbl_integration = QLabel("Right-click files for Quick Send; open BZ:// links directly.")
        self.lbl_integration.setObjectName("muted")
        self.lbl_integration.setWordWrap(True)
        row.addWidget(self.lbl_integration, 1)
        btn_register = QPushButton("Register integration")
        btn_register.setObjectName("secondary")
        btn_register.setCursor(Qt.PointingHandCursor)
        btn_register.clicked.connect(self._register_integration)
        row.addWidget(btn_register)
        card.body.addLayout(row)
        return card

    # -- appearance -----------------------------------------------------
    def _build_appearance_card(self) -> Card:
        card = Card("Appearance", "Choose how BuzzInstaller looks.")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        self._theme_buttons = {}
        for key, label in ((False, "Light"), (True, "Dark"), (None, "System")):
            btn = QPushButton(label)
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(34)
            self.theme_group.addButton(btn)
            self._theme_buttons[key] = btn
            btn.clicked.connect(lambda _=False, k=key: self._set_theme(k))
            row.addWidget(btn)
        row.addStretch(1)
        card.body.addLayout(row)

        self.chk_notifications = CheckBox("Show desktop notifications")
        self.chk_notifications.toggled.connect(self._on_toggle)
        card.body.addWidget(self.chk_notifications)
        return card

    # -- updates --------------------------------------------------------
    def _build_update_card(self) -> Card:
        card = Card("Updates", "BuzzInstaller can update itself from GitHub Releases.")
        self.chk_auto_check = CheckBox("Check for updates when BuzzInstaller starts")
        self.chk_auto_check.toggled.connect(self._on_toggle)
        card.body.addWidget(self.chk_auto_check)

        self.chk_auto_install = CheckBox(
            "Install updates automatically (the app restarts to finish)"
        )
        self.chk_auto_install.toggled.connect(self._on_toggle)
        card.body.addWidget(self.chk_auto_install)

        card.body.addWidget(divider())
        row = QHBoxLayout()
        row.setSpacing(8)
        self.update_status = QLabel("Not checked yet.")
        self.update_status.setObjectName("muted")
        self.update_status.setWordWrap(True)
        row.addWidget(self.update_status, 1)
        self.btn_check_updates = QPushButton("Check now")
        self.btn_check_updates.setObjectName("secondary")
        self.btn_check_updates.setCursor(Qt.PointingHandCursor)
        self.btn_check_updates.clicked.connect(self.checkUpdatesRequested.emit)
        row.addWidget(self.btn_check_updates)
        card.body.addLayout(row)
        return card

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------
    def load(self) -> None:
        self._loading = True
        try:
            cfg = load_config()
            self.txt_download.setText(
                cfg.get("download_folder") or str(get_default_download_dir())
            )
            self.chk_extract.setChecked(bool(cfg.get("auto_extract_zip", True)))
            self.chk_delete_zip.setChecked(bool(cfg.get("delete_zip_after_extract", False)))
            self.chk_open_folder.setChecked(bool(cfg.get("launch_folder_after_download", True)))
            mode = str(cfg.get("auto_copy_clear_mode", "never") or "never")
            index = self.combo_clipboard_mode.findData(mode)
            self.combo_clipboard_mode.setCurrentIndex(index if index >= 0 else 0)
            self.spin_clipboard_seconds.setValue(
                _int_setting(cfg, "auto_copy_clear_seconds", 10, 1, 3600)
            )
            self._update_clipboard_controls()
            self.chk_chunked.setChecked(bool(cfg.get("chunked_transfers_enabled", True)))
            try:
                chunk_bytes = int(cfg.get("chunk_size_bytes", 10 * 1024**3))
            except (TypeError, ValueError):
                chunk_bytes = 10 * 1024**3
            self.spin_chunk_mb.setValue(max(1, chunk_bytes // (1024 * 1024)))
            self.spin_upload_concurrency.setValue(
                _int_setting(cfg, "chunk_upload_concurrency", 3, 1, 8)
            )
            self.spin_download_concurrency.setValue(
                _int_setting(cfg, "chunk_download_concurrency", 3, 1, 8)
            )
            self.spin_retry_limit.setValue(
                _int_setting(cfg, "chunk_retry_limit", 5, 1, 20)
            )
            self.txt_account_id.setText(str(cfg.get("buzzheavier_account_id", "") or ""))
            self.txt_parent_id.setText(
                str(cfg.get("buzzheavier_bundle_parent_id", "") or "")
            )
            self.chk_keep_archives.setChecked(
                bool(cfg.get("keep_bundle_archives", False))
            )
            self.chk_hotkey.setChecked(bool(cfg.get("enable_global_hotkey", True)))
            self.lbl_hotkey.setText(cfg.get("global_hotkey", "Alt+F1"))
            self.chk_selected_text.setChecked(bool(cfg.get("use_selected_text", True)))
            self.chk_background_login.setChecked(
                bool(cfg.get("start_background_at_login", False))
            )
            self.chk_start_windows.setChecked(bool(cfg.get("start_with_windows", False)))
            self.chk_close_to_tray.setChecked(bool(cfg.get("close_to_tray", True)))
            try:
                self.chk_add_path.setChecked(is_in_path())
            except Exception:
                self.chk_add_path.setChecked(bool(cfg.get("add_to_path", False)))
            self.chk_notifications.setChecked(bool(cfg.get("enable_notifications", True)))
            self.chk_auto_check.setChecked(bool(cfg.get("auto_check_updates", True)))
            self.chk_auto_install.setChecked(bool(cfg.get("auto_download_updates", False)))
            dark = cfg.get("dark_mode", False)
            button = self._theme_buttons.get(dark)
            if button is not None:
                button.setChecked(True)
            self.btn_record.setEnabled(self.chk_hotkey.isChecked())
        finally:
            self._loading = False

    def save(self) -> None:
        if self._loading:
            return
        cfg = load_config()
        cfg["download_folder"] = self.txt_download.text().strip()
        cfg["auto_extract_zip"] = self.chk_extract.isChecked()
        cfg["delete_zip_after_extract"] = self.chk_delete_zip.isChecked()
        cfg["launch_folder_after_download"] = self.chk_open_folder.isChecked()
        cfg["auto_copy_clear_mode"] = (
            self.combo_clipboard_mode.currentData() or "never"
        )
        cfg["auto_copy_clear_seconds"] = int(self.spin_clipboard_seconds.value())
        cfg["chunked_transfers_enabled"] = self.chk_chunked.isChecked()
        cfg["chunk_size_bytes"] = int(self.spin_chunk_mb.value()) * 1024 * 1024
        cfg["chunk_upload_concurrency"] = int(self.spin_upload_concurrency.value())
        cfg["chunk_download_concurrency"] = int(self.spin_download_concurrency.value())
        cfg["chunk_retry_limit"] = int(self.spin_retry_limit.value())
        cfg["buzzheavier_account_id"] = self.txt_account_id.text().strip()
        cfg["buzzheavier_bundle_parent_id"] = self.txt_parent_id.text().strip()
        cfg["keep_bundle_archives"] = self.chk_keep_archives.isChecked()
        cfg["enable_global_hotkey"] = self.chk_hotkey.isChecked()
        cfg["global_hotkey"] = self.lbl_hotkey.text()
        cfg["use_selected_text"] = self.chk_selected_text.isChecked()
        cfg["start_background_at_login"] = self.chk_background_login.isChecked()
        cfg["start_with_windows"] = self.chk_start_windows.isChecked()
        cfg["close_to_tray"] = self.chk_close_to_tray.isChecked()
        cfg["add_to_path"] = self.chk_add_path.isChecked()
        cfg["enable_notifications"] = self.chk_notifications.isChecked()
        cfg["auto_check_updates"] = self.chk_auto_check.isChecked()
        cfg["auto_download_updates"] = self.chk_auto_install.isChecked()
        for dark, button in self._theme_buttons.items():
            if button.isChecked():
                cfg["dark_mode"] = dark
                break
        if save_config(cfg):
            self.status.setText("Settings saved.")
            QTimer.singleShot(2500, lambda: self.status.setText(""))
        else:
            self.status.setText("Could not save settings — see the log for details.")
        self.settingsChanged.emit(load_config())

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _schedule_save(self) -> None:
        if not self._loading:
            self._save_timer.start()

    def _on_toggle(self, _checked: bool = False) -> None:
        self.save()

    def _on_clipboard_mode_changed(self, _index: int = 0) -> None:
        self._update_clipboard_controls()
        if not self._loading:
            self.save()

    def _update_clipboard_controls(self) -> None:
        mode = self.combo_clipboard_mode.currentData()
        timed = mode == "timeout"
        self.lbl_clipboard_seconds.setEnabled(timed)
        self.spin_clipboard_seconds.setEnabled(timed)
        self.lbl_clipboard_hint.setText({
            "never": (
                "The link stays on the clipboard until you copy something else."
            ),
            "timeout": (
                "The link is removed automatically after the delay, unless "
                "the clipboard has already been replaced."
            ),
            "first_paste": (
                "Watches for Ctrl+V or Shift+Insert and removes the link "
                "right after the first paste. Manual copies are not affected."
            ),
        }.get(mode, ""))

    def _on_download_folder_changed(self) -> None:
        text = self.txt_download.text().strip()
        if text and not (text.startswith("~") or ":" in text or text.startswith("\\\\")):
            self.status.setText("Tip: use Browse… to select a valid folder.")
        self._schedule_save()

    def _browse_download_folder(self) -> None:
        current = self.txt_download.text() or str(get_default_download_dir())
        folder = QFileDialog.getExistingDirectory(self, "Select download folder", current)
        if folder:
            self.txt_download.setText(folder)
            self.save()

    def _on_hotkey_enabled(self, enabled: bool) -> None:
        if self._loading:
            return
        self.btn_record.setEnabled(enabled)
        self.save()
        self.hotkeyChanged.emit(self.lbl_hotkey.text(), enabled)

    def _start_recording(self) -> None:
        if self._recording:
            return
        self._recording = True
        self.btn_record.setText("Press a key combination…")
        self.btn_record.setEnabled(False)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _stop_recording(self) -> None:
        self._recording = False
        self.btn_record.setText("Record…")
        self.btn_record.setEnabled(self.chk_hotkey.isChecked())
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if not self._recording or event.type() != QEvent.KeyPress:
            return super().eventFilter(obj, event)

        key = event.key()
        if key in (Qt.Key_Escape,):
            self._stop_recording()
            return True
        if key in (
            Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta,
            Qt.Key_AltGr, Qt.Key_CapsLock,
        ):
            return True

        name: Optional[str] = None
        if Qt.Key_F1 <= key <= Qt.Key_F12:
            name = f"F{key - Qt.Key_F1 + 1}"
        elif Qt.Key_0 <= key <= Qt.Key_9:
            name = chr(ord("0") + (key - Qt.Key_0))
        elif Qt.Key_A <= key <= Qt.Key_Z:
            name = chr(ord("A") + (key - Qt.Key_A))
        if name is None:
            return True

        mods = 0
        modifiers = event.modifiers()
        if modifiers & Qt.AltModifier:
            mods |= 0x0001
        if modifiers & Qt.ControlModifier:
            mods |= 0x0002
        if modifiers & Qt.ShiftModifier:
            mods |= 0x0004
        if modifiers & Qt.MetaModifier:
            mods |= 0x0008
        if mods == 0:
            self.status.setText("Use at least one modifier (Alt, Ctrl, Shift or Win).")
            return True

        vk = key_code_for_name(name)
        if vk is None:
            return True
        spec = format_hotkey(mods, vk)
        if parse_hotkey(spec) is None:
            self.status.setText("That combination is not supported.")
            return True

        self.lbl_hotkey.setText(spec)
        self._stop_recording()
        self.save()
        self.hotkeyChanged.emit(spec, self.chk_hotkey.isChecked())
        self.status.setText(f"Hotkey set to {spec}.")
        return True

    def _set_theme(self, dark: Optional[bool]) -> None:
        cfg = load_config()
        cfg["dark_mode"] = dark
        save_config(cfg)
        self.status.setText("Theme updated.")
        self.themeChanged.emit()
        self.settingsChanged.emit(load_config())

    def _on_start_with_windows(self, enabled: bool) -> None:
        if self._loading:
            return
        self.save()
        try:
            mgr = WindowsIntegrationManager()
            if enabled:
                mgr.register_start_with_windows()
            else:
                mgr.unregister_start_with_windows()
            self.status.setText(
                "BuzzInstaller will start with Windows."
                if enabled else "Start with Windows disabled."
            )
        except Exception as exc:
            logger.warning("Start-with-Windows change failed: %s", exc)
            self.status.setText(f"Could not update startup setting: {exc}")

    def _on_background_login(self, enabled: bool) -> None:
        if self._loading:
            return
        self.save()
        try:
            mgr = WindowsIntegrationManager()
            if enabled:
                mgr.register_background_at_login()
            else:
                mgr.unregister_background_at_login()
            self.status.setText(
                "Background hotkey process will start at login."
                if enabled else "Background login entry removed."
            )
        except Exception as exc:
            logger.warning("Background-at-login change failed: %s", exc)
            self.status.setText(f"Could not update login setting: {exc}")

    def _on_add_to_path(self, enabled: bool) -> None:
        if self._loading:
            return
        self.save()
        try:
            if enabled:
                add_to_path()
            else:
                remove_from_path()
            self.status.setText(
                "Added to PATH — open a new terminal for 'bz' to work."
                if enabled else "Removed from PATH."
            )
        except PathError as exc:
            logger.warning("PATH update failed: %s", exc)
            self.status.setText(f"Could not update PATH: {exc}")
            self.chk_add_path.blockSignals(True)
            try:
                self.chk_add_path.setChecked(is_in_path())
            except Exception:
                self.chk_add_path.setChecked(False)
            self.chk_add_path.blockSignals(False)

    def _register_integration(self) -> None:
        try:
            WindowsIntegrationManager().register_all()
            self.status.setText("Windows integration registered.")
        except Exception as exc:
            logger.warning("Integration registration failed: %s", exc)
            self.status.setText(f"Registration failed: {exc}")

    def _reset_defaults(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, "Reset settings",
            "Reset all settings to their defaults?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        cfg = load_config()
        for key, value in DEFAULTS.items():
            cfg[key] = value
        save_config(cfg)
        self.load()
        self.themeChanged.emit()
        self.settingsChanged.emit(load_config())
        self.status.setText("Settings reset to defaults.")

    # ------------------------------------------------------------------
    def set_update_status(self, text: str, tone: str = "muted") -> None:
        self.update_status.setText(text)
        p = get_palette_from_config()
        color = {
            "error": p.error,
            "success": p.success,
            "warning": p.warning,
        }.get(tone, p.text_secondary)
        self.update_status.setStyleSheet(f"color: {color};")
