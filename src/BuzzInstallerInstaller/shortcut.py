"""
Windows shortcut (.lnk) creation for BuzzInstaller.

Uses PowerShell's WScript.Shell COM object (a reliable Windows-native
mechanism) to create real binary ``.lnk`` shortcuts — not text files.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional


def _ps_quote(path: str) -> str:
    """Escape a path for embedding in a PowerShell single-quoted string."""
    return path.replace("'", "''")


def create_shortcut(exe_path: Path, shortcut_path: Path) -> bool:
    """
    Create (or update) a Windows shortcut at *shortcut_path* pointing to
    *exe_path*.

    Working directory is set to the exe's folder; icon is taken from the
    exe itself.  Returns True on success, False on failure (e.g. permission
    denied).
    """
    if sys.platform != "win32":
        return False
    exe_path = Path(exe_path)
    shortcut_path = Path(shortcut_path)
    if not exe_path.is_file():
        return False

    try:
        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{_ps_quote(str(shortcut_path))}'); "
        f"$sc.TargetPath = '{_ps_quote(str(exe_path))}'; "
        f"$sc.WorkingDirectory = '{_ps_quote(str(exe_path.parent))}'; "
        f"$sc.IconLocation = '{_ps_quote(str(exe_path))},0'; "
        "$sc.Save()"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return result.returncode == 0 and shortcut_path.is_file()
    except (subprocess.SubprocessError, OSError):
        return False


def delete_shortcut(shortcut_path: Path) -> bool:
    """Delete a Windows shortcut. Returns True if removed (or absent)."""
    shortcut_path = Path(shortcut_path)
    try:
        if shortcut_path.exists():
            shortcut_path.unlink()
        return True
    except OSError:
        return False


def get_shortcut_target(shortcut_path: Path) -> Optional[str]:
    """Read the target path of an existing shortcut, or None."""
    if sys.platform != "win32":
        return None
    shortcut_path = Path(shortcut_path)
    if not shortcut_path.is_file():
        return None

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{_ps_quote(str(shortcut_path))}'); "
        "Write-Output $sc.TargetPath"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            return None
        target = result.stdout.strip()
        return target or None
    except (subprocess.SubprocessError, OSError):
        return None