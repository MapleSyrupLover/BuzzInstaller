"""Check game modules for Raw Input / DirectInput usage."""
import ctypes
import ctypes.wintypes as wt
import json

kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi

# Set proper argtypes
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
psapi.EnumProcessModulesEx.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), wt.DWORD,
    ctypes.POINTER(wt.DWORD), wt.DWORD,
]
psapi.EnumProcessModulesEx.restype = wt.BOOL
psapi.GetModuleFileNameExW.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, wt.LPWSTR, wt.DWORD,
]
psapi.GetModuleFileNameExW.restype = wt.DWORD

GAME_PID = 6388

# Open process with PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
h_process = kernel32.OpenProcess(0x0410, False, GAME_PID)
if not h_process:
    print(f"Failed to open process {GAME_PID}")
    exit(1)

# Get module list
needed = wt.DWORD()
psapi.EnumProcessModulesEx(h_process, None, 0, ctypes.byref(needed), 3)  # LIST_MODULES_ALL

count = needed.value // ctypes.sizeof(ctypes.c_void_p)
modules = (ctypes.c_void_p * count)()
psapi.EnumProcessModulesEx(h_process, modules, needed, ctypes.byref(needed), 3)

results = []
for i in range(count):
    buf = ctypes.create_unicode_buffer(1024)
    size = wt.DWORD(1024)
    if psapi.GetModuleFileNameExW(h_process, modules[i], buf, size):
        name = buf.value
        lower = name.lower()
        if any(k in lower for k in ["rawinput", "dinput", "directinput", "hid", "xinput", "d3d", "opengl", "sdl"]):
            results.append(name)

kernel32.CloseHandle(h_process)

with open("game_modules_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)

print(f"Found {len(results)} relevant modules")
for r in results:
    print(f"  {r}")