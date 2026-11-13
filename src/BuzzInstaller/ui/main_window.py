"""
Main dashboard window.

A Discord-style sidebar + page layout with Uploads, Downloads, Settings,
Customization and About pages.  The window also owns:

* the in-app update flow (manual + automatic checks)
* close-to-tray behaviour
* global shortcuts and drag & drop
* live theme switching
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSettings, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QMessageBox, QPushButton, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

from config.config_manager import load_config, save_config
from core import updater
from core.history import history
from ui.pages import (
    AboutPage, CustomizationPage, ReceivePage, SendPage, SettingsPage,
)
from ui.theme import app_qss, get_palette_from_config
from ui.update_dialog import UpdateDialog
from ui.widgets import Toast
from ui.workers import UpdateCheckWorker
from utils.logging import get_logger

logger = get_logger(__name__)

APP_TITLE = "BuzzInstaller"
NAV_ITEMS = [
    ("send", "↑", "Uploads"),
    ("receive", "↓", "Downloads"),
    ("settings", "⚙", "Settings"),
    ("customization", "◐", "Customization"),
    ("about", "ⓘ", "About"),
]

# The window starts compact and only grows for pages that need the room.
COMPACT_SIZE = (860, 650)
WIDE_SIZE = (1180, 830)
WIDE_PAGES = frozenset({"settings", "customization"})


def _icon_path() -> str:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parents[1]
    return str(base / "assets" / "buzzinstaller.ico")


class MainWindow(QMainWindow):
    """The primary BuzzInstaller window."""

    closingToTray = Signal()

    def __init__(self, page: str = "send", parent=None) -> None:
        super().__init__(parent)
        self._force_quit = False
        self._update_info = None
        self._update_worker: Optional[UpdateCheckWorker] = None
        self._update_dialog: Optional[UpdateDialog] = None
        self._tray: Optional[QSystemTrayIcon] = None
        self._settings = QSettings("BuzzInstaller", "BuzzInstaller")
        self._nav_buttons: dict[str, QPushButton] = {}
        self._pages: dict[str, QWidget] = {}
        self._window_mode: Optional[str] = None
        self._compact_size: Optional[tuple[int, int]] = None

        self.setWindowTitle(APP_TITLE)
        icon = _icon_path()
        if os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))
        self.setMinimumSize(780, 540)
        self.resize(*COMPACT_SIZE)
        self.setAcceptDrops(True)

        self._build_ui()
        self._connect_signals()
        self._install_shortcuts()
        self._restore_geometry()
        self.apply_theme()

        self.navigate(page if page in self._pages else "send")
        QTimer.singleShot(1200, self._maybe_auto_check)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        row.addWidget(self._build_sidebar())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.setObjectName("pages")
        content_layout.addWidget(self.stack)
        row.addWidget(content, 1)

        self.send_page = SendPage()
        self.receive_page = ReceivePage()
        self.settings_page = SettingsPage()
        self.customization_page = CustomizationPage()
        self.about_page = AboutPage()

        for key, page in (
            ("send", self.send_page),
            ("receive", self.receive_page),
            ("settings", self.settings_page),
            ("customization", self.customization_page),
            ("about", self.about_page),
        ):
            self._pages[key] = page
            self.stack.addWidget(page)

        self.toast = Toast(root)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(244)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(6)

        # Brand
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        icon_path = _icon_path()
        if os.path.exists(icon_path):
            pix = QIcon(icon_path).pixmap(38, 38)
            logo.setPixmap(pix)
        logo.setFixedSize(38, 38)
        logo.setAlignment(Qt.AlignCenter)
        brand.addWidget(logo)

        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        title = QLabel("BuzzInstaller")
        title.setObjectName("brandTitle")
        subtitle = QLabel(f"v{updater.current_version()}")
        subtitle.setObjectName("brandSubtitle")
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)
        brand.addLayout(brand_text)
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(18)

        section = QLabel("MENU")
        section.setObjectName("sectionTitle")
        layout.addWidget(section)
        layout.addSpacing(4)

        for key, glyph, label in NAV_ITEMS:
            button = QPushButton(f"{glyph}    {label}")
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(40)
            button.clicked.connect(lambda _=False, k=key: self.navigate(k))
            self._nav_buttons[key] = button
            layout.addWidget(button)

        layout.addStretch(1)

        # Update card (hidden until an update exists)
        self.side_update = QFrame()
        self.side_update.setObjectName("banner")
        side_update_layout = QVBoxLayout(self.side_update)
        side_update_layout.setContentsMargins(12, 10, 12, 12)
        side_update_layout.setSpacing(6)
        self.side_update_label = QLabel("")
        self.side_update_label.setObjectName("muted")
        self.side_update_label.setWordWrap(True)
        side_update_layout.addWidget(self.side_update_label)
        btn_install = QPushButton("Install update")
        btn_install.setObjectName("primary")
        btn_install.setFixedHeight(30)
        btn_install.setCursor(Qt.PointingHandCursor)
        btn_install.clicked.connect(self.open_update_dialog)
        side_update_layout.addWidget(btn_install)
        self.side_update.setVisible(False)
        layout.addWidget(self.side_update)

        # Status footer
        status_row = QHBoxLayout()
        status_row.setSpacing(7)
        self.status_dot = QLabel("●")
        self.status_dot.setFixedWidth(12)
        status_row.addWidget(self.status_dot)
        self.status_label = QLabel("")
        self.status_label.setObjectName("caption")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        layout.addLayout(status_row)

        self._refresh_sidebar_status()

        footer = QPushButton("Check for updates")
        footer.setObjectName("ghost")
        footer.setCursor(Qt.PointingHandCursor)
        footer.clicked.connect(lambda: self.start_update_check())
        layout.addWidget(footer)
        return sidebar

    # ------------------------------------------------------------------
    # Signals / shortcuts
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self.send_page.uploadFinished.connect(self._on_transfer_finished)
        self.receive_page.downloadFinished.connect(self._on_transfer_finished)

        self.settings_page.settingsChanged.connect(self._on_settings_changed)
        self.settings_page.themeChanged.connect(self.apply_theme)
        self.settings_page.hotkeyChanged.connect(self._on_hotkey_changed)
        self.settings_page.checkUpdatesRequested.connect(lambda: self.start_update_check())
        self.customization_page.themeChanged.connect(self.apply_theme)
        self.about_page.checkUpdatesRequested.connect(lambda: self.start_update_check())

    def _install_shortcuts(self) -> None:
        shortcuts = {
            "Ctrl+1": lambda: self.navigate("send"),
            "Ctrl+2": lambda: self.navigate("receive"),
            "Ctrl+3": lambda: self.navigate("settings"),
            "Ctrl+4": lambda: self.navigate("customization"),
            "Ctrl+5": lambda: self.navigate("about"),
            "Ctrl+O": self.send_page._browse_files,
            "Ctrl+R": lambda: self.navigate("receive"),
            "Ctrl+,": lambda: self.navigate("settings"),
            "Ctrl+U": lambda: self.start_update_check(),
            "F1": lambda: self.navigate("about"),
        }
        self._shortcuts = []
        for sequence, callback in shortcuts.items():
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def navigate(self, key: str) -> None:
        page = self._pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        for name, button in self._nav_buttons.items():
            button.setChecked(name == key)

        if key == "send":
            self.send_page.refresh()
        elif key == "receive":
            self.receive_page.refresh()
            self.receive_page.link_input.setFocus()
        elif key == "settings":
            self.settings_page.load()
        elif key == "customization":
            self.customization_page.refresh()

        self._apply_window_mode(key)

    # ------------------------------------------------------------------
    # Window sizing
    # ------------------------------------------------------------------
    def _apply_window_mode(self, key: str) -> None:
        """Keep the window compact unless the page needs the extra room."""
        mode = "wide" if key in WIDE_PAGES else "compact"
        previous, self._window_mode = self._window_mode, mode
        if self.isMaximized() or self.isFullScreen():
            return

        target = WIDE_SIZE if mode == "wide" else COMPACT_SIZE
        if previous is None:
            self._resize_to(target)
        elif mode == previous:
            return
        elif mode == "wide":
            # Remember the compact size so leaving Settings restores it.
            self._compact_size = (self.width(), self.height())
            self._resize_to((
                max(self.width(), target[0]),
                max(self.height(), target[1]),
            ))
        else:
            self._resize_to(self._compact_size or target)
            self._compact_size = None

    def _resize_to(self, size: tuple[int, int]) -> None:
        width, height = size
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, max(available.width() - 40, 720))
            height = min(height, max(available.height() - 60, 520))
        if (width, height) != (self.width(), self.height()):
            self.resize(width, height)
        self._keep_on_screen()

    def _keep_on_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        x = max(min(frame.x(), available.right() - frame.width() + 1),
                available.left())
        y = max(min(frame.y(), available.bottom() - frame.height() + 1),
                available.top())
        if (x, y) != (frame.x(), frame.y()):
            self.move(x, y)

    def _send_paths(self, paths: list[str]) -> None:
        self.navigate("send")
        self.send_page.add_files(paths)

    def _receive_code(self, code: str) -> None:
        self.navigate("receive")
        self.receive_page.set_code(code)
        self.receive_page.start_download(code)

    # ------------------------------------------------------------------
    # Settings / theme
    # ------------------------------------------------------------------
    def apply_theme(self) -> None:
        cfg = load_config()
        palette = get_palette_from_config()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(app_qss(palette, cfg))
        self._refresh_sidebar_status()
        if self._update_info is not None:
            self._show_update_banner(self._update_info)

    def _refresh_sidebar_status(self) -> None:
        from ui.theme import get_palette_from_config
        p = get_palette_from_config()
        cfg = load_config()
        enabled = bool(cfg.get("enable_global_hotkey", True))
        hotkey = cfg.get("global_hotkey", "Alt+F1")
        if enabled:
            self.status_label.setText(f"Hotkey {hotkey} · listening")
            self.status_dot.setStyleSheet(f"color: {p.success}; font-size: 12px;")
        else:
            self.status_label.setText("Global hotkey disabled")
            self.status_dot.setStyleSheet(f"color: {p.text_tertiary}; font-size: 12px;")

    def _on_settings_changed(self, cfg: dict) -> None:
        self.send_page.refresh()
        self.receive_page.refresh()
        self._refresh_sidebar_status()
        self._restart_background_process()

    def _on_hotkey_changed(self, spec: str, enabled: bool) -> None:
        self._refresh_sidebar_status()
        self._restart_background_process()

    def _restart_background_process(self) -> None:
        """Apply config changes to the background hotkey process."""
        try:
            from config.paths import get_config_path
            import json
            from system.background_service import BackgroundService

            cfg = load_config()
            # Write in-place so QFileSystemWatcher definitely sees the change.
            try:
                get_config_path().write_text(
                    json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except Exception as exc:
                logger.debug("Direct config write failed: %s", exc)

            if BackgroundService.is_background_running():
                BackgroundService.launch_app(["--background"])
            elif (cfg.get("enable_global_hotkey", True)
                  or cfg.get("start_with_windows", False)
                  or cfg.get("start_background_at_login", False)):
                BackgroundService.launch_app(["--background"])
        except Exception as exc:
            logger.warning("Could not restart background process: %s", exc)

    def _on_transfer_finished(self) -> None:
        self.send_page.refresh()
        self.receive_page.refresh()

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def _maybe_auto_check(self) -> None:
        try:
            updater.cleanup_downloads()
        except OSError as exc:
            logger.debug("Could not clean update cache: %s", exc)
        cfg = load_config()
        if cfg.get("auto_check_updates", True):
            self.start_update_check(silent=True)

    def start_update_check(self, silent: bool = False) -> None:
        if self._update_worker is not None:
            return
        self.settings_page.set_update_status("Checking for updates…")
        self.about_page.set_update_status("Checking for updates…")
        self.settings_page.btn_check_updates.setEnabled(False)
        self.about_page.btn_check.setEnabled(False)

        self._update_worker = UpdateCheckWorker(parent=self)
        self._update_worker.found.connect(
            lambda info: self._on_update_checked(info, silent)
        )
        self._update_worker.failed.connect(
            lambda message: self._on_update_check_failed(message, silent)
        )
        self._update_worker.finished.connect(self._on_update_worker_done)
        self._update_worker.start()

    def _on_update_worker_done(self) -> None:
        self._update_worker = None
        self.settings_page.btn_check_updates.setEnabled(True)
        self.about_page.btn_check.setEnabled(True)

    def _on_update_checked(self, info, silent: bool) -> None:
        from config.config_manager import load_config, save_config as _save
        cfg = load_config()
        cfg["last_update_check"] = time.time()
        _save(cfg)

        if info is None:
            self._update_info = None
            self.side_update.setVisible(False)
            self.settings_page.set_update_status(
                f"BuzzInstaller {updater.current_version()} is up to date.", "success"
            )
            self.about_page.set_update_status(
                f"BuzzInstaller {updater.current_version()} is up to date.", "success"
            )
            if not silent:
                self.show_toast("You're up to date.")
            return

        self._update_info = info
        self._show_update_banner(info)
        self.settings_page.set_update_status(
            f"Update available: {info.version} ({info.size_str}).", "warning"
        )
        self.about_page.set_update_status(
            f"BuzzInstaller {info.version} is available — {info.size_str}.", "warning"
        )
        if not silent:
            self.show_toast(f"BuzzInstaller {info.version} is available.")

        if cfg.get("auto_download_updates", False) and updater.can_self_update():
            if cfg.get("skipped_update_version") != info.version:
                QTimer.singleShot(700, self.open_update_dialog)

    def _on_update_check_failed(self, message: str, silent: bool) -> None:
        self.settings_page.set_update_status(
            f"Unable to check for updates: {message}", "error"
        )
        self.about_page.set_update_status(
            f"Unable to check for updates: {message}", "error"
        )
        if not silent:
            self.show_toast(f"Update check failed: {message}", error=True)

    def _show_update_banner(self, info) -> None:
        self.side_update_label.setText(
            f"Update {info.version} available ({info.size_str})"
        )
        self.side_update.setVisible(True)

    def dismiss_update(self) -> None:
        if self._update_info is None:
            return
        cfg = load_config()
        cfg["skipped_update_version"] = self._update_info.version
        save_config(cfg)
        self.side_update.setVisible(False)

    def open_update_dialog(self) -> None:
        if self._update_info is None:
            self.start_update_check()
            return
        if self._update_dialog is not None and self._update_dialog.isVisible():
            self._update_dialog.raise_()
            self._update_dialog.activateWindow()
            return
        self._update_dialog = UpdateDialog(self._update_info, parent=self)
        self._update_dialog.closed.connect(self._on_update_dialog_closed)
        self._update_dialog.show()
        self._update_dialog.raise_()
        self._update_dialog.activateWindow()

    def _on_update_dialog_closed(self) -> None:
        self._update_dialog = None

    # ------------------------------------------------------------------
    # Toast
    # ------------------------------------------------------------------
    def show_toast(self, message: str, error: bool = False) -> None:
        cfg = load_config()
        if not cfg.get("enable_notifications", True):
            return
        self.toast.show_message(message, error=error)

    # ------------------------------------------------------------------
    # Drag & drop anywhere
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        paths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            event.acceptProposedAction()
            self._send_paths(paths)
        else:
            super().dropEvent(event)

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        cfg = load_config()
        if not cfg.get("remember_window_size", True):
            return
        geometry = self._settings.value("geometry")
        if geometry is not None:
            try:
                self.restoreGeometry(geometry)
            except Exception:
                pass

    def _save_geometry(self) -> None:
        cfg = load_config()
        if cfg.get("remember_window_size", True):
            self._settings.setValue("geometry", self.saveGeometry())

    def _ensure_tray(self) -> bool:
        if self._tray is not None:
            return True
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        icon = QIcon(_icon_path()) if os.path.exists(_icon_path()) else QIcon()
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("BuzzInstaller — Quick Send & Receive")
        menu = QMenu()
        open_action = QAction("Open BuzzInstaller", self)
        open_action.triggered.connect(self._restore_window)
        menu.addAction(open_action)
        menu.addSeparator()
        for label, key in (
            ("Upload files…", "send"),
            ("Download a file…", "receive"),
            ("Settings", "settings"),
            ("Customization", "customization"),
        ):
            action = QAction(label, self)
            action.triggered.connect(lambda _=False, k=key: self._open_page(k))
            menu.addAction(action)
        menu.addSeparator()
        check_action = QAction("Check for updates", self)
        check_action.triggered.connect(lambda: self.start_update_check())
        menu.addAction(check_action)
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        return True

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self._restore_window()

    def _open_page(self, key: str) -> None:
        self._restore_window()
        self.navigate(key)

    def _restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._force_quit = True
        self._save_geometry()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:
        busy = (self.send_page._worker is not None
                or self.receive_page._worker is not None)
        if busy:
            answer = QMessageBox.question(
                self, "Transfer in progress",
                "A transfer is still running. Close BuzzInstaller anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return

        cfg = load_config()
        if (not self._force_quit and cfg.get("close_to_tray", True)
                and self._ensure_tray()):
            self._save_geometry()
            self.hide()
            event.ignore()
            self.closingToTray.emit()
            self._tray.showMessage(
                "BuzzInstaller is still running",
                "Quick Send, BZ links and the global hotkey keep working. "
                "Right-click the tray icon to quit.",
                QSystemTrayIcon.Information,
                3500,
            )
            return

        self._save_geometry()
        super().closeEvent(event)
