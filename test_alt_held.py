"""
Diagnostic: Check if Alt key is still physically held when Alt+F1 hotkey fires.

This registers Alt+F1, and when the hotkey fires, checks GetAsyncKeyState(VK_MENU)
to see if Alt is still held down. It also checks the state of Ctrl.

This verifies the theory that the physical Alt key is still held when the
hotkey sends Ctrl+C, causing the target app to see Alt+Ctrl+C instead of Ctrl+C.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_NOREPEAT = 0x4000
VK_F1 = 0x70
VK_F2 = 0x71
VK_MENU = 0x12  # Alt
VK_CONTROL = 0x11
VK_C = 0x43
HWND_MESSAGE = -3
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class _WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


def is_key_down(vk):
    """Check if a key is physically held down."""
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def main():
    print("=" * 70)
    print("ALT KEY STATE DIAGNOSTIC")
    print("=" * 70)
    print("This registers Alt+F2 (since Alt+F1 is taken by BuzzInstaller)")
    print("and checks whether Alt is still held when the hotkey fires.")
    print()
    print("INSTRUCTIONS:")
    print("1. Open Notepad and type some text with BZ://CODE")
    print("2. Select the text")
    print("3. Press Alt+F2")
    print("4. The script will report the key states at the moment the hotkey fires")
    print()

    # Set up message-only window
    kernel32.GetModuleHandleW.restype = wt.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    hInstance = kernel32.GetModuleHandleW(None)

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM,
    )

    hotkey_fired = threading.Event()
    key_states = {}

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_HOTKEY:
            # Record key states IMMEDIATELY
            key_states["alt_down"] = is_key_down(VK_MENU)
            key_states["ctrl_down"] = is_key_down(VK_CONTROL)
            key_states["c_down"] = is_key_down(VK_C)
            hotkey_fired.set()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wnd_proc_cb = WNDPROC(wnd_proc)

    user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASS)]
    user32.RegisterClassW.restype = wt.ATOM
    user32.CreateWindowExW.argtypes = [
        wt.DWORD, wt.ATOM, wt.LPCWSTR, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
    ]
    user32.CreateWindowExW.restype = wt.HWND
    user32.DefWindowProcW.argtypes = [
        wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM,
    ]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.RegisterHotKey.argtypes = [
        wt.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
    ]
    user32.RegisterHotKey.restype = wt.BOOL
    user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wt.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wt.MSG), wt.HWND, ctypes.c_uint, ctypes.c_uint,
    ]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t

    wc = _WNDCLASS()
    wc.lpfnWndProc = ctypes.cast(wnd_proc_cb, ctypes.c_void_p)
    wc.hInstance = hInstance
    wc.lpszClassName = "AltStateDiagWindow"
    atom = user32.RegisterClassW(ctypes.byref(wc))
    if not atom:
        print("RegisterClassW failed")
        return

    hwnd = user32.CreateWindowExW(
        0, atom, "AltStateDiagWindow", 0, 0, 0, 0, 0,
        HWND_MESSAGE, None, hInstance, None,
    )
    if not hwnd:
        print("CreateWindowExW failed")
        return

    ok = user32.RegisterHotKey(hwnd, 1, MOD_ALT | MOD_NOREPEAT, VK_F2)
    if not ok:
        print("RegisterHotKey failed (conflict?)")
        return

    print("Alt+F2 registered. Press Alt+F2 now...")
    print("(Make sure text is selected in Notepad first)")

    # Message loop
    msg = wt.MSG()
    while not hotkey_fired.is_set():
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r == 0 or r == -1:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    if hotkey_fired.is_set():
        print("\n" + "=" * 70)
        print("HOTKEY FIRED — KEY STATES AT THAT MOMENT:")
        print("=" * 70)
        print(f"  Alt key held down:  {key_states.get('alt_down', 'N/A')}")
        print(f"  Ctrl key held down: {key_states.get('ctrl_down', 'N/A')}")
        print(f"  C key held down:    {key_states.get('c_down', 'N/A')}")
        print()
        if key_states.get("alt_down"):
            print(">>> ALT IS STILL HELD DOWN when the hotkey fires!")
            print(">>> This means the target app sees Alt+Ctrl+C, not Ctrl+C!")
            print(">>> This is the ROOT CAUSE of the issue.")
        else:
            print(">>> Alt is NOT held down when the hotkey fires.")
            print(">>> The issue is something else.")

    user32.UnregisterHotKey(hwnd, 1)
    user32.DestroyWindow(hwnd)


if __name__ == "__main__":
    main()