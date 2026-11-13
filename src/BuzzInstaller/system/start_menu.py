"""Per-user Windows Start Menu integration for the installed app.

The Start Menu shortcut lives outside the installation folder, so the
updater can replace the app without removing the user's pinned tile.  Pinning
itself is requested from Explorer on a best-effort basis — Windows may ignore
the request depending on shell version or policy, but the shortcut always
stays visible in the Start menu app list and search.
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


def start_menu_dir() -> Optional[Path]:
    """Return the current user's Start Menu ``Programs`` folder."""
    if sys.platform != "win32":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / _START_MENU_SUBDIR


def start_menu_shortcut_path(app_name: str = APP_NAME) -> Optional[Path]:
    """Return the Start Menu shortcut path for *app_name*."""
    folder = start_menu_dir()
    return folder / f"{app_name}.lnk" if folder is not None else None


def _run_powershell(script: str) -> bool:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            ),
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _installed_exe() -> Optional[Path]:
    """Only installed builds get a Start Menu entry (not source runs)."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable)
    return exe if exe.is_file() else None


def ensure_start_menu_shortcut(
    exe_path: Optional[Path] = None,
    app_name: str = APP_NAME,
) -> Optional[Path]:
    """Create the Start Menu shortcut if it is missing.

    The shortcut path never changes between releases, so an existing tile
    keeps working after an update.  Returns the shortcut path when present.
    """
    shortcut = start_menu_shortcut_path(app_name)
    exe = Path(exe_path) if exe_path is not None else _installed_exe()
    if shortcut is None or exe is None or not exe.is_file():
        return None
    if shortcut.is_file():
        return shortcut

    try:
        shortcut.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{_ps_quote(str(shortcut))}'); "
        f"$sc.TargetPath = '{_ps_quote(str(exe))}'; "
        f"$sc.WorkingDirectory = '{_ps_quote(str(exe.parent))}'; "
        f"$sc.IconLocation = '{_ps_quote(str(exe))},0'; "
        "$sc.Save()"
    )
    if _run_powershell(ps) and shortcut.is_file():
        return shortcut
    return None


def pin_to_start(shortcut_path: Optional[Path] = None) -> bool:
    """Ask Explorer to pin the Start Menu shortcut (best effort)."""
    shortcut = (
        Path(shortcut_path)
        if shortcut_path is not None
        else start_menu_shortcut_path()
    )
    if shortcut is None or not shortcut.is_file():
        return False

    folder = str(shortcut.parent)
    leaf = shortcut.name
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
    return _run_powershell(ps)
