"""Check integrity levels of running processes and foreground window."""
import ctypes
import ctypes.wintypes as wt
import json

kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32
user32 = ctypes.windll.user32
psapi = ctypes.windll.psapi


def get_integrity(pid):
    try:
        h = kernel32.OpenProcess(0x0400, False, pid)
        if not h:
            return "N/A"
        tok = wt.HANDLE()
        if not advapi32.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
            kernel32.CloseHandle(h)
            return "N/A"
        size = wt.DWORD()
        advapi32.GetTokenInformation(tok, 25, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(tok, 25, buf, size, ctypes.byref(size)):
            kernel32.CloseHandle(tok)
            kernel32.CloseHandle(h)
            return "N/A"
        sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p)).contents.value

        class SID(ctypes.Structure):
            _fields_ = [
                ("Revision", ctypes.c_ubyte),
                ("SubAuthCount", ctypes.c_ubyte),
                ("Auth", ctypes.c_ubyte * 6),
                ("SubAuth", ctypes.c_ulong),
            ]

        sid = SID.from_address(sid_ptr)
        level = sid.SubAuth
        kernel32.CloseHandle(tok)
        kernel32.CloseHandle(h)
        return {
            0x1000: "Untrusted",
            0x2000: "Low",
            0x3000: "Medium",
            0x4000: "High",
            0x5000: "System",
        }.get(level, f"0x{level:X}")
    except Exception as e:
        return f"ERR:{e}"


def get_exe_path(pid):
    try:
        h = kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return "N/A"
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if psapi.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            kernel32.CloseHandle(h)
            return buf.value
        kernel32.CloseHandle(h)
        return "N/A"
    except Exception:
        return "N/A"


results = {}

# Check BuzzInstaller processes
import subprocess
out = subprocess.run(
    ["tasklist", "/FI", "IMAGENAME eq BuzzInstaller.exe", "/FO", "CSV"],
    capture_output=True, text=True
).stdout
lines = [l for l in out.strip().splitlines() if "BuzzInstaller" in l]
pids = []
for line in lines:
    parts = line.split(",")
    if len(parts) >= 2:
        try:
            pids.append(int(parts[1].strip('"')))
        except ValueError:
            pass

for pid in pids:
    results[f"BuzzInstaller_{pid}"] = {
        "integrity": get_integrity(pid),
        "exe": get_exe_path(pid),
    }

# Check foreground window
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
    "integrity": get_integrity(pid.value),
    "exe": get_exe_path(pid.value),
}

# Check this process
results["this_process"] = {
    "pid": ctypes.windll.kernel32.GetCurrentProcessId(),
    "integrity": get_integrity(ctypes.windll.kernel32.GetCurrentProcessId()),
}

with open("integrity_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print("Results written to integrity_results.json")