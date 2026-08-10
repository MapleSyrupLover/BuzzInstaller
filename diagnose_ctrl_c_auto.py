"""
Automated diagnostic: Isolate the Ctrl+C simulation from the global hotkey system.

This script runs BOTH keybd_event and SendInput Ctrl+C simulations WITHOUT
requiring interactive input, using a countdown to give the user time to
prepare text selection.

Usage:
    python diagnose_ctrl_c_auto.py

Instructions:
    - Run this script.
    - Immediately select text containing BZ://CODE in the foreground
      application (e.g. Notepad). 
    - The script waits 5 seconds, then runs the keybd_event test.
    - Wait 5 more seconds with text selected, then it runs the SendInput test.
    - Results are printed after each test.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
import traceback
from ctypes import wintypes

# --- Win32 constants ---
VK_CONTROL = 0x11
VK_C = 0x43
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
CF_UNICODETEXT = 13

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

    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    exe_path = "N/A"
    try:
        h_process = kernel32.OpenProcess(0x1000, False, pid.value)
        if h_process:
            buf2 = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if psapi.QueryFullProcessImageNameW(h_process, 0, buf2, ctypes.byref(size)):
                exe_path = buf2.value
            kernel32.CloseHandle(h_process)
    except Exception:
        pass

    return {"hwnd": hwnd, "title": buf.value, "pid": pid.value, "exe": exe_path}


def get_integrity_level(pid: int) -> str:
    """Get the integrity level of a process."""
    try:
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32

        h_process = kernel32.OpenProcess(0x0400, False, pid)
        if not h_process:
            return "N/A"
        h_token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h_process, 0x0008, ctypes.byref(h_token)):
            kernel32.CloseHandle(h_process)
            return "N/A"
        TOKEN_INTEGRITY_LEVEL = 25
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(h_token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(h_token, TOKEN_INTEGRITY_LEVEL, buf, size, ctypes.byref(size)):
            kernel32.CloseHandle(h_token)
            kernel32.CloseHandle(h_process)
            return "N/A"

        class TOKEN_MANDATORY_LABEL(ctypes.Structure):
            _fields_ = [("Label", ctypes.c_void_p)]

        tml = TOKEN_MANDATORY_LABEL.from_buffer(buf)
        sid = tml.Label

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

        levels = {0x1000: "Untrusted", 0x2000: "Low", 0x3000: "Medium",
                  0x4000: "High", 0x5000: "System"}
        return levels.get(level, f"Unknown(0x{level:X})")
    except Exception as exc:
        return f"N/A ({exc})"


def get_clipboard_info() -> dict:
    """Get clipboard accessibility, formats, text length."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    result = {"accessible": False, "formats": [], "text_length": 0, "has_text": False}

    if not user32.OpenClipboard(None):
        return result
    try:
        result["accessible"] = True
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if fmt == 0:
                break
            result["formats"].append(fmt)
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
    """Check for BZ:// link (no content logging)."""
    if not text:
        return False
    import re
    return bool(re.search(r"BZ://\s*[a-z0-9]{8,}", text, re.IGNORECASE))


def simulate_ctrl_c_keybd_event() -> dict:
    """keybd_event Ctrl+C. Returns status."""
    user32 = ctypes.windll.user32
    result = {"method": "keybd_event", "success": False, "error": None, "events_sent": 0}
    try:
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        result["events_sent"] += 1
        user32.keybd_event(VK_C, 0, 0, 0)
        result["events_sent"] += 1
        user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
        result["events_sent"] += 1
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        result["events_sent"] += 1
        result["success"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def simulate_ctrl_c_sendinput() -> dict:
    """SendInput Ctrl+C. Returns status."""
    user32 = ctypes.windll.user32
    result = {"method": "SendInput", "success": False, "error": None,
              "events_sent": 0, "getlasterror": None}
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT

    inputs = (_INPUT * 4)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0]._input.ki.wVk = VK_CONTROL
    inputs[1].type = INPUT_KEYBOARD
    inputs[1]._input.ki.wVk = VK_C
    inputs[2].type = INPUT_KEYBOARD
    inputs[2]._input.ki.wVk = VK_C
    inputs[2]._input.ki.dwFlags = KEYEVENTF_KEYUP
    inputs[3].type = INPUT_KEYBOARD
    inputs[3]._input.ki.wVk = VK_CONTROL
    inputs[3]._input.ki.dwFlags = KEYEVENTF_KEYUP

    try:
        sent = user32.SendInput(4, inputs, ctypes.sizeof(_INPUT))
        result["events_sent"] = sent
        result["getlasterror"] = ctypes.get_last_error()
        result["success"] = (sent == 4)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def poll_clipboard(delays_ms: list[int]) -> list[dict]:
    """Poll clipboard at delays after simulation."""
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


def countdown(seconds: int, message: str) -> None:
    """Print a countdown with a message."""
    print(f"\n{message}")
    for i in range(seconds, 0, -1):
        print(f"  {i}...", end="\r", flush=True)
        time.sleep(1)
    print("  GO!")


def run_test(method_name: str, simulate_fn) -> None:
    """Run one simulation test with clipboard polling."""
    print(f"\n{'='*70}")
    print(f"TEST: {method_name} Ctrl+C simulation")
    print(f"{'='*70}")

    fg = get_foreground_info()
    print(f"\nForeground application:")
    print(f"  Title:      {fg['title']}")
    print(f"  HWND:       0x{fg['hwnd']:X}")
    print(f"  PID:        {fg['pid']}")
    print(f"  EXE:        {fg['exe']}")
    print(f"  Integrity:  {get_integrity_level(fg['pid'])}")

    before = get_clipboard_info()
    print(f"\nClipboard BEFORE simulation:")
    print(f"  Accessible: {before['accessible']}")
    print(f"  Formats:    {before['formats']}")
    print(f"  Text length: {before['text_length']}")
    print(f"  Has text:   {before['has_text']}")

    print(f"\nSimulating Ctrl+C via {method_name}...")
    sim = simulate_fn()
    print(f"  Success:     {sim['success']}")
    print(f"  Events sent: {sim['events_sent']}/4")
    if sim.get("error"):
        print(f"  Error:       {sim['error']}")
    if sim.get("getlasterror") is not None:
        print(f"  GetLastError: {sim['getlasterror']}")

    print(f"\nPolling clipboard after simulation:")
    polls = poll_clipboard([0, 50, 100, 200, 500, 1000])
    for p in polls:
        print(f"  +{p['delay_ms']:>4}ms: accessible={p['accessible']}, "
              f"formats={p['formats']}, text_len={p['text_length']}, "
              f"has_text={p['has_text']}, BZ://={p['bz_detected']}")

    changed = any(p["has_text"] and p["text_length"] > 0 for p in polls)
    bz_found = any(p["bz_detected"] for p in polls)
    print(f"\n  SUMMARY: clipboard changed={changed}, BZ:// detected={bz_found}")
    return {"changed": changed, "bz_found": bz_found, "foreground": fg}


def main():
    print("=" * 70)
    print("BUZZINSTALLER CTRL+C SIMULATION DIAGNOSTIC (AUTOMATED)")
    print("=" * 70)
    print(f"Python: {sys.version}")
    print(f"PID:    {os.getpid()}")
    print(f"This process integrity: {get_integrity_level(os.getpid())}")

    print("\nIMPORTANT: Before the countdown ends, select text containing")
    print("BZ://CODE (e.g. BZ://abcdefghijkl) in the foreground app.") 

    # Test 1: keybd_event
    countdown(5, "Select text with BZ://CODE now — keybd_event test starts in:")
    result1 = run_test("keybd_event", simulate_ctrl_c_keybd_event)

    # Test 2: SendInput
    countdown(5, "Select text with BZ://CODE again — SendInput test starts in:")
    result2 = run_test("SendInput", simulate_ctrl_c_sendinput)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"keybd_event:  clipboard_changed={result1['changed']}, BZ://={result1['bz_found']}")
    print(f"SendInput:    clipboard_changed={result2['changed']}, BZ://={result2['bz_found']}")
    if not result1["changed"] and not result2["changed"]:
        print("\n>>> BOTH methods failed to change the clipboard.")
        print(">>> This indicates the simulated input is NOT reaching the")
        print(">>> foreground application (or is being blocked/intercepted).")
    elif result1["changed"] and not result2["changed"]:
        print("\n>>> keybd_event worked, SendInput didn't.")
    elif not result1["changed"] and result2["changed"]:
        print("\n>>> SendInput worked, keybd_event didn't.")
    else:
        print("\n>>> Both methods worked! The issue may be specific to the")
        print(">>> hotkey context (background thread injection).")

    print("\nDIAGNOSTIC COMPLETE")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception:
        traceback.print_exc()