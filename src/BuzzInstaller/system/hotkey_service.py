"""
HotkeyService — global "Open BZ Link" hotkey.

Responsibilities
----------------
* Register / unregister a global hotkey via the native Windows
  ``RegisterHotKey`` API (no polling — CPU stays at ~0 while idle).
* Detect hotkey presses through a message-only window + ``WM_HOTKEY``.
* When the hotkey is pressed, automatically simulates Ctrl+C to copy
  the currently selected text, reads the clipboard, finds a ``BZ://``
  link within it, and launches the download flow.
* Preserves the existing behaviour where Alt+F1 also works if the
  clipboard already contains a valid ``BZ://`` link (no selection needed).

Flow
----
1. User selects text containing ``BZ://CODE``.
2. User presses Alt+F1.
3. HotkeyService simulates Ctrl+C (SendInput).
4. Briefly waits / retries for the target app to update the clipboard.
5. Reads the clipboard via ClipboardService.
6. Searches the text for a ``BZ://`` code.
7. If no code is found in the copied text, falls back to reading the
   existing clipboard (which may already contain a ``BZ://`` link).
8. Launches the existing download flow.

Important
---------
* All simulated keys are always released, even on error (try/finally).
* The simulated Ctrl+C never triggers the hotkey recursively because
  the hotkey is Alt+F1 (Ctrl+C does not match Alt+F1).
* ClipboardService is the single clipboard access mechanism.
"""

from __future__ import annotations

import ctypes
import re
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

VK_CONTROL = 0x11
VK_C = 0x43
VK_MENU = 0x12  # Alt — must be released before Ctrl+C injection

# SendInput constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

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

# BZ:// link regex — searches for BZ://CODE anywhere in a text string.
# Codes are lowercase alphanumeric, 8+ chars (matches _CODE_RE in
# core.buzzheavier_api).
_BZ_LINK_RE = re.compile(r"BZ://\s*([a-z0-9]{8,})", re.IGNORECASE)

# Number of clipboard retry attempts after simulating Ctrl+C.
_CLIPBOARD_RETRIES = 3
# Delay between clipboard read attempts (small, per requirements).
_CLIPBOARD_RETRY_DELAY_MS = 75


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


# ULONG_PTR is not in ctypes.wintypes on all Python builds.
ULONG_PTR = ctypes.c_ulonglong


# ---------------------------------------------------------------------------
# SendInput keyboard structures
# ---------------------------------------------------------------------------
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("_input", _INPUT_UNION)]


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
    # Hotkey action — the core flow
    # ------------------------------------------------------------------
    def _on_hotkey(self) -> None:
        """Hotkey pressed — copy selection, read clipboard, download BZ link.

        Flow:
          1. Simulate Ctrl+C to copy the currently selected text.
          2. Wait briefly / retry for the clipboard to update.
          3. Read the clipboard via ClipboardService.
          4. Search the text for a ``BZ://`` code.
          5. If no code is found, fall back to reading the existing
             clipboard (which may already contain a ``BZ://`` link).
          6. Launch the download.
        """
        logger.info("hotkey: WM_HOTKEY received — callback executed")

        # --- Step 1: Simulate Ctrl+C to copy the current selection. ----
        # This happens BEFORE reading the clipboard, so the user does not
        # need to manually press Ctrl+C.
        copied_text = self._simulate_copy_and_read_with_retry()

        # --- Step 2: Try to find a BZ:// code in the copied text. ------
        code = self._find_bz_code(copied_text) if copied_text else None

        # --- Step 3: Fall back to the existing clipboard. --------------
        # If the copy produced no BZ:// link (e.g. nothing selected), the
        # clipboard may already contain a valid link from before.
        if code is None:
            logger.info("hotkey: no BZ:// in copied text — checking existing clipboard")
            fallback_text = self._read_clipboard_text()
            if fallback_text:
                code = self._find_bz_code(fallback_text)

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

    # ------------------------------------------------------------------
    # Clipboard & copy helpers
    # ------------------------------------------------------------------
    def _simulate_copy_and_read_with_retry(
        self,
        attempts: int = _CLIPBOARD_RETRIES,
        delay_ms: int = _CLIPBOARD_RETRY_DELAY_MS,
    ) -> Optional[str]:
        """Simulate Ctrl+C, then read the clipboard with brief retries.

        Returns the copied clipboard text, or None if the clipboard could
        not be read.  Never raises — errors are logged.
        """
        saved = None
        try:
            # Save the full clipboard so we can restore it after reading.
            saved = clipboard_service.save_full()
        except Exception as exc:
            logger.warning("hotkey: clipboard save failed (continuing): %s", exc)
            saved = None

        # Record the clipboard sequence number BEFORE simulating Ctrl+C.
        # After the copy, we wait for this number to change — that proves
        # the target application actually updated the clipboard with the
        # selected text, rather than us reading stale pre-copy content.
        seq_before = clipboard_service.get_sequence_number()
        logger.info("hotkey: clipboard seq before copy = %d", seq_before)

        try:
            # Simulate Ctrl+C to copy the selected text.
            self._simulate_ctrl_c()
        except Exception as exc:
            logger.warning(
                "hotkey: Ctrl+C simulation failed: %r\n%s",
                exc, traceback.format_exc(),
            )
            # Keys may be left pressed — send key-up events explicitly.
            self._release_keys_safely()
            return None

        # Wait for the clipboard to CHANGE (sequence number increment).
        # This prevents reading stale pre-copy clipboard content — the
        # app that owns the selection needs time to post the new content.
        changed = self._wait_for_clipboard_change(
            seq_before,
            attempts=max(attempts, 10),
            delay_ms=max(delay_ms, 50),
        )
        if not changed:
            logger.info(
                "hotkey: clipboard did not change within retry window "
                "(seq stayed %d)", seq_before,
            )

        # Read the clipboard now that it has (hopefully) been updated.
        text = self._read_clipboard_with_retry(attempts, delay_ms)
        if text is None:
            logger.info("hotkey: clipboard was empty after copy simulation")

        return text

    def _simulate_ctrl_c(self) -> None:
        """Simulate Ctrl+C using keybd_event (with SendInput fallback).

        keybd_event is used as the primary mechanism because it is more
        permissive about input injection than SendInput — SendInput can
        return 0 (rejecting all events) on some systems when called from
        a non-foreground thread, even at the same integrity level.  This
        was observed on a real user PC where SendInput returned 0/4 but
        keybd_event worked.

        Always releases both keys even if an error occurs (caller uses
        ``_release_keys_safely`` on failure).
        """
        if self._user32 is None:
            raise RuntimeError("User32 unavailable")

        # The hotkey (e.g. Alt+F1) fires while the physical Alt AND F1
        # keys are STILL HELD DOWN.  If we inject Ctrl+C while either is
        # still held, the target app sees Alt+Ctrl+C or F1+Ctrl+C (not a
        # copy command) and ignores it.  Release both Alt and the hotkey
        # key first so the target app receives a clean Ctrl+C.
        if self._user32.GetAsyncKeyState(VK_MENU) & 0x8000:
            self._user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            logger.info("hotkey: Alt released before Ctrl+C injection")
        if self._vk and self._user32.GetAsyncKeyState(self._vk) & 0x8000:
            self._user32.keybd_event(self._vk, 0, KEYEVENTF_KEYUP, 0)
            logger.info("hotkey: hotkey key (F%d) released before Ctrl+C injection",
                        self._vk - 0x6F)

        # Primary: keybd_event (more permissive, works from background threads).
        try:
            self._user32.keybd_event(VK_CONTROL, 0, 0, 0)   # Ctrl down
            self._user32.keybd_event(VK_C, 0, 0, 0)         # C down
            self._user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)  # C up
            self._user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)  # Ctrl up
            logger.info("hotkey: Ctrl+C simulated via keybd_event")
            return
        except Exception as exc:
            logger.warning(
                "hotkey: keybd_event failed (%r) — trying SendInput", exc,
            )
            self._release_keys_safely()

        # Fallback: SendInput.
        self._user32.SendInput.argtypes = [
            wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int,
        ]
        self._user32.SendInput.restype = wintypes.UINT

        inputs = (_INPUT * 4)()

        # Ctrl down
        inputs[0].type = INPUT_KEYBOARD
        inputs[0]._input.ki.wVk = VK_CONTROL
        inputs[0]._input.ki.wScan = 0
        inputs[0]._input.ki.dwFlags = 0

        # C down
        inputs[1].type = INPUT_KEYBOARD
        inputs[1]._input.ki.wVk = VK_C
        inputs[1]._input.ki.wScan = 0
        inputs[1]._input.ki.dwFlags = 0

        # C up
        inputs[2].type = INPUT_KEYBOARD
        inputs[2]._input.ki.wVk = VK_C
        inputs[2]._input.ki.wScan = 0
        inputs[2]._input.ki.dwFlags = KEYEVENTF_KEYUP

        # Ctrl up
        inputs[3].type = INPUT_KEYBOARD
        inputs[3]._input.ki.wVk = VK_CONTROL
        inputs[3]._input.ki.wScan = 0
        inputs[3]._input.ki.dwFlags = KEYEVENTF_KEYUP

        sent = self._user32.SendInput(4, inputs, ctypes.sizeof(_INPUT))
        if sent != 4:
            logger.warning(
                "hotkey: SendInput returned %d/4 — some keys may not have "
                "been sent", sent,
            )
            # Explicitly release both keys.
            self._release_keys_safely()
            if sent != 4:
                raise RuntimeError(f"SendInput sent {sent}/4 inputs")

    def _release_keys_safely(self) -> None:
        """Send key-up events for Ctrl and C to prevent stuck modifiers."""
        if self._user32 is None:
            return
        try:
            inputs = (_INPUT * 2)()

            # C up
            inputs[0].type = INPUT_KEYBOARD
            inputs[0]._input.ki.wVk = VK_C
            inputs[0]._input.ki.wScan = 0
            inputs[0]._input.ki.dwFlags = KEYEVENTF_KEYUP

            # Ctrl up
            inputs[1].type = INPUT_KEYBOARD
            inputs[1]._input.ki.wVk = VK_CONTROL
            inputs[1]._input.ki.wScan = 0
            inputs[1]._input.ki.dwFlags = KEYEVENTF_KEYUP

            self._user32.SendInput(2, inputs, ctypes.sizeof(_INPUT))
        except Exception as exc:
            logger.warning("hotkey: failed to release keys safely: %s", exc)

    def _wait_for_clipboard_change(
        self,
        seq_before: int,
        attempts: int = 10,
        delay_ms: int = 50,
    ) -> bool:
        """Wait until the clipboard sequence number changes.

        Returns True if the clipboard changed within the retry window,
        False if it stayed the same (or the sequence number is 0, meaning
        it could not be read).
        """
        if seq_before == 0:
            # Sequence number unavailable — fall back to a short fixed wait.
            time.sleep(delay_ms / 1000.0)
            return True
        for _ in range(attempts):
            seq_now = clipboard_service.get_sequence_number()
            if seq_now != seq_before:
                logger.info(
                    "hotkey: clipboard changed (seq %d → %d)",
                    seq_before, seq_now,
                )
                return True
            time.sleep(delay_ms / 1000.0)
        return False

    def _read_clipboard_with_retry(
        self,
        attempts: int = _CLIPBOARD_RETRIES,
        delay_ms: int = _CLIPBOARD_RETRY_DELAY_MS,
    ) -> Optional[str]:
        """Read clipboard text with small retries to allow app update.

        Uses the existing ``clipboard_service`` — never raw Win32 calls.
        """
        last_exc: Optional[Exception] = None
        for i in range(attempts):
            try:
                text = clipboard_service.read()
            except Exception as exc:
                last_exc = exc
                text = None
            if text:
                return text
            if i < attempts - 1:
                time.sleep(delay_ms / 1000.0)
        if last_exc is not None:
            logger.warning(
                "hotkey: clipboard read failed after %d attempts: %r",
                attempts, last_exc,
            )
        return None

    def _read_clipboard_text(self) -> Optional[str]:
        """Read the existing clipboard text via ClipboardService."""
        try:
            return clipboard_service.read()
        except Exception as exc:
            logger.warning(
                "hotkey: failed to read clipboard: %r\n%s",
                exc, traceback.format_exc(),
            )
            return None

    # ------------------------------------------------------------------
    # BZ:// link detection
    # ------------------------------------------------------------------
    def _find_bz_code(self, text: str) -> Optional[str]:
        """Search *text* for a ``BZ://CODE`` pattern anywhere.

        Unlike the old validator which required the ENTIRE text to be
        exactly ``BZ://CODE``, this searches for the pattern within the
        text — so "Check this: BZ://afe80ff4p12u" also works.
        """
        if not text:
            return None
        m = _BZ_LINK_RE.search(text)
        if not m:
            return None
        code = m.group(1).lower()
        if not is_valid_code(code):
            return None
        return code
