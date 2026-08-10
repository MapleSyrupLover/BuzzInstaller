"""Check game window style and fullscreen status."""
import ctypes
import ctypes.wintypes as wt
import json

user32 = ctypes.windll.user32

# Find the game window by PID 6388
windows = []


@ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
def enum_proc(h, l):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    if pid.value == 6388:
        length = user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(h, buf, length + 1)
        windows.append({"hwnd": h, "title": buf.value})
    return True


user32.EnumWindows(enum_proc, 0)

results = {"windows": []}
for w in windows:
    hwnd = w["hwnd"]
    style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
    exstyle = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    sm_cx = user32.GetSystemMetrics(0)
    sm_cy = user32.GetSystemMetrics(1)
    w_width = rect.right - rect.left
    w_height = rect.bottom - rect.top
    results["windows"].append({
        "hwnd": f"0x{hwnd:X}",
        "title": w["title"],
        "style": f"0x{style:X}",
        "exstyle": f"0x{exstyle:X}",
        "rect": f"({rect.left},{rect.top})-({rect.right},{rect.bottom})",
        "size": f"{w_width}x{w_height}",
        "screen": f"{sm_cx}x{sm_cy}",
        "fullscreen": (w_width == sm_cx and w_height == sm_cy),
    })

# Also check foreground
hwnd = user32.GetForegroundWindow()
pid = wt.DWORD()
user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
length = user32.GetWindowTextLengthW(hwnd)
buf = ctypes.create_unicode_buffer(length + 1)
user32.GetWindowTextW(hwnd, buf, length + 1)
results["foreground"] = {
    "hwnd": f"0x{hwnd:X}",
    "pid": pid.value,
    "title": buf.value,
}

with open("game_window_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print("Results written to game_window_results.json")