#!/usr/bin/env python
"""
BuzzInstaller — entry point & controller.

Dispatch logic
--------------
no args / ``--settings``        → Settings window
``--send <path> [path...]``    → Quick Send (upload) flow
``BZ://code`` / ``--protocol`` → Quick Receive (download) flow
``--install``                  → Register protocol + context menu
``--uninstall``                → Remove all integration
``--tray``                     → System-tray-only mode (start-with-windows)

All GUI modes create a single short-lived ``QApplication`` that exits
as soon as the visible window closes — satisfying the "no permanently
running service / launch only when needed" requirement.
"""

from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Startup logging — before ANY application logic.
# Logs sys.argv, executable path, working directory and PID so that even
# an early failure (import error, arg parsing, deadlock) is visible via
# %LOCALAPPDATA%\BuzzInstaller\logs\startup.log.
# ---------------------------------------------------------------------------
_STARTUP_LOG = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "BuzzInstaller", "logs", "startup.log",
)


def _startup_log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(_STARTUP_LOG), exist_ok=True)
        with open(_STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write("[%d] %s\n" % (os.getpid(), msg))
    except Exception:
        pass


_startup_log("=== BuzzInstaller start ===")
_startup_log("argv = %r" % (sys.argv,))
_startup_log("exe  = %r" % (sys.executable,))
_startup_log("cwd  = %r" % (os.getcwd(),))
_startup_log("pid  = %d" % os.getpid())


def _flush_traceback(tag: str, exc: BaseException) -> None:
    """Write a full traceback to the startup log (never silent exit)."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    _startup_log("%s:\n%s" % (tag, tb))


# ---------------------------------------------------------------------------
# Bootstrap sys.path so that top-level package imports work both when
# running from source and when bundled by PyInstaller.
# ---------------------------------------------------------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

# Also make the *parent* available so ``src.BuzzInstaller`` imports resolve
# when launched from an editable checkout.
_PARENT = os.path.dirname(_DIR)
if _PARENT not in sys.path and os.path.basename(_DIR) == "BuzzInstaller":
    sys.path.insert(0, _PARENT)

from utils.logging import setup_logging, get_logger  # noqa: E402

setup_logging()
logger = get_logger("main")


def _crash_handler(exc_type, exc_value, exc_tb):
    """Last-resort crash handler — NEVER exits silently.

    Writes the full traceback to a crash log (visible in a --windowed
    build with no console) and shows an error dialog when a GUI is
    available.
    """
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        crash_path = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "BuzzInstaller", "logs", "crash.log",
        )
        os.makedirs(os.path.dirname(crash_path), exist_ok=True)
        with open(crash_path, "a", encoding="utf-8") as cf:
            cf.write("\n" + "=" * 60 + "\n")
            cf.write("CRASH (PID %d)\n%s\n" % (os.getpid(), text))
    except Exception:
        pass
    try:
        logger.error("UNCAUGHT EXCEPTION:\n%s", text)
    except Exception:
        pass
    # Show the exception to the user if a GUI can be created.
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv[:1])
        QMessageBox.critical(None, "BuzzInstaller Error", text[-2000:])
    except Exception:
        pass
    # Do not call sys.exit here — propagate the error.
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _crash_handler

# Import after path setup -----------------------------------------------------
from config.config_manager import load_config, save_config  # noqa: E402
from config.config_manager import DEFAULTS  # noqa: E402
from core.buzzheavier_api import (  # noqa: E402
    BuzzHeavierError, NetworkError, UploadError, UploadCancelled,
    DownloadError, DownloadCancelled, InvalidCodeError,
    CloudflareBlockedError, extract_code, is_valid_code,
)
from core.upload_service import UploadService  # noqa: E402
from core.download_service import DownloadService  # noqa: E402
from core.protocol_handler import parse_args, extract_protocol_code, extract_send_paths  # noqa: E402
from system.context_menu import WindowsIntegrationManager  # noqa: E402
from utils.logging import get_logger as _gl  # noqa: E402

# Qt imports (lazy — only needed for GUI modes)
_QT_APP = None


def _ensure_background_process() -> bool:
    """Ensure the lightweight background hotkey process is running.

    The background process owns the global hotkey so it continues to
    work after every window is closed.  If one is already running this
    is a no-op (single instance).  If the hotkey AND all autostart
    options are disabled, no background process is started.
    """
    try:
        from system.background_service import BackgroundService
        if BackgroundService.is_background_running():
            return True
        cfg = load_config()
        if not (cfg.get("enable_global_hotkey", True)
                or cfg.get("start_with_windows", False)
                or cfg.get("start_background_at_login", False)):
            _startup_log("background: hotkey & autostart disabled — not started")
            return False
        _startup_log("background: spawning background process")
        BackgroundService.launch_app(["--background"])
        return True
    except Exception as exc:
        logger.warning("Ensure background process failed (non-fatal): %s", exc)
        return False


def _restart_background_process() -> bool:
    """Force the background process to pick up config changes.

    The background process uses QFileSystemWatcher on the config file,
    but save_config() uses a temp-file + replace pattern which may not
    reliably trigger the watcher on Windows.  We write the config
    directly in-place to force a file-change notification, then ensure
    the background process is running.
    """
    try:
        from config.paths import get_config_path
        import json

        # Re-read the config that was just saved and write it directly
        # (not via temp-file replace) so QFileSystemWatcher fires.
        cfg = load_config()
        path = get_config_path()
        try:
            path.write_text(
                json.dumps(cfg, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.debug("Config written directly to trigger watcher")
        except Exception as exc:
            logger.warning("Direct config write failed: %s", exc)

        # Ensure the background process is running (it will pick up the
        # new config via the watcher).
        from system.background_service import BackgroundService
        if not BackgroundService.is_background_running():
            cfg = load_config()
            if (cfg.get("enable_global_hotkey", True)
                    or cfg.get("start_with_windows", False)
                    or cfg.get("start_background_at_login", False)):
                _startup_log("background: spawning background process")
                BackgroundService.launch_app(["--background"])

        return True
    except Exception as exc:
        logger.warning("Restart background process failed: %s", exc)
        return False


def _get_qt_app():
    """Create (or return existing) QApplication with proper Windows setup."""
    global _QT_APP
    if _QT_APP is not None:
        return _QT_APP
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QCoreApplication
    _QT_APP = QApplication.instance() or QApplication(sys.argv)
    # High-DPI support
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    # Set AppUserModelID for proper Windows taskbar grouping
    _set_app_user_model_id()
    # Window icon
    from PySide6.QtGui import QIcon, QPixmap
    icon_path = _resource_path("assets/buzzinstaller.ico")
    if os.path.exists(icon_path):
        _QT_APP.setWindowIcon(QIcon(icon_path))
    return _QT_APP


def _set_app_user_model_id():
    """Set the AppUserModelID so Windows treats all windows as one app."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        cts = ctypes.windll.user32
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "BuzzInstaller.BuzzInstaller"
        )
    except Exception:
        pass


def _resource_path(relative: str) -> str:
    """Return a path that works both in dev and in a PyInstaller bundle.

    In a PyInstaller onedir build, ``sys._MEIPASS`` points to the
    ``_internal/`` directory where bundled data files (assets) live.
    In source mode, the assets live next to this file.
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # PyInstaller bundle data dir (_internal in onedir)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


# ---------------------------------------------------------------------------
# Headless modes (no GUI)
# ---------------------------------------------------------------------------
def run_install(verbose: bool = False) -> int:
    """Register the BZ:// protocol and Explorer context menu."""
    setup_logging()
    try:
        mgr = WindowsIntegrationManager()
        mgr.register_all()
        logger.info("Windows integration installed successfully.")
        if verbose:
            print("BuzzInstaller integration registered.")
        return 0
    except Exception as exc:
        logger.error("Registration failed: %s", exc)
        if verbose:
            print(f"Registration failed: {exc}", file=sys.stderr)
        return 1


def run_uninstall(verbose: bool = False) -> int:
    """Remove all Windows shell integration."""
    setup_logging()
    try:
        mgr = WindowsIntegrationManager()
        mgr.unregister_all()
        logger.info("Windows integration removed.")
        if verbose:
            print("BuzzInstaller integration removed.")
        return 0
    except Exception as exc:
        logger.error("Uninstall failed: %s", exc)
        if verbose:
            print(f"Uninstall failed: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Upload (Quick Send) flow
# ---------------------------------------------------------------------------
def _err_msg(exc: Exception) -> tuple[str, str]:
    """Map an exception to a (title, user-friendly message)."""
    if isinstance(exc, NetworkError):
        # Surface the ACTUAL failure (exception type + message) instead of
        # a generic "No Internet" — the API now embeds the real cause.
        return ("Upload Failed", str(exc))
    if isinstance(exc, UploadCancelled):
        return ("Upload Cancelled", "The upload was cancelled.")
    if isinstance(exc, InvalidCodeError):
        return ("Invalid Link", str(exc))
    if isinstance(exc, CloudflareBlockedError):
        return ("Blocked by Cloudflare",
                "BuzzHeavier is temporarily blocking automated requests. "
                "Please try again in a few moments, or open the link in your browser.")
    if isinstance(exc, DownloadCancelled):
        return ("Download Cancelled", "The download was cancelled.")
    if isinstance(exc, UploadError):
        return ("Upload Failed", str(exc))
    if isinstance(exc, DownloadError):
        return ("Download Failed", str(exc))
    if isinstance(exc, BuzzHeavierError):
        return ("Error", str(exc))
    # Unexpected
    return ("Unexpected Error", str(exc))


def run_upload(file_paths: list[str]) -> int:
    """Launch the Quick Send (upload) flow."""
    from PySide6.QtWidgets import QApplication
    from ui.progress_window import ProgressWindow, UPLOAD
    from ui.result_window import ResultWindow
    from ui.error_dialog import ErrorDialog
    from ui.theme import get_palette_from_config

    app = _get_qt_app()
    palette = get_palette_from_config()

    # For a single file, the file name is shown directly.
    file_name = os.path.basename(file_paths[0])

    pw = ProgressWindow(mode=UPLOAD, file_name=file_name, palette=palette)

    # State
    result_holder = {"result": None}
    worker_done = threading.Event()

    def worker():
        service = None
        try:
            service = UploadService()
            progress_cb = pw.make_progress_callback()
            cancelled_cb = pw.make_cancelled_checker()
            pw.set_status("Connecting to BuzzHeavier…")

            if len(file_paths) == 1:
                result = service.upload(file_paths[0], progress_cb, cancelled_cb)
            else:
                # Upload all sequentially; combine BZ links
                results = service.upload_multiple(file_paths, progress_cb, cancelled_cb)
                result = results  # list of UploadResult

            result_holder["result"] = result
            pw.finish_ok(result)
        except (UploadCancelled,):
            pw.finish_cancelled()
        except (NetworkError, UploadError, BuzzHeavierError) as exc:
            title, msg = _err_msg(exc)
            pw.finish_error(title, msg)
        except Exception as exc:
            logger.exception("Upload worker error")
            title, msg = _err_msg(exc)
            pw.finish_error("Upload Failed", msg)
        finally:
            # Release the requests.Session (connection pools / sockets)
            # so no network handles linger when the process exits.
            if service is not None:
                try:
                    service.api.close()
                except Exception:
                    pass
            worker_done.set()

    # Connect signals
    def on_result(result):
        pw.hide()
        if isinstance(result, list):
            # Multiple results — show the first; copy latest to clipboard
            bz_urls = [r.bz_url for r in result]
            from system.clipboard_service import clipboard_service
            clipboard_service.copy("\n".join(bz_urls))
            rw = ResultWindow(
                bz_url=result[-1].bz_url,
                buzzheavier_url=result[-1].url,
                file_name=result[-1].file_name,
                file_size_str=result[-1].size_str,
                palette=palette,
            )
            rw.show()
            rw.raise_()
        else:
            rw = ResultWindow(
                bz_url=result.bz_url,
                buzzheavier_url=result.url,
                file_name=getattr(result, "file_name", file_name),
                file_size_str=getattr(result, "size_str", ""),
                palette=palette,
            )
            rw.closed.connect(app.quit)
            rw.show()
            rw.raise_()

    def on_error(title, message):
        pw.hide()
        err = ErrorDialog(title, message, palette=palette)
        err.closed.connect(app.quit)
        err.show()
        err.raise_()

    pw.result_shown.connect(on_result)
    pw.error_shown.connect(on_error)

    # Start worker thread
    t = threading.Thread(target=worker, daemon=True)
    t.start()

    pw.show()
    pw.raise_()
    app.exec()

    # Wait for the worker thread to fully finish (network closed) so
    # no thread outlives app.exec() with open sockets/sessions.
    t.join(timeout=5.0)
    _startup_log("upload: worker joined (alive=%s)" % t.is_alive())

    return 0


# ---------------------------------------------------------------------------
# Download (Quick Receive) flow
# ---------------------------------------------------------------------------
def run_download(code: str) -> int:
    """Launch the Quick Receive (download) flow for a BZ code.

    The Qt event loop (``app.exec()``) is entered immediately and stays
    alive until the worker thread posts ``finish_ok``/``finish_error``,
    which is connected to ``app.quit()``.  The process therefore never
    exits while a download is in progress.
    """
    from PySide6.QtWidgets import QApplication
    from ui.progress_window import ProgressWindow, DOWNLOAD
    from ui.error_dialog import ErrorDialog
    from ui.theme import get_palette_from_config
    from utils.notifications import show_toast

    _startup_log(f"run_download: code={code!r} → URL https://buzzheavier.com/{code}")
    app = _get_qt_app()
    palette = get_palette_from_config()

    pw = ProgressWindow(mode=DOWNLOAD, file_name=code, palette=palette)

    # State
    worker_done = threading.Event()

    def worker():
        service = None
        try:
            service = DownloadService()
            progress_cb = pw.make_progress_callback()
            cancelled_cb = pw.make_cancelled_checker()
            pw.set_status("Resolving download link…")
            _startup_log("download: HTTP request begins (service.download)")

            result = service.download(code, progress_cb, cancelled_cb)
            _startup_log("download: HTTP complete, file=%s" % result.file_path)
            pw.finish_ok(result)
        except DownloadCancelled:
            _startup_log("download: CANCELLED")
            pw.finish_cancelled()
        except (NetworkError, DownloadError, BuzzHeavierError) as exc:
            _startup_log("download: ERROR %r" % (exc,))
            title, msg = _err_msg(exc)
            pw.finish_error(title, msg)
        except Exception as exc:
            logger.exception("Download worker error")
            _startup_log("download: UNEXPECTED %r\n%s" % (exc, traceback.format_exc()))
            title, msg = _err_msg(exc)
            pw.finish_error("Download Failed", msg)
        finally:
            # Release any network handles so nothing lingers at exit.
            if service is not None:
                try:
                    service.api.close()
                except Exception:
                    pass
            worker_done.set()

    def on_result(result):
        pw.hide()
        # Show a balloon notification
        if hasattr(result, "was_extracted") and result.was_extracted:
            body = f"Extracted to {result.extracted_to}"
        else:
            body = f"Saved to {result.file_path}"
        show_toast("Download Complete", f"BZ://{code}\n{body}", info=True)
        _startup_log("download: DONE → app.quit")
        app.quit()

    def on_error(title, message):
        pw.hide()
        show_toast(title, message[:200], error=True)
        # Also show error dialog
        err = ErrorDialog(title, message, palette=palette)
        err.closed.connect(app.quit)
        err.show()
        err.raise_()
        _startup_log("download: ERROR SHOWN (%s) → stays open until closed" % title)

    pw.result_shown.connect(on_result)
    pw.error_shown.connect(on_error)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    pw.show()
    pw.raise_()
    _startup_log("download: entering app.exec() — event loop alive")
    app.exec()
    _startup_log("download: app.exec() returned — process exiting")

    # Wait for the worker thread to fully finish (network closed) so
    # no thread outlives app.exec() with open sockets/sessions.
    t.join(timeout=5.0)
    _startup_log("download: worker joined (alive=%s)" % t.is_alive())

    return 0


# ---------------------------------------------------------------------------
# Settings / dashboard flow
# ---------------------------------------------------------------------------
def run_app(page: str = "home") -> int:
    """Show the main dashboard window."""
    app = _get_qt_app()
    from ui.main_window import MainWindow

    win = MainWindow(page=page)
    win.show()
    win.raise_()
    win.activateWindow()

    app.exec()
    return 0


def run_settings() -> int:
    """Show the dashboard with the Settings page selected."""
    return run_app(page="settings")


def _on_hotkey_changed(spec: str, enabled: bool):
    """Persist and apply a hotkey change."""
    _startup_log("hotkey: changed to %r enabled=%s" % (spec, enabled))
    try:
        cfg = load_config()
        cfg["global_hotkey"] = spec
        cfg["enable_global_hotkey"] = enabled
        save_config(cfg)
        _ensure_background_process()
    except Exception as exc:
        logger.warning("Hotkey change apply failed: %s", exc)


def _on_settings_saved(cfg: dict):
    """Settings were saved — force-restart the background process."""
    _startup_log("settings: saved, force-restarting background process")
    try:
        _restart_background_process()
    except Exception as exc:
        logger.warning("Settings-saved background restart failed: %s", exc)


# ---------------------------------------------------------------------------
# Update check (headless or UI)
# ---------------------------------------------------------------------------
def _has_console() -> bool:
    """True when stdout is usable (a console build or redirected output)."""
    return sys.stdout is not None


def _report_update_result(
    version: str,
    message: str,
    error: bool = False,
    url: str = "",
    update_info=None,
) -> None:
    """Report an update check to stdout or, in the windowed build, the UI."""
    lines = [f"BuzzInstaller {version}", message]
    if url:
        lines.append(f"Download: {url}")

    if _has_console():
        print("\n".join(lines), file=sys.stderr if error else sys.stdout)
        return

    from PySide6.QtWidgets import QMessageBox
    from ui.theme import app_qss, get_palette_from_config

    app = _get_qt_app()
    app.setStyleSheet(app_qss(get_palette_from_config()))

    # An installed build can update itself: reuse the real update dialog
    # (release notes, download progress, install button).
    if update_info is not None and not error and url:
        try:
            from core import updater
            from ui.update_dialog import UpdateDialog

            if updater.can_self_update():
                dialog = UpdateDialog(update_info)
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
                app.exec()
                return
        except Exception as exc:  # fall back to a plain message box
            logger.warning("Could not open the update dialog: %s", exc)

    box = QMessageBox()
    box.setWindowTitle("BuzzInstaller update check")
    box.setIcon(QMessageBox.Critical if error else QMessageBox.Information)
    box.setText(lines[0])
    box.setInformativeText("\n".join(lines[1:]))
    if url and not error:
        open_button = box.addButton("Open release page", QMessageBox.AcceptRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_button:
            import webbrowser

            webbrowser.open(url)
    else:
        box.addButton("OK", QMessageBox.AcceptRole)
        box.exec()


def run_check_updates() -> int:
    """Check GitHub Releases and report the result.

    Writes to stdout when a console is attached. The installed build is
    windowed and has no console, so it opens the in-app update dialog
    (or a message box) instead.
    """
    from core import updater

    version = updater.current_version()
    try:
        info = updater.check_for_update()
    except Exception as exc:
        _report_update_result(version, f"Update check failed: {exc}", error=True)
        return 1
    if info is None:
        _report_update_result(version, "Status: up to date")
        return 0
    _report_update_result(
        version,
        f"Update available: {info.version} ({info.size_str})",
        url=info.html_url,
        update_info=info,
    )
    return 2


# ---------------------------------------------------------------------------
# System tray flow
# ---------------------------------------------------------------------------
def run_tray() -> int:
    """Run in the system tray (started by Windows at login)."""
    from PySide6.QtWidgets import QApplication
    from system.system_tray import SystemTrayManager
    from ui.theme import get_palette_from_config

    app = _get_qt_app()
    palette = get_palette_from_config()

    tray_mgr = SystemTrayManager(app)
    if not tray_mgr.create():
        # Fallback: show the dashboard
        return run_app()
    else:
        tray_mgr.show_main.connect(lambda: _open_main_window("home"))
        tray_mgr.show_send.connect(lambda: _open_main_window("send"))
        tray_mgr.show_settings.connect(lambda: _open_main_window("settings"))
        tray_mgr.show_receive.connect(lambda: _open_main_window("receive"))
        tray_mgr.quit_app.connect(app.quit)

    app.exec()
    return 0


def _open_main_window(page: str = "home"):
    """Open (or focus) the dashboard from the tray process."""
    from PySide6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    app = QApplication.instance()
    if app is None:
        return
    win = getattr(app, "_bz_main_window", None)
    if win is None:
        win = MainWindow(page=page)
        setattr(app, "_bz_main_window", win)
        win.show()
    else:
        win.showNormal()
        win.raise_()
        win.activateWindow()
        win.navigate(page)


def _show_settings_in_existing_app(app, palette):
    """Open the dashboard on the Settings page from the tray."""
    _open_main_window("settings")


def _prompt_for_code_and_download(app, palette):
    """Open the Receive page from the tray."""
    _open_main_window("receive")


def show_error_msg(app, title, message):
    """Show an error dialog (or fallback to a message box)."""
    try:
        from ui.error_dialog import ErrorDialog
        from ui.theme import get_palette_from_config
        palette = get_palette_from_config()
        err = ErrorDialog(title, message, palette=palette)
        err.show()
        err.raise_()
    except Exception:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, title, message)


# ---------------------------------------------------------------------------
# Auto-registration on first run
# ---------------------------------------------------------------------------
def _maybe_auto_register():
    """Register integration on first run."""
    cfg = load_config()
    if not cfg.get("_integration_registered", False):
        try:
            mgr = WindowsIntegrationManager()
            mgr.register_all()
            cfg["_integration_registered"] = True
            save_config(cfg)
            logger.info("Auto-registered Windows integration on first run.")
        except Exception as exc:
            logger.warning("Auto-registration failed: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    _startup_log("main(): application start")
    try:
        args = parse_args()
        _startup_log("main(): argument parsing done → %r" % (args,))
    except Exception:
        _startup_log("main(): ARGUMENT PARSING FAILED\n%s" % traceback.format_exc())
        raise

    # --- Headless modes (no Qt) ---
    if args.install:
        _startup_log("main(): running --install")
        return run_install()
    if args.uninstall:
        _startup_log("main(): running --uninstall")
        return run_uninstall()
    if getattr(args, "check_updates", False):
        _startup_log("main(): running --check-updates")
        return run_check_updates()

    # --- Background mode (no window, global hotkey only) ---
    if args.background:
        _startup_log("main(): running --background (headless hotkey process)")
        _maybe_auto_register()
        from system.background_service import BackgroundService
        # The background process shows no UI.  When a valid BZ link is
        # detected it launches the existing download flow in a fresh
        # BuzzInstaller process (reusing run_download without nesting a
        # second Qt event loop inside this headless one).
        svc = BackgroundService(on_download=BackgroundService.launch_download)
        if not svc.start():
            _startup_log("main(): background already running — exiting")
            return 0
        _startup_log("main(): background ready, entering idle event loop")
        try:
            rc = svc.run()
            _startup_log("main(): background event loop exited (rc=%s)" % (rc,))
            return rc
        finally:
            _startup_log("main(): stopping background service")
            svc.stop()
            _startup_log("main(): background service stopped")

    # --- System tray mode (legacy start-with-windows) ---
    if args.tray:
        _startup_log("main(): running --tray")
        _maybe_auto_register()
        _ensure_background_process()
        return run_tray()

    # --- GUI modes (require Qt) ---
    _get_qt_app()
    # Ensure the background hotkey process is running so the global
    # hotkey keeps working after every window closes.  This is a no-op
    # if one is already running (single instance).
    _ensure_background_process()

    # Protocol activation: BZ://code or --protocol <url>
    code = extract_protocol_code(args)
    _startup_log("main(): extract_protocol_code → %r" % (code,))
    if code:
        _startup_log("main(): PROTOCOL CODE CONFIRMED → download '%s'" % code)
        _maybe_auto_register()
        return run_download(code)

    # Quick Send: --send <files...>
    send_paths = extract_send_paths(args)
    _startup_log("main(): extract_send_paths → %r" % (send_paths,))
    if send_paths:
        _maybe_auto_register()
        return run_upload(send_paths)

    # Settings (default / --settings)
    _maybe_auto_register()
    if args.settings:
        _startup_log("main(): opening dashboard (settings page)")
        return run_app(page="settings")
    _startup_log("main(): opening dashboard")
    return run_app(page="home")


if __name__ == "__main__":
    try:
        rc = main()
        _startup_log("=== BuzzInstaller exit (rc=%s) ===" % (rc,))
        sys.exit(rc)
    except KeyboardInterrupt:
        _startup_log("=== BuzzInstaller exit (KeyboardInterrupt) ===")
        sys.exit(0)
    except Exception:
        logger.exception("Fatal error")
        _startup_log("=== BuzzInstaller exit (Fatal error) ===")
        sys.exit(1)