"""
Windows integration manager.

Registers / unregisters:
  * The **BZ://** custom URI protocol.
  * The **Quick Send** Explorer context menu item for files and folders.
  * The **Start with Windows** autostart entry (optional).

All operations are performed via the RegistryManager (winreg wrapper)
and require no external tools.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from config.paths import get_exe_path, is_frozen
from system.registry_manager import (
    RegistryManager, HKCR, HKCU, REG_SZ, RegistryError,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# Use HKCU\Software\Classes for per-user registration (no admin required).
# Windows merges this with HKCR for the effective class root.
HKCU_CLASSES = HKCU
CLASSES_PREFIX = r"Software\Classes"

# Registry key paths for the protocol and context menu
PROTOCOL_KEY = r"Software\Classes\BZ"
PROTOCOL_COMMAND = r"Software\Classes\BZ\shell\open\command"
PROTOCOL_DEFAULT_ICON = r"Software\Classes\BZ\DefaultIcon"

CONTEXT_MENU_FILES = r"Software\Classes\*\shell\BuzzInstallerSend"
CONTEXT_MENU_DIRS = r"Software\Classes\Directory\shell\BuzzInstallerSend"
CONTEXT_MENU_COMMAND_FILES = r"Software\Classes\*\shell\BuzzInstallerSend\command"
CONTEXT_MENU_COMMAND_DIRS = r"Software\Classes\Directory\shell\BuzzInstallerSend\command"

START_WITH_WINDOWS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
START_WITH_WINDOWS_NAME = "BuzzInstaller"

APP_DISPLAY_NAME = "BuzzInstaller"
APP_ID = "BuzzInstaller.BuzzInstaller"


def _get_exe() -> str:
    """Return the quoted path to the running executable."""
    exe = get_exe_path()
    return exe


def _quoted_exe() -> str:
    exe = _get_exe()
    if " " in exe:
        return f'"{exe}"'
    return exe


class WindowsIntegrationManager:
    """Register/unregister all Windows shell integrations."""

    def __init__(self, reg: Optional[RegistryManager] = None) -> None:
        self.reg = reg or RegistryManager()
        self.exe = _get_exe()

    # ------------------------------------------------------------------
    # Protocol: BZ://
    # ------------------------------------------------------------------
    def register_protocol(self) -> None:
        """Register the BZ:// URI protocol under HKCU\\Software\\Classes\\BZ.

        Creates the complete structure Windows expects for a custom URL
        scheme so it works **immediately** without admin privileges:

            HKCU\\Software\\Classes\\BZ
              (Default)       = "URL:BZ Protocol"
              URL Protocol    = ""
              DefaultIcon
                (Default)     = "<exe>,0"
              shell
                open
                  command
                    (Default) = "\\"<exe>\\" \\"%1\\""
        """
        exe_icon = f"{self.exe},0"        # unquoted path + ,0 for the icon
        exe_q = f'"{self.exe}"'          # always-quoted executable path

        try:
            # 1) Root key: class description + URL Protocol marker
            self.reg.set_value(HKCU_CLASSES, PROTOCOL_KEY, "", "URL:BZ Protocol")
            self.reg.set_value(HKCU_CLASSES, PROTOCOL_KEY, "URL Protocol", "")

            # 2) DefaultIcon subkey
            self.reg.create_key(HKCU_CLASSES, PROTOCOL_DEFAULT_ICON)
            self.reg.set_value(HKCU_CLASSES, PROTOCOL_DEFAULT_ICON, "", exe_icon)

            # 3) shell\open\command subkey
            self.reg.create_key(HKCU_CLASSES, PROTOCOL_COMMAND)
            cmd = f'{exe_q} "%1"'
            self.reg.set_value(HKCU_CLASSES, PROTOCOL_COMMAND, "", cmd)

            # 4) Notify Windows that associations changed
            self._notify_assoc_changed()

            # 5) Verify every value was written correctly
            problems = self._verify_protocol()
            if problems:
                raise RegistryError(
                    "Protocol verification failed:\n  " + "\n  ".join(problems)
                )

            logger.info("Protocol BZ:// registered")
        except RegistryError as exc:
            logger.error("Failed to register protocol: %s", exc)
            raise

    def unregister_protocol(self) -> None:
        self.reg.delete_key_tree(HKCU_CLASSES, PROTOCOL_KEY)
        self._notify_assoc_changed()
        logger.info("Protocol BZ:// unregistered")

    def _verify_protocol(self) -> list[str]:
        """Read back every required value; return a list of mismatches."""
        problems: list[str] = []
        checks = {
            f"{PROTOCOL_KEY} (Default)": ("URL:BZ Protocol", PROTOCOL_KEY, ""),
            f"{PROTOCOL_KEY} URL Protocol": ("", PROTOCOL_KEY, "URL Protocol"),
            f"{PROTOCOL_DEFAULT_ICON} (Default)": (
                f"{self.exe},0", PROTOCOL_DEFAULT_ICON, ""
            ),
            f"{PROTOCOL_COMMAND} (Default)": (
                f'"{self.exe}" "%1"', PROTOCOL_COMMAND, ""
            ),
        }
        for label, (expected, subkey, name) in checks.items():
            actual = self.reg.get_value(HKCU_CLASSES, subkey, name)
            if actual is None:
                problems.append(f"{label}: not found (expected {expected!r})")
            elif actual != expected:
                problems.append(f"{label}: expected {expected!r}, got {actual!r}")
        return problems

    @staticmethod
    def _notify_assoc_changed() -> None:
        """Tell Windows Explorer that file/URL associations changed.

        Without this, Explorer may still say "Get an app to open this
        BZ link" until the shell refreshes its association cache.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            SHCNE_ASSOCCHANGED = 0x08000000
            SHCNF_IDLIST = 0x0000
            ctypes.windll.shell32.SHChangeNotify(
                SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None
            )
            logger.debug("SHChangeNotify(SHCNE_ASSOCCHANGED) sent")
        except Exception as exc:
            logger.warning("SHChangeNotify failed: %s", exc)

    # ------------------------------------------------------------------
    # Context menu: Quick Send
    # ------------------------------------------------------------------
    def register_context_menu(self) -> None:
        exe_cmd = self._command_prefix()

        # For files (including ZIPs)
        self._register_menu_entry(
            root=HKCU_CLASSES,
            key=CONTEXT_MENU_FILES,
            command_key=CONTEXT_MENU_COMMAND_FILES,
            exe_cmd=exe_cmd,
            label="Quick Send",
            icon=f"{self.exe},0",
            arguments=f'--send "%1"',
        )

        # For folders
        self._register_menu_entry(
            root=HKCU_CLASSES,
            key=CONTEXT_MENU_DIRS,
            command_key=CONTEXT_MENU_COMMAND_DIRS,
            exe_cmd=exe_cmd,
            label="Quick Send",
            icon=f"{self.exe},0",
            arguments=f'--send "%1"',
        )

        logger.info("Context menu 'Quick Send' registered")

    def unregister_context_menu(self) -> None:
        self.reg.delete_key_tree(HKCU_CLASSES, CONTEXT_MENU_FILES)
        self.reg.delete_key_tree(HKCU_CLASSES, CONTEXT_MENU_DIRS)
        logger.info("Context menu unregistered")

    def _register_menu_entry(self, root, key, command_key, exe_cmd,
                             label, icon, arguments) -> None:
        self.reg.create_key(root, key)
        self.reg.set_value(root, key, "", label)
        self.reg.set_value(root, key, "Icon", icon)
        self.reg.set_value(root, key, "AppUserModelID", APP_ID)

        full_cmd = f'{exe_cmd} {arguments}'
        self.reg.create_key(root, command_key)
        self.reg.set_value(root, command_key, "", full_cmd)

    # ------------------------------------------------------------------
    # Start with Windows
    # ------------------------------------------------------------------
    def register_start_with_windows(self) -> None:
        # Launch the lightweight background process so the global hotkey
        # works even when no BuzzInstaller window is open.  The
        # background process shows no window and uses ~0 CPU while idle.
        cmd = f'{self._command_prefix()} --background'
        self.reg.set_value(HKCU, START_WITH_WINDOWS_KEY,
                           START_WITH_WINDOWS_NAME, cmd)
        logger.info("Start-with-Windows registered (--background)")

    def unregister_start_with_windows(self) -> None:
        self.reg.delete_value(HKCU, START_WITH_WINDOWS_KEY,
                              START_WITH_WINDOWS_NAME)
        logger.info("Start-with-Windows unregistered")

    def is_start_with_windows_enabled(self) -> bool:
        val = self.reg.get_value(HKCU, START_WITH_WINDOWS_KEY,
                                 START_WITH_WINDOWS_NAME)
        return val is not None

    # ------------------------------------------------------------------
    # Background process at login (optional, independent of the tray)
    # ------------------------------------------------------------------
    def register_background_at_login(self) -> None:
        """Launch ONLY the lightweight background hotkey process at login.

        Uses a separate registry value (``BuzzInstallerBackground``) so
        users can opt into the always-on global hotkey without the full
        tray app.
        """
        cmd = f'{self._command_prefix()} --background'
        self.reg.set_value(HKCU, START_WITH_WINDOWS_KEY,
                           "BuzzInstallerBackground", cmd)
        logger.info("Background-at-login registered (--background)")

    def unregister_background_at_login(self) -> None:
        self.reg.delete_value(HKCU, START_WITH_WINDOWS_KEY,
                              "BuzzInstallerBackground")
        logger.info("Background-at-login unregistered")

    def is_background_at_login_enabled(self) -> bool:
        val = self.reg.get_value(HKCU, START_WITH_WINDOWS_KEY,
                                 "BuzzInstallerBackground")
        return val is not None

    # ------------------------------------------------------------------
    # Combined
    # ------------------------------------------------------------------
    def register_all(self) -> None:
        self.register_protocol()
        self.register_context_menu()
        self._notify_assoc_changed()
        logger.info("All Windows integration registered")

    def unregister_all(self) -> None:
        self.unregister_protocol()
        self.unregister_context_menu()
        self.unregister_start_with_windows()
        logger.info("All Windows integration unregistered")

    def is_registered(self) -> bool:
        """Quick check: are the protocol and context menu present?"""
        return (self.reg.key_exists(HKCU_CLASSES, PROTOCOL_COMMAND) and
                self.reg.key_exists(HKCU_CLASSES, CONTEXT_MENU_COMMAND_FILES))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _command_prefix(self) -> str:
        """Return the executable path (quoted if needed)."""
        if " " in self.exe:
            return f'"{self.exe}"'
        return self.exe
