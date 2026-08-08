"""
Background launcher — short-lived intermediary that waits for the
foreground BuzzInstaller process to fully exit before starting the
long-lived ``--background`` process.

Why: PyInstaller onefile.  If the foreground spawns ``--background``
directly, the child's bootloader can reuse the parent's ``_MEI*``
directory, locking it so PyInstaller can't clean up.  The launcher
waits for the foreground to exit (its ``_MEI*`` is then free), then
spawns ``--background`` with ``_MEIPASS2`` stripped so it extracts its
own independent directory.

The launcher imports ONLY the standard library — it holds no files
from the parent's ``_MEI*`` directory.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_FAILED = 0xFFFFFFFF
_POLL_MS = 250


def _wait_for_process_exit(pid: int, timeout: float = 300.0) -> bool:
    """Wait until the process with *pid* has fully exited."""
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        # SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(0x00100000, 0, pid)
        if not handle:
            return True  # process already gone
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                result = kernel32.WaitForSingleObject(handle, _POLL_MS)
                if result == _WAIT_OBJECT_0 or result == _WAIT_FAILED:
                    return True
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(_POLL_MS / 1000.0)
        return False


def _launch_background() -> None:
    """Launch BuzzInstaller.exe --background with an independent env."""
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--background"]
    else:
        main_py = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "main.py")
        )
        args = [sys.executable, main_py, "--background"]

    popen_kwargs: dict = {"close_fds": True}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
        )

    # CRITICAL: strip _MEIPASS2 so the background extracts its own _MEI* dir.
    if getattr(sys, "frozen", False):
        child_env = dict(os.environ)
        child_env.pop("_MEIPASS2", None)
        popen_kwargs["env"] = child_env

    subprocess.Popen(args, **popen_kwargs)


def main() -> int:
    """Launcher entry point: wait for foreground, then start background."""
    if len(sys.argv) < 2:
        print("Usage: background_launcher.py <foreground_pid>", file=sys.stderr)
        return 1
    try:
        foreground_pid = int(sys.argv[1])
    except ValueError:
        print(f"Invalid foreground PID: {sys.argv[1]!r}", file=sys.stderr)
        return 1

    print(f"[launcher] Starting background handoff (foreground PID={foreground_pid})")
    print("[launcher] Waiting for foreground process to exit...")

    if not _wait_for_process_exit(foreground_pid):
        print("[launcher] Timed out waiting for foreground process", file=sys.stderr)
        print("[launcher] Starting independent background process anyway...")
        _launch_background()
        return 0

    print("[launcher] Foreground process exited")
    print("[launcher] Starting independent background process...")
    _launch_background()
    print("[launcher] Background process started")
    return 0


if __name__ == "__main__":
    sys.exit(main())