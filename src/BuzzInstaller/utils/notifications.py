"""
Desktop notification helpers.

Uses Windowsballoon / taskbar list APIs via ctypes so there is no
dependency on a heavy notification library.  All calls degrade
gracefully when the platform is unavailable.
"""

from __future__ import annotations

import sys
import ctypes
from ctypes import wintypes
from pathlib import Path

from utils.logging import get_logger

logger = get_logger()

# ``ctypes.wintypes`` does NOT define ``LRESULT``.  On Windows, LRESULT is
# a LONG_PTR (pointer-sized), so ``c_ssize_t`` is the correct mapping on
# both 32- and 64-bit builds.  Using ``wintypes.LRESULT`` raises
# ``AttributeError`` at import time, which previously caused the download
# flow to exit silently in the windowed (no-console) executable.
LRESULT = ctypes.c_ssize_t

# ---------------------------------------------------------------------------
# ctypes wrappers (lazy-loaded so import never fails on non-Windows)
# ---------------------------------------------------------------------------
_shell32 = ctypes.windll.shell32
_user32 = ctypes.windll.user32
_ole32 = ctypes.windll.ole32

# NOTIFYICONDATA struct fields
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIF_STATE = 0x00000008

NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002
NIIF_ERROR = 0x00000003
NIIF_NONE = 0x00000000

WM_USER = 0x0400
WM_SETTINGCHANGE = 0x001A
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETFOCUS = 3

HWND_MESSAGE = -3  # message-only window


class _NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", ctypes.c_uint),
        ("dwStateMask", ctypes.c_uint),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", ctypes.c_uint),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", ctypes.c_uint),
        ("guidItem", ctypes.c_char * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


def _show_balloon(title: str, message: str, info_flags: int = NIIF_INFO,
                  timeout_ms: int = 4000) -> bool:
    """
    Create a temporary message-only window with a tray icon and show a
    balloon notification.  Returns True on success.
    """
    if sys.platform != "win32":
        return False

    # Window class for the message-only window
    class_name = "BuzzInstallerToast"
    _user32.GetModuleHandleW.restype = wintypes.HMODULE
    _user32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    hInstance = _user32.GetModuleHandleW(None)

    wndclass = wintypes.WNDCLASS()
    wndclass.lpfnWndProc = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
    )(_message_only_proc)
    wndclass.hInstance = hInstance
    wndclass.lpszClassName = class_name
    atom = _user32.RegisterClassW(ctypes.byref(wndclass))
    if not atom:
        return False

    hwnd = _user32.CreateWindowExW(0, atom, class_name, 0, 0, 0, 0, 0,
                                    HWND_MESSAGE, None, hInstance, None)
    if not hwnd:
        return False

    # Load a default app icon (use system application icon to avoid needing a file)
    hIcon = _user32.LoadIconW(None, wintypes.LPCWSTR(32512))  # IDI_APPLICATION

    nid = _NOTIFYICONDATA()
    nid.cbSize = ctypes.sizeof(_NOTIFYICONDATA)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_INFO | NIF_MESSAGE
    nid.uCallbackMessage = WM_USER + 1
    nid.hIcon = hIcon
    # Truncate
    nid.szInfo = message[:255]
    nid.szInfoTitle = title[:63]
    nid.dwInfoFlags = info_flags
    nid.uVersion = 4

    _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
    _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # Clean up after timeout
    def _cleanup():
        import time
        time.sleep(timeout_ms / 1000.0)
        _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        _user32.UnregisterClassW(atom, hInstance)

    import threading
    threading.Thread(target=_cleanup, daemon=True).start()
    return True


def _message_only_proc(hwnd, msg, wParam, lParam):
    """Minimal WndProc that just returns 0."""
    return 0


_message_only_proc = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
)(_message_only_proc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
class Balloon:
    """Simple balloon-toast notification via the system tray."""

    @staticmethod
    def show(title: str, message: str, error: bool = False,
             info: bool = False) -> bool:
        flags = NIIF_ERROR if error else (NIIF_INFO if info else NIIF_NONE)
        try:
            ok = _show_balloon(title, message, info_flags=flags)
            if not ok:
                logger.warning("Balloon notification failed")
            return ok
        except Exception as exc:
            logger.warning("Balloon notification error: %s", exc)
            return False


def show_toast(title: str, message: str, *, error: bool = False,
               info: bool = False) -> bool:
    """Module-level convenience wrapper."""
    return Balloon.show(title, message, error=error, info=info)
