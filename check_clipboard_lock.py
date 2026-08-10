"""
Diagnostic: Check whether the clipboard is locked by another process.

This checks:
1. Can we OpenClipboard at all?
2. Who is holding the clipboard?
3. Does the lock persist over time?
4. Can we copy text to the clipboard?

This may explain why the hotkey's Ctrl+C simulation copies successfully
but the read-back fails (clipboard is locked by another owner).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

CF_UNICODETEXT = 13
GHND = 0x0042


def can_open_clipboard() -> tuple[bool, int]:
    """Try to open the clipboard. Returns (success, GetLastError)."""
    ctypes.set_last_error(0)
    ok = bool(user32.OpenClipboard(None))
    err = ctypes.get_last_error()
    if ok:
        user32.CloseClipboard()
    return ok, err


def get_clipboard_owner() -> dict:
    """Get info about the window that opened the clipboard."""
    try:
        hwnd = user32.GetClipboardOwner()
        if not hwnd:
            return {"hwnd": 0, "title": "None (no owner)", "pid": 0, "exe": "N/A"}

        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        exe_path = "N/A"
        try:
            h_process = kernel32.OpenProcess(0x1000, False, pid.value)
            if h_process:
                buf2 = ctypes.create_unicode_buffer(1024)
                size = wt.DWORD(1024)
                if ctypes.windll.psapi.QueryFullProcessImageNameW(
                    h_process, 0, buf2, ctypes.byref(size)
                ):
                    exe_path = buf2.value
                kernel32.CloseHandle(h_process)
        except Exception:
            pass

        return {"hwnd": hwnd, "title": title, "pid": pid.value, "exe": exe_path}
    except Exception as e:
        return {"error": str(e)}


def set_clipboard_text(text: str) -> tuple[bool, str]:
    """Set clipboard text. Returns (success, error)."""
    if not user32.OpenClipboard(None):
        return False, f"OpenClipboard failed (err={ctypes.get_last_error()})"
    try:
        user32.EmptyClipboard()
        size = (len(text) + 1) * 2
        h_global = kernel32.GlobalAlloc(GHND, size)
        if not h_global:
            return False, "GlobalAlloc failed"
        p_global = kernel32.GlobalLock(h_global)
        if not p_global:
            kernel32.GlobalFree(h_global)
            return False, "GlobalLock failed"
        try:
            ctypes.memmove(p_global, ctypes.c_wchar_p(text), size)
        finally:
            kernel32.GlobalUnlock(h_global)
        result = user32.SetClipboardData(CF_UNICODETEXT, h_global)
        if not result:
            return False, f"SetClipboardData failed (err={ctypes.get_last_error()})"
        return True, "OK"
    finally:
        user32.CloseClipboard()


def read_clipboard_text() -> tuple[str | None, str]:
    """Read clipboard text. Returns (text, error)."""
    if not user32.OpenClipboard(None):
        return None, f"OpenClipboard failed (err={ctypes.get_last_error()})"
    try:
        h_global = user32.GetClipboardData(CF_UNICODETEXT)
        if not h_global:
            return None, "No text format on clipboard"
        p_global = kernel32.GlobalLock(h_global)
        if not p_global:
            return None, "GlobalLock failed"
        try:
            return ctypes.c_wchar_p(p_global).value, "OK"
        finally:
            kernel32.GlobalUnlock(h_global)
    finally:
        user32.CloseClipboard()


def main():
    print("=" * 70)
    print("CLIPBOARD LOCK DIAGNOSTIC")
    print("=" * 70)

    # Test 1: Basic open
    ok, err = can_open_clipboard()
    print(f"\n[1] OpenClipboard: {'SUCCESS' if ok else 'FAILED'} (err={err})")

    # Test 2: Clipboard owner
    owner = get_clipboard_owner()
    print(f"\n[2] Clipboard owner:")
    print(f"    HWND:  0x{owner.get('hwnd', 0):X}")
    print(f"    Title: {owner.get('title', 'N/A')}")
    print(f"    PID:   {owner.get('pid', 0)}")
    print(f"    EXE:   {owner.get('exe', 'N/A')}")

    # Test 3: Try to read clipboard
    text, err = read_clipboard_text()
    print(f"\n[3] Read clipboard: {'SUCCESS' if text is not None else 'FAILED'} (err={err})")
    if text is not None:
        print(f"    Length: {len(text)}")

    # Test 4: Try to set clipboard
    success, msg = set_clipboard_text("BZ://abcdefghijkl")
    print(f"\n[4] Set clipboard: {'SUCCESS' if success else 'FAILED'} ({msg})")

    # Test 5: Poll over time
    print(f"\n[5] Polling OpenClipboard over 3 seconds...")
    for i in range(6):
        time.sleep(0.5)
        ok, err = can_open_clipboard()
        print(f"    +{i * 500}ms: {'SUCCESS' if ok else 'FAILED'} (err={err})")

    # Test 6: Verify set actually worked
    if success:
        text, err = read_clipboard_text()
        print(f"\n[6] Verify clipboard set: {'SUCCESS' if text else 'FAILED'} (err={err})")
        if text:
            print(f"    Length: {len(text)}")

    # Test 7: Clipboard sequence number (to detect external changes)
    seq1 = user32.GetClipboardSequenceNumber()
    print(f"\n[7] Clipboard sequence number: {seq1}")
    print("    (If this keeps changing, something is actively modifying the clipboard)")

    print("\n" + "=" * 70)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()