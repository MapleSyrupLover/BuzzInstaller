"""
Diagnostic: Isolate the Ctrl+C simulation from the global hotkey system.

This script:
1. Records the foreground application (title, handle, PID, exe, integrity).
2. Tests keybd_event Ctrl+C simulation.
3. Tests SendInput Ctrl+C simulation.
4. Polls the clipboard at 0/50/100/200/500/1000 ms after each simulation.
5. Reports whether the clipboard changed and whether BZ:// was detected.

Usage:
    python diagnose_hotkey_ctrl_c.py

Instructions:
    - Run this script.
    - It will wait 3 seconds, then focus a text application (e.g. Notepad).
    - Select some text containing BZ://CODE in that application.
    - Press Enter in this console to trigger the first test (keybd_event).
    - Press Enter again to trigger the second test (SendInput).
    - The script will poll the clipboard and report results.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
import traceback
from ctypes import wintypes

# --- Win32 constants ---
WM_HOTKEY = 0x0312
VK_CONTROL = 0x11
VK_C = 0x43
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
CF_UNICODETEXT = 13
GHND = 0x0042

# --- Structures for SendInput ---
ULONG_PTR = ctypes.c_ulonglong

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


def get_foreground_info() -> dict:
    """Get info about the foreground window."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"hwnd": 0, "title": "N/A", "pid": 0, "exe": "N/A"}

    # Window title
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value

    # PID
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    # Executable path
    exe_path = "N/A"
    try:
        h_process = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h_process:
            buf2 = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if psapi.QueryFullProcessImageNameW(h_process, 0, buf2, ctypes.byref(size)):
                exe_path = buf2.value
            kernel32.CloseHandle(h_process)
    except Exception:
        pass

    return {
        "hwnd": hwnd,
        "title": title,
        "pid": pid.value,
        "exe": exe_path,
    }


def get_integrity_level(pid: int) -> str:
    """Get the integrity level of a process."""
    try:
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32

        h_process = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if not h_process:
            return "N/A (OpenProcess failed)"

        # Get token
        h_token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h_process, 0x0008, ctypes.byref(h_token)):  # TOKEN_QUERY
            kernel32.CloseHandle(h_process)
            return "N/A (OpenProcessToken failed)"

        # Get token integrity level
        TOKEN_INTEGRITY_LEVEL = 25
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(h_token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(h_token, TOKEN_INTEGRITY_LEVEL, buf, size, ctypes.byref(size)):
            kernel32.CloseHandle(h_token)
            kernel32.CloseHandle(h_process)
            return "N/A (GetTokenInformation failed)"

        # The SID is at the start of the buffer
        class TOKEN_MANDATORY_LABEL(ctypes.Structure):
            _fields_ = [("Label", ctypes.c_void_p)]

        tml = TOKEN_MANDATORY_LABEL.from_buffer(buf)
        sid = tml.Label

        # Get SID sub-authority (integrity level)
        class SID_IDENTIFIER_AUTHORITY(ctypes.Structure):
            _fields_ = [("Value", ctypes.c_ubyte * 6)]

        class SID(ctypes.Structure):
            _fields_ = [
                ("Revision", ctypes.c_ubyte),
                ("SubAuthorityCount", ctypes.c_ubyte),
                ("IdentifierAuthority", SID_IDENTIFIER_AUTHORITY),
                ("SubAuthority", ctypes.c_ulong * 1),
            ]

        sid_struct = SID.from_address(sid)
        level = sid_struct.SubAuthority[0]

        kernel32.CloseHandle(h_token)
        kernel32.CloseHandle(h_process)

        if level == 0x1000:  # 4096
            return "Untrusted"
        elif level == 0x2000:  # 8192
            return "Low"
        elif level == 0x3000:  # 12288
            return "Medium"
        elif level == 0x4000:  # 16384
            return "High"
        elif level == 0x5000:  # 20480
            return "System"
        else:
            return f"Unknown (0x{level:X})"
    except Exception as exc:
        return f"N/A ({exc})"


def get_clipboard_info() -> dict:
    """Get clipboard accessibility, formats, and text length (not contents)."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    result = {
        "accessible": False,
        "formats": [],
        "text_length": 0,
        "has_text": False,
    }

    if not user32.OpenClipboard(None):
        return result

    try:
        result["accessible"] = True
        # Enumerate formats
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if fmt == 0:
                break
            result["formats"].append(fmt)

        # Check for text
        h_global = user32.GetClipboardData(CF_UNICODETEXT)
        if h_global:
            p_global = kernel32.GlobalLock(h_global)
            if p_global:
                try:
                    text = ctypes.c_wchar_p(p_global).value
                    if text:
                        result["text_length"] = len(text)
                        result["has_text"] = True
                finally:
                    kernel32.GlobalUnlock(h_global)
    finally:
        user32.CloseClipboard()

    return result


def read_clipboard_text() -> str | None:
    """Read clipboard text (for BZ:// detection only)."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if not user32.OpenClipboard(None):
        return None
    try:
        h_global = user32.GetClipboardData(CF_UNICODETEXT)
        if not h_global:
            return None
        p_global = kernel32.GlobalLock(h_global)
        if not p_global:
            return None
        try:
            return ctypes.c_wchar_p(p_global).value
        finally:
            kernel32.GlobalUnlock(h_global)
    finally:
        user32.CloseClipboard()


def has_bz_link(text: str | None) -> bool:
    """Check if text contains a BZ:// link (without logging contents)."""
    if not text:
        return False
    import re
    return bool(re.search(r"BZ://\s*[a-z0-9]{8,}", text, re.IGNORECASE))


def simulate_ctrl_c_keybd_event() -> dict:
    """Simulate Ctrl+C using keybd_event. Returns status info."""
    user32 = ctypes.windll.user32
    result = {
        "method": "keybd_event",
        "success": False,
        "error": None,
        "events_sent": 0,
    }
    try:
        user32.keybd_event(VK_CONTROL, 0, 0, 0)   # Ctrl down
        result["events_sent"] += 1
        user32.keybd_event(VK_C, 0, 0, 0)         # C down
        result["events_sent"] += 1
        user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)  # C up
        result["events_sent"] += 1
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)  # Ctrl up
        result["events_sent"] += 1
        result["success"] = True
    except Exception as exc:
        result["error"] = str(exc)
        result["success"] = False
    return result


def simulate_ctrl_c_sendinput() -> dict:
    """Simulate Ctrl+C using SendInput. Returns status info."""
    user32 = ctypes.windll.user32
    result = {
        "method": "SendInput",
        "success": False,
        "error": None,
        "events_sent": 0,
        "getlasterror": None,
    }

    user32.SendInput.argtypes = [
        wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int,
    ]
    user32.SendInput.restype = wintypes.UINT

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

    try:
        sent = user32.SendInput(4, inputs, ctypes.sizeof(_INPUT))
        result["events_sent"] = sent
        result["getlasterror"] = ctypes.get_last_error()
        result["success"] = (sent == 4)
    except Exception as exc:
        result["error"] = str(exc)
        result["success"] = False

    return result


def poll_clipboard(delays_ms: list[int]) -> list[dict]:
    """Poll the clipboard at specified delays after a Ctrl+C simulation."""
    results = []
    for delay_ms in delays_ms:
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)
        info = get_clipboard_info()
        text = read_clipboard_text()
        results.append({
            "delay_ms": delay_ms,
            "accessible": info["accessible"],
            "formats": info["formats"],
            "text_length": info["text_length"],
            "has_text": info["has_text"],
            "bz_detected": has_bz_link(text),
        })
    return results


def run_test(method_name: str, simulate_fn) -> None:
    """Run a single Ctrl+C simulation test with clipboard polling."""
    print(f"\n{'='*70}")
    print(f"TEST: {method_name}")
    print(f"{'='*70}")

    # Record foreground app
    fg = get_foreground_info()
    print(f"\nForeground application:")
    print(f"  Title:    {fg['title']}")
    print(f"  HWND:     0x{fg['hwnd']:X}")
    print(f"  PID:      {fg['pid']}")
    print(f"  EXE:      {fg['exe']}")
    print(f"  Integrity: {get_integrity_level(fg['pid'])}")

    # Record clipboard BEFORE simulation
    before = get_clipboard_info()
    print(f"\nClipboard BEFORE simulation:")
    print(f"  Accessible: {before['accessible']}")
    print(f"  Formats:    {before['formats']}")
    print(f"  Text length: {before['text_length']}")
    print(f"  Has text:   {before['has_text']}")

    # Simulate Ctrl+C
    print(f"\nSimulating Ctrl+C via {method_name}...")
    sim_result = simulate_fn()
    print(f"  Success:     {sim_result['success']}")
    print(f"  Events sent: {sim_result['events_sent']}/4")
    if sim_result.get('error'):
        print(f"  Error:       {sim_result['error']}")
    if sim_result.get('getlasterror') is not None:
        print(f"  GetLastError: {sim_result['getlasterror']}")

    # Poll clipboard at intervals
    print(f"\nPolling clipboard after simulation:")
    delays = [0, 50, 100, 200, 500, 1000]
    polls = poll_clipboard(delays)
    for p in polls:
        print(f"  +{p['delay_ms']:>4}ms: accessible={p['accessible']}, "
              f"formats={p['formats']}, text_len={p['text_length']}, "
              f"has_text={p['has_text']}, BZ://={p['bz_detected']}")

    # Summary
    changed = any(p['has_text'] and p['text_length'] > 0 for p in polls)
    bz_found = any(p['bz_detected'] for p in polls)
    print(f"\n  SUMMARY: clipboard changed={changed}, BZ:// detected={bz_found}")


def main():
    print("=" * 70)
    print("BUZZINSTALLER HOTKEY CTRL+C DIAGNOSTIC")
    print("=" * 70)
    print(f"Python: {sys.version}")
    print(f"PID:    {os.getpid()}")
    print(f"Integrity: {get_integrity_level(os.getpid())}")

    print("\n" + "=" * 70)
    print("INSTRUCTIONS:")
    print("1. Open a text application (e.g. Notepad).")
    print("2. Type some text containing BZ://CODE (e.g. BZ://abcdefghijkl).")
    print("3. Select that text.")
    print("4. Make sure the text application is the FOREGROUND window.")
    print("5. Return to this console and press Enter to run the keybd_event test.")
    print("6. After the first test, select the text again, then press Enter")
    print("   to run the SendInput test.")
    print("=" * 70)

    input("\nPress Enter to run keybd_event test (make sure text is selected)...")
    run_test("keybd_event", simulate_ctrl_c_keybd_event)

    input("\nPress Enter to run SendInput test (make sure text is selected again)...")
    run_test("SendInput", simulate_ctrl_c_sendinput)

    print("\n" + "=" * 70)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception:
        traceback.print_exc()