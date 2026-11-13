"""
Windows shortcut (.lnk) creation for BuzzInstaller.

Uses PowerShell's WScript.Shell COM object (a reliable Windows-native
mechanism) to create real binary ``.lnk`` shortcuts — not text files.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "BuzzInstaller"
_START_MENU_SUBDIR = Path("Microsoft") / "Windows" / "Start Menu" / "Programs"


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


def get_start_menu_dir() -> Optional[Path]:
    """Return the current user's Start Menu ``Programs`` folder."""
    if sys.platform != "win32":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / _START_MENU_SUBDIR


def start_menu_shortcut_path(app_name: str = APP_NAME) -> Optional[Path]:
    """Return the Start Menu shortcut path for *app_name*."""
    folder = get_start_menu_dir()
    return folder / f"{app_name}.lnk" if folder is not None else None


def create_start_menu_shortcut(
    exe_path: Path,
    app_name: str = APP_NAME,
) -> Optional[Path]:
    """Create/update the per-user Start Menu shortcut and return its path."""
    shortcut = start_menu_shortcut_path(app_name)
    if shortcut is None:
        return None
    return shortcut if create_shortcut(exe_path, shortcut) else None


def delete_start_menu_shortcut(app_name: str = APP_NAME) -> bool:
    """Remove the per-user Start Menu shortcut (best effort)."""
    shortcut = start_menu_shortcut_path(app_name)
    if shortcut is None:
        return True
    return delete_shortcut(shortcut)


def pin_shortcut_to_start(shortcut_path: Path) -> bool:
    """Ask Explorer to pin the Start Menu shortcut (best effort).

    Windows may ignore the request depending on shell version or policy; the
    shortcut is still listed in the Start menu app list either way.
    """
    if sys.platform != "win32":
        return False
    shortcut_path = Path(shortcut_path)
    if not shortcut_path.is_file():
        return False

    folder = str(shortcut_path.parent)
    leaf = shortcut_path.name
    ps = (
        "$shell = New-Object -ComObject Shell.Application; "
        f"$folder = $shell.Namespace('{_ps_quote(folder)}'); "
        "if (-not $folder) { exit 1 }; "
        f"$item = $folder.ParseName('{_ps_quote(leaf)}'); "
        "if (-not $item) { exit 1 }; "
        "$verbs = @($item.Verbs()); "
        "$verb = $verbs | Where-Object { "
        "$n = ($_.Name -replace '&',''); "
        "$n -match '(?i)^(pin to start|an start anheften|.pingler.*d.marrer|fijar.*inicio|blocca.*avvio|aggiungi.*start|vastmaken.*start|aan start.*|przypnij.*start)$' } | "
        "Select-Object -First 1; "
        "if (-not $verb) { exit 2 }; "
        "$verb.DoIt(); Start-Sleep -Milliseconds 250; exit 0"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False