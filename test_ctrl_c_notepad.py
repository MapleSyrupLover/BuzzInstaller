"""
Fully automated test: Simulate Ctrl+C against Notepad (known-good target).

This isolates the Ctrl+C simulation from the game. It:
1. Sets the clipboard to a BZ:// text programmatically
2. Opens Notepad
3. Pastes the text (Ctrl+V)
4. Selects all text (Ctrl+A)
5. Simulates Ctrl+C via keybd_event
6. Polls the clipboard
7. Simulates Ctrl+C via SendInput
8. Polls the clipboard again

This proves whether the simulation methods work at all on this PC.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
import traceback

VK_CONTROL = 0x11
VK_C = 0x43
VK_A = 0x41
VK_V = 0x56
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
CF_UNICODETEXT = 13
GHND = 0x0042

ULONG_PTR = ctypes.c_ulonglong
TEST_TEXT = "BZ://abcdefghijkl"

# --- Set proper ctypes signatures for 64-bit handles ---
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wt.UINT]
user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.EmptyClipboard.restype = wt.BOOL
user32.CloseClipboard.restype = wt.BOOL


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("_input", _INPUT_UNION)]


def set_clipboard_text(text: str) -> bool:
    """Set clipboard text via Win32 API."""
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        size = (len(text) + 1) * 2
        h_global = kernel32.GlobalAlloc(GHND, size)
        if not h_global:
            return False
        p_global = kernel32.GlobalLock(h_global)
        if not p_global:
            kernel32.GlobalFree(h_global)
            return False
        try:
            ctypes.memmove(p_global, ctypes.c_wchar_p(text), size)
        finally:
            kernel32.GlobalUnlock(h_global)
        result = user32.SetClipboardData(CF_UNICODETEXT, h_global)
        return bool(result)
    finally:
        user32.CloseClipboard()


def get_clipboard_info():
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


def read_clipboard_text():
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


def has_bz_link(text):
    if not text:
        return False
    import re
    return bool(re.search(r"BZ://\s*[a-z0-9]{8,}", text, re.IGNORECASE))


def send_keybd(vk, keyup=False):
    """Send a single key via keybd_event."""
    flags = KEYEVENTF_KEYUP if keyup else 0
    user32.keybd_event(vk, 0, flags, 0)


def send_combo(vk):
    """Send Ctrl+<key> via keybd_event."""
    send_keybd(VK_CONTROL)
    send_keybd(vk)
    send_keybd(vk, keyup=True)
    send_keybd(VK_CONTROL, keyup=True)


def get_foreground_info():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"hwnd": 0, "title": "N/A", "pid": 0}
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return {"hwnd": hwnd, "title": buf.value, "pid": pid.value}


def simulate_ctrl_c_keybd_event():
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


def simulate_ctrl_c_sendinput():
    result = {"method": "SendInput", "success": False, "error": None,
              "events_sent": 0, "getlasterror": None}
    user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    user32.SendInput.restype = wt.UINT

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


def poll_clipboard(delays_ms):
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


def run_copy_test(method_name, simulate_fn):
    """Run one copy test: Ctrl+C, poll clipboard."""
    print(f"\n--- Testing {method_name} Ctrl+C ---")

    before = get_clipboard_info()
    print(f"  Clipboard BEFORE: accessible={before['accessible']}, "
          f"text_len={before['text_length']}, has_text={before['has_text']}")

    sim = simulate_fn()
    print(f"  Simulation: success={sim['success']}, events={sim['events_sent']}/4")
    if sim.get("error"):
        print(f"  Error: {sim['error']}")
    if sim.get("getlasterror") is not None:
        print(f"  GetLastError: {sim['getlasterror']}")

    polls = poll_clipboard([0, 50, 100, 200, 500, 1000])
    for p in polls:
        print(f"  +{p['delay_ms']:>4}ms: accessible={p['accessible']}, "
              f"text_len={p['text_length']}, has_text={p['has_text']}, "
              f"BZ://={p['bz_detected']}")

    changed = any(p["has_text"] and p["text_length"] > 0 for p in polls)
    bz = any(p["bz_detected"] for p in polls)
    print(f"  RESULT: clipboard_changed={changed}, BZ://={bz}")
    return changed, bz


def main():
    print("=" * 70)
    print("CTRL+C SIMULATION TEST AGAINST NOTEPAD")
    print("=" * 70)

    # 1. Set clipboard text programmatically
    print("\n[1] Setting clipboard text...")
    if not set_clipboard_text(TEST_TEXT):
        print("  FAILED to set clipboard!")
        return
    print(f"  Clipboard set (length={len(TEST_TEXT)})")

    # 2. Open Notepad
    print("\n[2] Opening Notepad...")
    subprocess.Popen(["notepad.exe"])
    time.sleep(2)

    # 3. Find Notepad window
    hwnd = user32.FindWindowW(None, "Untitled - Notepad")
    if not hwnd:
        hwnd = user32.FindWindowW("Notepad", None)
    if not hwnd:
        print("  ERROR: Could not find Notepad window!")
        return

    user32.SetForegroundWindow(hwnd)
    time.sleep(1)

    fg = get_foreground_info()
    print(f"  Foreground: {fg['title']} (hwnd=0x{fg['hwnd']:X}, pid={fg['pid']})")

    # 4. Paste text (Ctrl+V)
    print("\n[3] Pasting text (Ctrl+V)...")
    send_combo(VK_V)
    time.sleep(0.5)

    # 5. Select all (Ctrl+A)
    print("\n[4] Selecting all text (Ctrl+A)...")
    send_combo(VK_A)
    time.sleep(0.5)

    # 6. Test keybd_event Ctrl+C
    keybd_changed, keybd_bz = run_copy_test("keybd_event", simulate_ctrl_c_keybd_event)

    # 7. Re-select all and test SendInput Ctrl+C
    print("\n[6] Re-selecting all text for SendInput test...")
    send_combo(VK_A)
    time.sleep(0.3)
    sendinput_changed, sendinput_bz = run_copy_test("SendInput", simulate_ctrl_c_sendinput)

    # 8. Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"keybd_event:  clipboard_changed={keybd_changed}, BZ://={keybd_bz}")
    print(f"SendInput:    clipboard_changed={sendinput_changed}, BZ://={sendinput_bz}")

    if not keybd_changed and not sendinput_changed:
        print("\n>>> BOTH methods FAILED against Notepad!")
        print(">>> Ctrl+C simulation itself is broken on this PC,")
        print(">>> independent of the target application.")
    elif keybd_changed and not sendinput_changed:
        print("\n>>> keybd_event works, SendInput doesn't.")
    elif not keybd_changed and sendinput_changed:
        print("\n>>> SendInput works, keybd_event doesn't (hotkey uses keybd_event — BUG!).")
    else:
        print("\n>>> BOTH methods work against Notepad!")
        print(">>> Simulation is fine. Issue is specific to the game target.")

    print("\nDONE")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception:
        traceback.print_exc()