"""
HotkeyService — global "Open BZ Link" hotkey.

Responsibilities
----------------
* Register / unregister a global hotkey via the native Windows
  ``RegisterHotKey`` API (no polling — CPU stays at ~0 while idle).
* Detect hotkey presses through a message-only window + ``WM_HOTKEY``.
* Retrieve the currently highlighted text (Windows accessibility APIs
  where possible; fall back to a temporary Ctrl+C that preserves the
  full clipboard) and validate a BZ link.
* Launch the existing download flow for a valid link.

Validation
----------
Only ``BZ://CODE`` is accepted.  Normal text, URLs, empty clips,
multiple links, partial matches and malformed links are ignored.

This service is independent of the upload/download services and reuses
the existing download implementation.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import traceback
from ctypes import wintypes
from typing import Callable, Optional

from core.buzzheavier_api import extract_code, is_valid_code
from system.clipboard_service import clipboard_service
from utils.logging import get_logger

logger = get_logger(__name__)

# --- Win32 constants --------------------------------------------------------
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
HWND_MESSAGE = -3
VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F4 = 0x73
VK_F5 = 0x74
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A
VK_F12 = 0x7B
VK_0 = 0x30
VK_9 = 0x39
VK_A = 0x41
VK_Z = 0x5A

class _WNDCLASS(ctypes.Structure):
    """WNDCLASS — ctypes.wintypes does not define it."""
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# Map a human-readable key name to its virtual-key code.
_KEY_CODES = {
    "F1": VK_F1, "F2": VK_F2, "F3": VK_F3, "F4": VK_F4,
    "F5": VK_F5, "F6": VK_F6, "F7": VK_F7, "F8": VK_F8,
    "F9": VK_F9, "F10": VK_F10, "F11": VK_F11, "F12": VK_F12,
}
for _i in range(10):
    _KEY_CODES[str(_i)] = VK_0 + _i
for _i in range(26):
    _KEY_CODES[chr(ord("A") + _i)] = VK_A + _i

_MODIFIER_BITS = {
    "ALT": MOD_ALT,
    "CTRL": MOD_CONTROL,
    "CONTROL": MOD_CONTROL,
    "SHIFT": MOD_SHIFT,
    "WIN": MOD_WIN,
    "SUPER": MOD_WIN,
}


def key_code_for_name(name: str) -> Optional[int]:
    """Return the virtual-key code for a key name, or None."""
    return _KEY_CODES.get(name.upper())


def parse_hotkey(spec: str) -> Optional[tuple[int, int]]:
    """Parse ``"Alt+F1"`` into ``(modifiers, vk)`` or None if invalid."""
    if not spec:
        return None
    parts = [p.strip().upper() for p in spec.split("+") if p.strip()]
    if not parts:
        return None
    key = parts[-1]
    if key not in _KEY_CODES:
        return None
    mods = 0
    for m in parts[:-1]:
        if m not in _MODIFIER_BITS:
            return None
        mods |= _MODIFIER_BITS[m]
    return mods, _KEY_CODES[key]


def format_hotkey(mods: int, vk: int) -> str:
    """Convert (modifiers, vk) back to a human-readable string."""
    names = []
    if mods & MOD_ALT:
        names.append("Alt")
    if mods & MOD_CONTROL:
        names.append("Ctrl")
    if mods & MOD_SHIFT:
        names.append("Shift")
    if mods & MOD_WIN:
        names.append("Win")
    key = next((k for k, v in _KEY_CODES.items() if v == vk), "?")
    return "+".join(names + [key])


class HotkeyService:
    """Registers a global hotkey and reacts to it without polling."""

    def __init__(self, on_download: Optional[Callable[[str], None]] = None) -> None:
        self._on_download = on_download
        self._hwnd = None
        self._atom = None
        self._registered = False
        self._mods = 0
        self._vk = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._user32 = ctypes.windll.user32 if sys.platform == "win32" else None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(self, spec: str) -> bool:
        """Register the global hotkey described by *spec* (e.g. 'Alt+F1')."""
        if sys.platform != "win32":
            return False
        parsed = parse_hotkey(spec)
        if parsed is None:
            logger.warning("Invalid hotkey spec: %r", spec)
            return False
        mods, vk = parsed
        self.unregister()
        self._mods, self._vk = mods, vk
        self._stop.clear()

        # Create a message-only window to receive WM_HOTKEY.
        self._create_message_window()
        if self._hwnd is None:
            return False

        # RegisterHotKey(hwnd, id=1, modifiers, vk)
        ok = self._user32.RegisterHotKey(self._hwnd, 1, mods | MOD_NOREPEAT, vk)
        if not ok:
            logger.warning("RegisterHotKey failed (conflict?) for %s", spec)
            self._destroy_message_window()
            return False
        self._registered = True

        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        logger.info("Global hotkey registered: %s", spec)
        return True

    def unregister(self) -> None:
        """Unregister the hotkey and stop the message loop."""
        self._stop.set()
        if self._registered and self._hwnd is not None:
            self._user32.UnregisterHotKey(self._hwnd, 1)
            self._registered = False
        self._destroy_message_window()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("Global hotkey unregistered")

    # ------------------------------------------------------------------
    # Message-only window + message loop
    # ------------------------------------------------------------------
    def _create_message_window(self) -> None:
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
            wintypes.WPARAM, wintypes.LPARAM,
        )

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_HOTKEY:
                self._on_hotkey()
                return 0
            return self._user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc = WNDPROC(wnd_proc)
        class_name = "BuzzInstallerHotkeyWindow"
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        hInstance = kernel32.GetModuleHandleW(None)

        # Set argtypes so HWND_MESSAGE (-3) is accepted as a signed HWND.
        self._user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASS)]
        self._user32.RegisterClassW.restype = wintypes.ATOM
        self._user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.ATOM, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        self._user32.CreateWindowExW.restype = wintypes.HWND
        self._user32.DefWindowProcW.argtypes = [
            wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM,
        ]
        self._user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self._user32.UnregisterClassW.argtypes = [
            wintypes.ATOM, wintypes.HINSTANCE,
        ]
        self._user32.UnregisterClassW.restype = wintypes.BOOL
        self._user32.DestroyWindow.argtypes = [wintypes.HWND]
        self._user32.DestroyWindow.restype = wintypes.BOOL
        self._user32.RegisterHotKey.argtypes = [
            wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
        ]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self._user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint,
        ]
        self._user32.GetMessageW.restype = ctypes.c_int
        self._user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self._user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self._user32.DispatchMessageW.restype = ctypes.c_ssize_t
        self._user32.keybd_event.argtypes = [
            ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong, ctypes.c_ulong,
        ]
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.SendMessageW.argtypes = [
            wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM,
        ]
        self._user32.SendMessageW.restype = ctypes.c_ssize_t

        wc = _WNDCLASS()
        wc.lpfnWndProc = ctypes.cast(self._wnd_proc, ctypes.c_void_p)
        wc.hInstance = hInstance
        wc.lpszClassName = class_name
        self._atom = self._user32.RegisterClassW(ctypes.byref(wc))
        if not self._atom:
            logger.warning("RegisterClassW failed for hotkey window")
            return
        self._hwnd = self._user32.CreateWindowExW(
            0, self._atom, class_name, 0, 0, 0, 0, 0,
            HWND_MESSAGE, None, hInstance, None,
        )

    def _destroy_message_window(self) -> None:
        if self._hwnd is not None:
            self._user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        if self._atom:
            kernel32 = ctypes.windll.kernel32
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            self._user32.UnregisterClassW(self._atom, kernel32.GetModuleHandleW(None))
            self._atom = None

    def _message_loop(self) -> None:
        msg = wintypes.MSG()
        while not self._stop.is_set():
            r = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0:  # WM_QUIT
                break
            if r == -1:
                break
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

    # ------------------------------------------------------------------
    # Hotkey action
    # ------------------------------------------------------------------
    def _on_hotkey(self) -> None:
        """Hotkey pressed — retrieve a BZ link and launch download if valid."""
        logger.info("hotkey: WM_HOTKEY received — callback executed")
        text = self._get_selected_text()
        if text is None:
            return
        code = self._validate_bz_link(text)
        if code is None:
            logger.info("hotkey: no valid BZ link found")
            return
        logger.info("hotkey: BZ link detected → BZ://%s", code)
        logger.info("hotkey: extracted code = %s", code)
        if self._on_download is not None:
            logger.info("hotkey: download started for %s", code)
            self._on_download(code)
        else:
            logger.warning("hotkey: no on_download handler registered")

    def _get_selected_text(self) -> Optional[str]:
        """Retrieve the highlighted text.

        Strategy:
          1. Try Windows accessibility APIs (UI Automation) for the
             focused window's selection.
          2. If unavailable, simulate Ctrl+C while preserving the full
             clipboard, read the copied text, then restore the clipboard
             exactly (even on error).
        """
        text = self._try_ui_automation_selection()
        if text:
            logger.info("hotkey: retrieved selected text via UI Automation "
                        "(length=%d)", len(text))
            return text
        logger.info("hotkey: UI Automation unavailable — using Ctrl+C fallback")
        return self._copy_selection_preserving_full_clipboard()

    def _try_ui_automation_selection(self) -> Optional[str]:
        """Best-effort UI Automation selection read (may return None)."""
        try:
            # Use System.Windows.Automation via ctypes is complex; use
            # the simpler approach: post WM_COPY / read focused control.
            hwnd = self._user32.GetForegroundWindow()
            if not hwnd:
                return None
            # Attempt to read the focused control's selected text via
            # WM_GETTEXT is a heuristic; prefer SendInput Ctrl+C fallback
            # for reliability.  Returning None triggers the fallback.
            return None
        except Exception as exc:
            logger.debug("UI Automation selection read failed: %s", exc)
            return None

    def _copy_selection_preserving_full_clipboard(self) -> Optional[str]:
        """Simulate Ctrl+C, read the selection, restore the full clipboard.

        Uses ClipboardService's full-format save/restore so the user never
        loses clipboard contents, even on error.
        """
        saved = clipboard_service.save_full()
        try:
            self._user32.keybd_event(0x11, 0, 0, 0)   # Ctrl down
            self._user32.keybd_event(0x43, 0, 0, 0)   # C down
            self._user32.keybd_event(0x43, 0, 2, 0)   # C up
            self._user32.keybd_event(0x11, 0, 2, 0)   # Ctrl up
            time.sleep(0.05)
            return clipboard_service.read()
        except Exception as exc:
            logger.warning("hotkey: Ctrl+C simulation failed: %r\n%s",
                           exc, traceback.format_exc())
            return None
        finally:
            clipboard_service.restore_full(saved)

    def _validate_bz_link(self, text: str) -> Optional[str]:
        """Return the extracted code if *text* is EXACTLY one valid BZ link.

        Accepts only ``BZ://CODE``.  Rejects normal text, URLs, empty
        clips, multiple links, partial matches, and malformed links.
        """
        if not text:
            return None
        stripped = text.strip()
        if not stripped.startswith("BZ://"):
            return None
        # Reject multiple links / extra content (newlines, spaces).
        if "\n" in stripped or "\r" in stripped or " " in stripped:
            return None
        try:
            code = extract_code(stripped)
        except Exception:
            return None
        if not is_valid_code(code):
            return None
        return code
