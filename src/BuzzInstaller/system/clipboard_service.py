"""
ClipboardService — the single, thread-safe path for ALL clipboard access.

Root cause fixed
----------------
Windows requires clipboard access (OpenClipboard / SetClipboardData /
GetClipboardData / QClipboard) to happen from a thread that owns a
message queue — i.e. the Qt GUI thread.  Previously:

* ``upload_service`` copied the BZ:// link from the **upload worker**
  thread.
* ``hotkey_service`` read/copied the selection from the **hotkey
  message-loop** thread.

Both silently failed (or were unsafe) because the raw Win32 calls were
invoked off the GUI thread.  This service marshals every operation onto
the Qt GUI thread via a queued signal, then waits for the result.

Capabilities
------------
* ``copy(text) -> bool`` — copies on the GUI thread, then **reads the
  clipboard back** and verifies it equals the expected text.  On failure
  it logs the exact exception, the full traceback, AND the actual
  clipboard contents.
* ``read() -> Optional[str]`` — reads clipboard text on the GUI thread.
* ``clear() -> bool`` — empties the clipboard on the GUI thread.

Every feature (upload auto-copy, "Copy Again", hotkey selected-text
retrieval) goes through this single service so behaviour stays
consistent and maintainable.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot

from utils.logging import get_logger

logger = get_logger(__name__)


class ClipboardService(QObject):
    """Thread-safe clipboard access, always executed on the GUI thread."""

    # Signal carrying (fn, result_dict, event).  QueuedConnection routes
    # it to the GUI thread (the service is moved there).
    _operation = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._operation.connect(self._execute, Qt.QueuedConnection)
        self._ensure_gui_thread_affinity()

        # Auto-copy expiry state.  Everything except ``_generation`` is
        # touched only on the GUI thread.
        self._generation_lock = threading.Lock()
        self._generation = 0
        self._expiry_timer: Optional[QTimer] = None
        self._paste_hook = None
        self._paste_hook_proc = None
        self._paste_text = ""
        self._paste_generation = 0
        self._paste_sequence = 0

    def _ensure_gui_thread_affinity(self) -> None:
        """Move this service onto the GUI thread so queued slots run there."""
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None and QThread.currentThread() is not app.thread():
                self.moveToThread(app.thread())
        except Exception as exc:
            logger.warning("ClipboardService thread affinity setup failed: %s", exc)

    @Slot(object)
    def _execute(self, payload: tuple) -> None:
        """Run *fn* on the GUI thread and publish the result."""
        fn, result, event = payload
        try:
            result["value"] = fn()
            result["ok"] = True
        except Exception as exc:
            result["exc"] = exc
            result["tb"] = traceback.format_exc()
        finally:
            event.set()

    def _dispatch(self, fn: Callable[[], object]) -> object:
        """Run *fn* on the GUI thread and return its value (or raise)."""
        app = None
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
        except Exception as exc:
            logger.error("ClipboardService: cannot obtain QApplication: %r\n%s",
                         exc, traceback.format_exc())
            raise RuntimeError("QApplication unavailable for clipboard access") from exc

        if app is None:
            logger.error("ClipboardService: no QApplication exists — clipboard unavailable")
            raise RuntimeError("No QApplication exists for clipboard access")

        # Already on the GUI thread → run directly.
        if QThread.currentThread() is app.thread():
            return fn()

        # Off the GUI thread → marshal via queued signal and wait.
        result: dict = {}
        event = threading.Event()
        self._operation.emit((fn, result, event))
        if not event.wait(timeout=5.0):
            logger.error(
                "ClipboardService: GUI thread did not service request within 5s\n%s",
                traceback.format_stack(),
            )
            raise TimeoutError("Clipboard GUI-thread request timed out")

        if "exc" in result:
            logger.error(
                "ClipboardService: operation raised %r\n%s",
                result["exc"], result.get("tb", ""),
            )
            raise result["exc"]
        return result.get("value")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def copy(self, text: str) -> bool:
        """Copy *text* to the clipboard and VERIFY it landed.

        Logs the exact exception + traceback on failure, and the actual
        clipboard contents if verification fails.
        """
        try:
            self._dispatch(lambda: _qt_clipboard_set(text))
        except Exception:
            logger.error("Clipboard copy FAILED for %r\n%s",
                         text, traceback.format_exc())
            return False

        # Verify by reading back.
        try:
            actual = self._dispatch(lambda: _qt_clipboard_get())
        except Exception:
            logger.error("Clipboard verification read FAILED after copy of %r\n%s",
                         text, traceback.format_exc())
            return False

        if actual != text:
            logger.error(
                "Clipboard verification FAILED: expected %r, "
                "actual clipboard contains %r", text, actual,
            )
            return False

        # A new copy supersedes any pending auto-clear or first-paste watch.
        self._next_generation()
        try:
            self._dispatch(self._cancel_expiry)
        except Exception:
            logger.warning("Could not cancel previous clipboard expiry\n%s",
                           traceback.format_exc())

        logger.info("Clipboard copy verified for %r", text)
        return True

    def copy_auto(self, text: str) -> bool:
        """Copy an automatically generated BZ:// link.

        Applies the user's clipboard-lifetime preference from Settings:
        keep until replaced, clear after a delay, or clear after the first
        paste.
        """
        mode, seconds = self._auto_copy_settings()
        if not self.copy(text):
            return False

        generation = self._current_generation()
        if mode == "timeout":
            self._dispatch(
                lambda: self._arm_timeout(text, generation, seconds)
            )
        elif mode == "first_paste":
            self._dispatch(
                lambda: self._arm_first_paste(text, generation, seconds)
            )
        return True

    @staticmethod
    def _auto_copy_settings() -> tuple[str, int]:
        """Read the auto-copy expiry preference, with safe fallbacks."""
        try:
            from config.config_manager import load_config
            cfg = load_config()
            mode = str(cfg.get("auto_copy_clear_mode", "never") or "never").lower()
            seconds = int(cfg.get("auto_copy_clear_seconds", 10) or 10)
        except Exception:
            mode, seconds = "never", 10
        if mode not in ("never", "timeout", "first_paste"):
            mode = "never"
        seconds = max(1, min(86400, seconds))
        return mode, seconds

    def _next_generation(self) -> int:
        with self._generation_lock:
            self._generation += 1
            return self._generation

    def _current_generation(self) -> int:
        with self._generation_lock:
            return self._generation

    # -- auto-clear implementation (GUI thread only) --------------------
    def _arm_timeout(self, text: str, generation: int, seconds: int) -> None:
        """Clear *text* after *seconds*, unless the clipboard changed."""
        self._cancel_expiry()
        if generation != self._current_generation():
            return

        sequence = _qt_clipboard_sequence_number()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(max(1, int(seconds)) * 1000)
        timer.timeout.connect(
            lambda: self._expire_if_current(text, generation, sequence)
        )
        self._expiry_timer = timer
        timer.start()
        logger.info("Auto-copied link will clear in %s second(s)", seconds)

    def _arm_first_paste(self, text: str, generation: int,
                         fallback_seconds: int) -> None:
        """Clear *text* as soon as the user pastes it for the first time."""
        self._cancel_expiry()
        if generation != self._current_generation():
            return

        if sys.platform != "win32":
            # No global keyboard hook on other platforms — fall back to
            # the timed behaviour rather than leaving the link forever.
            self._arm_timeout(text, generation, fallback_seconds)
            return

        self._paste_text = text
        self._paste_generation = generation
        self._paste_sequence = _qt_clipboard_sequence_number()
        if not self._install_paste_hook():
            logger.warning(
                "Could not watch for the first paste; falling back to %ss timeout",
                fallback_seconds,
            )
            self._paste_text = ""
            self._arm_timeout(text, generation, fallback_seconds)

    def _install_paste_hook(self) -> bool:
        if self._paste_hook:
            return True
        try:
            import ctypes
            user32 = _win_user32()
            kernel32 = _win_kernel32()
            self._paste_hook_proc = _HOOKPROC(self._on_paste_key)
            handle = user32.SetWindowsHookExW(
                _WH_KEYBOARD_LL,
                self._paste_hook_proc,
                kernel32.GetModuleHandleW(None),
                0,
            )
            if not handle:
                logger.warning(
                    "SetWindowsHookExW failed (%s)",
                    ctypes.get_last_error(),
                )
                self._paste_hook_proc = None
                return False
            self._paste_hook = handle
            logger.info("First-paste clipboard watcher armed")
            return True
        except Exception:
            logger.error("Could not install paste hook\n%s",
                         traceback.format_exc())
            self._paste_hook_proc = None
            return False

    def _disarm_paste_hook(self) -> None:
        self._paste_text = ""
        hook = self._paste_hook
        self._paste_hook = None
        if not hook:
            self._paste_hook_proc = None
            return
        try:
            _win_user32().UnhookWindowsHookEx(hook)
            logger.info("First-paste clipboard watcher disarmed")
        except Exception:
            logger.warning("Could not remove paste hook\n%s",
                           traceback.format_exc())
        finally:
            # Keep the callback object alive until the hook is gone.
            self._paste_hook_proc = None

    def _on_paste_key(self, n_code, w_param, l_param):
        """Low-level keyboard hook: detect the first Ctrl+V / Shift+Insert."""
        try:
            if (n_code == _HC_ACTION
                    and w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
                    and self._paste_text):
                import ctypes
                info = ctypes.cast(
                    l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)
                ).contents
                if _is_paste_key(info.vkCode):
                    if _qt_clipboard_sequence_number() != self._paste_sequence:
                        # Another copy replaced our link; stop watching.
                        self._paste_text = ""
                        QTimer.singleShot(0, self._disarm_paste_hook)
                    else:
                        text = self._paste_text
                        generation = self._paste_generation
                        sequence = self._paste_sequence
                        self._paste_text = ""
                        QTimer.singleShot(0, self._disarm_paste_hook)
                        # Give the target application time to read the
                        # clipboard before removing it.
                        QTimer.singleShot(
                            250,
                            lambda: self._expire_if_current(
                                text, generation, sequence
                            ),
                        )
                        logger.info("First paste detected; clearing link")
        except Exception:
            logger.error("Paste hook callback failed\n%s",
                         traceback.format_exc())
        try:
            return _win_user32().CallNextHookEx(None, n_code, w_param, l_param)
        except Exception:
            return 0

    def _expire_if_current(self, text: str, generation: int,
                           sequence: int) -> None:
        """Clear the clipboard only if it still holds our auto-copied text."""
        try:
            if generation != self._current_generation():
                return
            if _qt_clipboard_sequence_number() != sequence:
                return
            if _qt_clipboard_get() != text:
                return
            _qt_clipboard_clear()
            logger.info("Cleared the auto-copied link from the clipboard")
        except Exception:
            logger.error("Clipboard expiry failed\n%s",
                         traceback.format_exc())
        finally:
            self._cancel_expiry()

    def _cancel_expiry(self) -> None:
        timer = self._expiry_timer
        self._expiry_timer = None
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        self._disarm_paste_hook()

    def read(self) -> Optional[str]:
        """Return the current clipboard text, or None on failure/empty."""
        try:
            value = self._dispatch(lambda: _qt_clipboard_get())
            return value if value else None
        except Exception:
            logger.error("Clipboard read FAILED\n%s", traceback.format_exc())
            return None

    def get_sequence_number(self) -> int:
        """Return the Win32 clipboard sequence number (0 if unavailable).

        The sequence number increments every time the clipboard contents
        change.  The hotkey service uses this to detect when a simulated
        Ctrl+C has actually updated the clipboard, instead of reading
        stale pre-copy content.
        """
        try:
            return self._dispatch(lambda: _qt_clipboard_sequence_number())
        except Exception:
            return 0

    def clear(self) -> bool:
        """Empty the clipboard on the GUI thread."""
        try:
            self._dispatch(lambda: _qt_clipboard_clear())
            return True
        except Exception:
            logger.error("Clipboard clear FAILED\n%s", traceback.format_exc())
            return False

    # ------------------------------------------------------------------
    # Full-format preservation (for selected-text mode Ctrl+C fallback)
    # ------------------------------------------------------------------
    def save_full(self) -> dict:
        """Snapshot the entire clipboard (all formats) on the GUI thread.

        Returns a dict that can be passed to :meth:`restore_full`.  If
        the clipboard cannot be read, returns an empty dict (restore
        becomes a no-op).
        """
        try:
            return self._dispatch(lambda: _qt_clipboard_save_full())
        except Exception:
            logger.error("Clipboard full save FAILED\n%s", traceback.format_exc())
            return {}

    def restore_full(self, snapshot: dict) -> bool:
        """Restore a clipboard snapshot (all formats) on the GUI thread.

        Restores every format captured by :meth:`save_full`.  If the
        snapshot is empty, clears the clipboard.  Always runs even if
        the caller raised an exception (call from a ``finally`` block).
        """
        try:
            self._dispatch(lambda: _qt_clipboard_restore_full(snapshot))
            return True
        except Exception:
            logger.error("Clipboard full restore FAILED\n%s", traceback.format_exc())
            return False


# ---------------------------------------------------------------------------
# Qt clipboard primitives (must run on the GUI thread — ClipboardService
# guarantees this).
# ---------------------------------------------------------------------------
def _qt_clipboard_set(text: str) -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().setText(text)


def _qt_clipboard_get() -> Optional[str]:
    from PySide6.QtWidgets import QApplication
    text = QApplication.clipboard().text()
    return text if text else None


def _qt_clipboard_sequence_number() -> int:
    """Return the Win32 clipboard sequence number (0 if unavailable)."""
    import ctypes
    try:
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:
        return 0


def _qt_clipboard_clear() -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().clear()


def _qt_clipboard_save_full() -> dict:
    """Snapshot every clipboard format (text, HTML, image, files, etc.)."""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QImage
    cb = QApplication.clipboard()
    mime = cb.mimeData()
    snapshot: dict = {}
    if mime is None:
        return snapshot
    for fmt in mime.formats():
        try:
            data = mime.data(fmt)
            snapshot[fmt] = bytes(data)
        except Exception:
            continue
    # Also capture text and image for convenience.
    try:
        snapshot["_text"] = cb.text()
    except Exception:
        pass
    try:
        img = cb.image()
        if not img.isNull():
            snapshot["_image"] = img
    except Exception:
        pass
    return snapshot


def _qt_clipboard_restore_full(snapshot: dict) -> None:
    """Restore a clipboard snapshot captured by ``_qt_clipboard_save_full``."""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QMimeData
    cb = QApplication.clipboard()
    if not snapshot:
        cb.clear()
        return
    mime = QMimeData()
    for fmt, data in snapshot.items():
        if fmt.startswith("_"):
            continue
        try:
            mime.setData(fmt, data)
        except Exception:
            continue
    cb.setMimeData(mime)
    # Restore text/image if present (some apps only read these).
    if "_text" in snapshot:
        try:
            cb.setText(snapshot["_text"])
        except Exception:
            pass
    if "_image" in snapshot:
        try:
            cb.setImage(snapshot["_image"])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Win32 low-level keyboard hook (first-paste detection)
# ---------------------------------------------------------------------------
_WH_KEYBOARD_LL = 13
_HC_ACTION = 0
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_VK_CONTROL = 0x11
_VK_SHIFT = 0x10
_VK_V = 0x56
_VK_INSERT = 0x2D

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    _HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    )
    _WIN_USER32 = ctypes.WinDLL("user32", use_last_error=True)
    _WIN_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _WIN_USER32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD,
    ]
    _WIN_USER32.SetWindowsHookExW.restype = wintypes.HANDLE
    _WIN_USER32.CallNextHookEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    ]
    _WIN_USER32.CallNextHookEx.restype = ctypes.c_ssize_t
    _WIN_USER32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
    _WIN_USER32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _WIN_USER32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _WIN_USER32.GetAsyncKeyState.restype = ctypes.c_short
    _WIN_KERNEL32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _WIN_KERNEL32.GetModuleHandleW.restype = wintypes.HMODULE
else:
    _KBDLLHOOKSTRUCT = None
    _HOOKPROC = None
    _WIN_USER32 = None
    _WIN_KERNEL32 = None


def _win_user32():
    return _WIN_USER32


def _win_kernel32():
    return _WIN_KERNEL32


def _is_paste_key(vk_code: int) -> bool:
    """True when *vk_code* is V with Ctrl or Insert with Shift held."""
    user32 = _win_user32()
    if user32 is None:
        return False
    ctrl = bool(user32.GetAsyncKeyState(_VK_CONTROL) & 0x8000)
    shift = bool(user32.GetAsyncKeyState(_VK_SHIFT) & 0x8000)
    return (vk_code == _VK_V and ctrl) or (vk_code == _VK_INSERT and shift)


# ---------------------------------------------------------------------------
# Module-level singleton — the single clipboard entry point for the app.
# ---------------------------------------------------------------------------
clipboard_service = ClipboardService()