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
SHORTCUT_DESCRIPTION = "BuzzInstaller - send and receive files with BuzzHeavier"
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


def _read_shortcut_target(shortcut: Path) -> Optional[str]:
    """Return the target path stored in *shortcut*, or None."""
    if not shortcut.is_file():
        return None
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "$ws = New-Object -ComObject WScript.Shell; "
                f"$sc = $ws.CreateShortcut('{_ps_quote(str(shortcut))}'); "
                "Write-Output $sc.TargetPath",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            ),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _notify_shell(shortcut: Path) -> None:
    """Tell Explorer about a new/updated shortcut so it is searchable now."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        SHCNE_CREATE = 0x00000002
        SHCNE_UPDATEDIR = 0x00001000
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_PATHW = 0x0005
        SHCNF_IDLIST = 0x0000

        shell32 = ctypes.windll.shell32
        shell32.SHChangeNotify.argtypes = [
            ctypes.c_long, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p,
        ]
        shell32.SHChangeNotify.restype = None
        shell32.SHChangeNotify(SHCNE_CREATE, SHCNF_PATHW, str(shortcut), None)
        shell32.SHChangeNotify(
            SHCNE_UPDATEDIR, SHCNF_PATHW, str(shortcut.parent), None
        )
        shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass


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
    """Create — or repair — the Start Menu shortcut.

    The shortcut path never changes between releases, so an existing tile
    keeps working after an update.  If the shortcut points at an executable
    that no longer exists (for example after the install directory moved) it
    is recreated so Windows Search never ends up with a dead entry.
    """
    shortcut = start_menu_shortcut_path(app_name)
    exe = Path(exe_path) if exe_path is not None else _installed_exe()
    if shortcut is None or exe is None or not exe.is_file():
        return None

    if shortcut.is_file():
        # Checking the stored target costs a PowerShell start-up, so only do
        # it when the executable is newer than the shortcut (i.e. the app was
        # updated or moved).  In the common case this is a quick stat call.
        try:
            up_to_date = shortcut.stat().st_mtime >= exe.stat().st_mtime
        except OSError:
            up_to_date = False
        if up_to_date:
            return shortcut

        target = _read_shortcut_target(shortcut)
        if target is not None and Path(target) == exe:
            return shortcut
        # Fall through and rewrite the stale or unreadable shortcut.

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
        f"$sc.Description = '{_ps_quote(SHORTCUT_DESCRIPTION)}'; "
        "$sc.Save()"
    )
    if _run_powershell(ps) and shortcut.is_file():
        _notify_shell(shortcut)
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
