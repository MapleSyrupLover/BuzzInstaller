"""
System tray icon manager.

The tray is an **optional**, opt-in feature.  When *minimize-to-tray* or
*start-with-windows* is enabled the application keeps a single lightweight
``QSystemTrayIcon`` alive with a small context menu:

    • Quick Receive …  (paste or open a BZ:// link)
    • Settings
    • Open Downloads Folder
    • About
    • Exit

Uses PySide6 (already a hard dependency for the GUI) so no extra packages
are required.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from PySide6.QtCore import Qt, Signal, QObject, QCoreApplication
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from config.config_manager import load_config
from config.paths import get_default_download_dir, get_app_dir
from utils.logging import get_logger

logger = get_logger(__name__)

TRAY_TOOLTIP = "BuzzInstaller — Quick Send & Receive"


class SystemTrayManager(QObject):
    """Manages the system-tray icon and its context menu."""

    # Signals for cross-thread communication (used by the controller)
    show_main = Signal()
    show_send = Signal()
    show_settings = Signal()
    show_receive = Signal()
    quit_app = Signal()

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self._config = load_config()
        self._tray: Optional[QSystemTrayIcon] = None
        self._menu: Optional[QMenu] = None

    def create(self) -> bool:
        """Create and show the tray icon. Returns True on success."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray is not available on this system")
            return False

        icon_path = self._icon_path()
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self._tray = QSystemTrayIcon(icon, self.app)
        self._tray.setToolTip(TRAY_TOOLTIP)

        self._build_menu()
        self._tray.setContextMenu(self._menu)

        # Show balloon on first launch (once per install)
        if not self._config.get("_first_run_toast_shown", False):
            self._tray.showMessage(
                "BuzzInstaller",
                "Ready. Right-click any file for Quick Send.\n"
                "Open a BZ:// link to Quick Receive.",
                QSystemTrayIcon.Information,
                4000,
            )
            cfg = dict(self._config)
            cfg["_first_run_toast_shown"] = True
            from config.config_manager import save_config
            save_config(cfg)

        self._tray.show()
        logger.info("System tray icon created")
        return True

    def _build_menu(self) -> None:
        self._menu = QMenu()

        act_open = QAction("Open BuzzInstaller", self)
        act_open.triggered.connect(self.on_open)
        self._menu.addAction(act_open)

        self._menu.addSeparator()

        act_send = QAction("&Send files …", self)
        act_send.triggered.connect(lambda: self.show_send.emit())
        self._menu.addAction(act_send)

        act_receive = QAction("Quick &Receive …", self)
        act_receive.setShortcut(QKeySequence("Ctrl+R"))
        act_receive.triggered.connect(self.on_receive)
        self._menu.addAction(act_receive)

        self._menu.addSeparator()

        act_settings = QAction("⚙ &Settings", self)
        act_settings.triggered.connect(self.on_settings)
        self._menu.addAction(act_settings)

        act_downloads = QAction("📂 Open Downloads", self)
        act_downloads.triggered.connect(self.on_open_downloads)
        self._menu.addAction(act_downloads)

        self._menu.addSeparator()

        act_about = QAction("About", self)
        act_about.triggered.connect(self.on_about)
        self._menu.addAction(act_about)

        act_exit = QAction("E&xit", self)
        act_exit.triggered.connect(self.on_exit)
        self._menu.addAction(act_exit)

        # Enable/disable settings based on whether we're in tray mode
        act_receive.setEnabled(True)

    # ------------------------------------------------------------------
    # Icon
    # ------------------------------------------------------------------
    def _icon_path(self) -> str:
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "assets", "buzzinstaller.ico")

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------
    def on_open(self) -> None:
        self.show_main.emit()

    def on_receive(self) -> None:
        """Prompt the user for a BZ code, then start a download."""
        self.show_receive.emit()

    def on_settings(self) -> None:
        self.show_settings.emit()

    def on_open_downloads(self) -> None:
        cfg = load_config()
        dl_dir = cfg.get("download_folder") or str(get_default_download_dir())
        try:
            os.startfile(dl_dir)  # type: ignore[attr-defined]
        except Exception:
            import subprocess
            subprocess.Popen(["explorer.exe", dl_dir])

    def on_about(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from version import APP_VERSION
        msg = QMessageBox()
        msg.setWindowTitle("About BuzzInstaller")
        msg.setText(
            f"<b>BuzzInstaller {APP_VERSION}</b><br>"
            "Send & receive files through BuzzHeavier.<br><br>"
            "Uses native Python — no external runtimes.<br>"
            f"Config: {get_app_dir()}"
        )
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec()

    def on_exit(self) -> None:
        self.quit_app.emit()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def hide(self) -> None:
        if self._tray:
            self._tray.hide()

    def is_shown(self) -> bool:
        return self._tray is not None and self._tray.isSystemTrayNotificationShown()
