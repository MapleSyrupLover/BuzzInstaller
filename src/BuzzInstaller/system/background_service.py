"""
BackgroundService — lightweight background process for the global hotkey.

Purpose
-------
The global hotkey must work even when the main BuzzInstaller window is
closed.  This service runs a minimal, headless Qt event loop that:

* Registers the global hotkey via ``RegisterHotKey`` (no polling).
* Waits only on Windows events (WM_HOTKEY) — effectively zero CPU idle.
* Uses a single-instance mutex so only one background process runs.
* Watches the config file with ``QFileSystemWatcher`` (event-driven,
  not polling) so hotkey changes apply without restarting the process.
* Does NOT create the main window.
* When a valid BZ link is detected, launches the existing download flow
  in a fresh BuzzInstaller process — the UI is only spawned when needed.

A minimal ``QApplication`` is required because clipboard access must
happen on the Qt GUI thread (see ClipboardService).  The background
process keeps that thread alive but shows no window.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from typing import Callable, Optional

from PySide6.QtWidgets import QApplication

from config.config_manager import load_config
from config.paths import get_config_path
from system.hotkey_service import HotkeyService
from utils.logging import get_logger

logger = get_logger(__name__)

# Single-instance mutex name (global namespace so it's system-wide).
_MUTEX_NAME = "Global\\BuzzInstallerBackgroundMutex"

# Windows process-creation flags for spawning detached instances.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


class BackgroundService:
    """Runs the global hotkey in a headless background process."""

    def __init__(self, on_download: Optional[Callable[[str], None]] = None) -> None:
        self._on_download = on_download
        self._app: Optional[QApplication] = None
        self._hotkey: Optional[HotkeyService] = None
        self._watcher = None
        self._mutex_handle = None
        self._owns_mutex = False

    # ------------------------------------------------------------------
    # Single-instance
    # ------------------------------------------------------------------
    @staticmethod
    def is_background_running() -> bool:
        """True if a background instance currently holds the mutex.

        This is the "communicate with the existing process" check: the
        foreground probes for an existing background instance before
        spawning a new one, and a spawned ``--background`` process uses
        ``CreateMutexW`` so only one instance can ever run.
        """
        if sys.platform != "win32":
            return False
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenMutexW.restype = ctypes.c_void_p
            kernel32.OpenMutexW.argtypes = [
                ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p,
            ]
            # SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenMutexW(0x00100000, 0, _MUTEX_NAME)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception as exc:
            logger.warning("Background: mutex probe failed: %s", exc)
            return False

    def _acquire_mutex(self) -> bool:
        """Try to acquire the single-instance mutex.

        Returns True if this process owns it (i.e. it is the one and only
        background instance).  Returns False if another instance already
        holds it.
        """
        if sys.platform != "win32":
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.CreateMutexW.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p,
            ]
            handle = kernel32.CreateMutexW(None, 1, _MUTEX_NAME)
            if not handle:
                logger.warning("CreateMutexW failed")
                return False
            # ERROR_ALREADY_EXISTS = 183
            already_exists = ctypes.get_last_error() == 183
            self._mutex_handle = handle
            if already_exists:
                logger.info("Another background instance is already running")
                return False
            self._owns_mutex = True
            return True
        except Exception as exc:
            logger.warning("Mutex acquisition failed: %s", exc)
            return True  # fall back to allowing this instance

    def _release_mutex(self) -> None:
        if self._mutex_handle:
            try:
                ctypes.windll.kernel32.ReleaseMutex(self._mutex_handle)
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            except Exception:
                pass
            self._mutex_handle = None
            self._owns_mutex = False

    # ------------------------------------------------------------------
    # Process spawning (used to launch the main UI / download flow)
    # ------------------------------------------------------------------
    @staticmethod
    def _app_command(extra: list[str]) -> list[str]:
        """Return the command used to launch a new BuzzInstaller instance."""
        if getattr(sys, "frozen", False):
            return [sys.executable] + extra
        main_py = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "main.py")
        )
        return [sys.executable, main_py] + extra

    @staticmethod
    def launch_app(extra: list[str]) -> None:
        """Launch a detached BuzzInstaller instance with *extra* args.

        Used both to spawn the initial ``--background`` process and to
        open the download UI when a valid BZ link is detected.  The
        spawned process is fully detached so the background never blocks.

        In the PyInstaller onedir distribution each instance runs
        directly from the installed ``BuzzInstaller/`` directory — there
        is no ``_MEI*`` temporary extraction, so no environment
        manipulation is needed.  The background process simply runs as a
        separate, detached instance of the same executable.
        """
        try:
            args = BackgroundService._app_command(extra)
            popen_kwargs: dict = {"close_fds": True}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
                )
            logger.info("Background: launching %r", args)
            subprocess.Popen(args, **popen_kwargs)
        except Exception as exc:
            logger.error("Background: failed to launch %r: %s", extra, exc)

    @staticmethod
    def launch_download(code: str) -> None:
        """Launch the existing download flow for *code* in a fresh process.

        This reuses the existing download implementation (``run_download``
        inside ``main.py``) without duplicating any download logic.
        """
        logger.info("Background: launch download for BZ://%s", code)
        BackgroundService.launch_app([f"BZ://{code}"])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Start the background service.  Returns True if running."""
        if not self._acquire_mutex():
            return False  # another instance is already running

        # A QApplication is required for GUI-thread clipboard access.
        self._app = QApplication.instance() or QApplication(sys.argv[:1])
        # Do NOT create any window.

        # Apply the hotkey from config.
        self._apply_hotkey_from_config()

        # Watch the config file so hotkey changes apply immediately
        # (event-driven; QFileSystemWatcher uses ReadDirectoryChangesW,
        #  not polling — CPU stays at ~0 while idle).
        try:
            from PySide6.QtCore import QFileSystemWatcher
            self._watcher = QFileSystemWatcher()
            self._watcher.addPath(str(get_config_path()))
            self._watcher.fileChanged.connect(self._on_config_changed)
        except Exception as exc:
            logger.warning(
                "Background: config watcher failed (hotkey changes "
                "will require a restart): %s", exc,
            )

        logger.info("Background service started (no window, waiting for hotkey)")
        return True

    def _apply_hotkey_from_config(self) -> None:
        """Register/unregister the hotkey based on the current config."""
        if self._hotkey is not None:
            self._hotkey.unregister()
            self._hotkey = None
        cfg = load_config()
        enabled = cfg.get("enable_global_hotkey", True)
        spec = cfg.get("global_hotkey", "Alt+F1")
        if enabled:
            self._hotkey = HotkeyService(on_download=self._dispatch_download)
            ok = self._hotkey.register(spec)
            if not ok:
                logger.warning("Background: hotkey registration failed for %s", spec)
        else:
            logger.info("Background: global hotkey disabled in config")

    def _on_config_changed(self, _path: str) -> None:
        """Config file changed — re-read and re-apply the hotkey."""
        try:
            # save_config() writes a temp file then replaces the target,
            # which drops the watch; re-add it.
            try:
                self._watcher.addPath(str(get_config_path()))
            except Exception:
                pass
            self._apply_hotkey_from_config()

            # If BOTH the hotkey and all autostart options are disabled,
            # the background process no longer serves a purpose — exit.
            cfg = load_config()
            if not (cfg.get("enable_global_hotkey", True)
                    or cfg.get("start_with_windows", False)
                    or cfg.get("start_background_at_login", False)):
                logger.info("Background: hotkey & autostart disabled — exiting")
                if self._app is not None:
                    self._app.quit()
        except Exception as exc:
            logger.warning("Background: config change handling failed: %s", exc)

    def _dispatch_download(self, code: str) -> None:
        """Dispatch a detected BZ link to the existing download pipeline.

        The background process shows no UI, so the download flow is
        launched in a fresh BuzzInstaller process.  This keeps the
        background lightweight and avoids nested Qt event loops.
        """
        logger.info("Background: dispatching download for BZ://%s", code)
        if self._on_download is not None:
            try:
                self._on_download(code)
            except Exception as exc:
                logger.error("Background: dispatch failed: %s", exc)
        else:
            logger.warning("Background: no on_download handler registered")

    def run(self) -> int:
        """Run the Qt event loop until quit.  Returns exit code."""
        if self._app is None:
            return 1
        logger.info("Background: entering event loop (idle, ~0 CPU)")
        return self._app.exec()

    def stop(self) -> None:
        """Stop the background service."""
        if self._hotkey is not None:
            self._hotkey.unregister()
            self._hotkey = None
        if self._watcher is not None:
            try:
                self._watcher.fileChanged.disconnect(self._on_config_changed)
            except Exception:
                pass
            self._watcher = None
        if self._app is not None:
            self._app.quit()
        self._release_mutex()
        logger.info("Background service stopped")