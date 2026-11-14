"""Windows shortcut helpers for BuzzInstaller."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "BuzzInstaller"
_START_MENU_SUBDIR = Path("Microsoft") / "Windows" / "Start Menu" / "Programs"

# The long-standing shortcut location used by previous installers.
SHORTCUT_DIR = Path("C:/BuzzInstaller")
SHORTCUT_PATH = SHORTCUT_DIR / "BuzzInstaller.lnk"


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _ps_quote(path: str) -> str:
    return path.replace("'", "''")


def create_shortcut(exe_path: Path, shortcut_path: Path) -> bool:
    """Create (or update) a Windows ``.lnk`` pointing at *exe_path*."""
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

    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{_ps_quote(str(shortcut_path))}'); "
        f"$sc.TargetPath = '{_ps_quote(str(exe_path))}'; "
        f"$sc.WorkingDirectory = '{_ps_quote(str(exe_path.parent))}'; "
        f"$sc.IconLocation = '{_ps_quote(str(exe_path))},0'; "
        "$sc.Save()"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_creation_flags(),
        )
        return result.returncode == 0 and shortcut_path.is_file()
    except (subprocess.SubprocessError, OSError):
        return False


def delete_shortcut(shortcut_path: Path) -> bool:
    shortcut_path = Path(shortcut_path)
    try:
        if shortcut_path.exists():
            shortcut_path.unlink()
        return True
    except OSError:
        return False


def get_start_menu_dir() -> Optional[Path]:
    if sys.platform != "win32":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / _START_MENU_SUBDIR


def start_menu_shortcut_path(app_name: str = APP_NAME) -> Optional[Path]:
    folder = get_start_menu_dir()
    return folder / f"{app_name}.lnk" if folder is not None else None


def create_start_menu_shortcut(
    exe_path: Path,
    app_name: str = APP_NAME,
) -> Optional[Path]:
    shortcut = start_menu_shortcut_path(app_name)
    if shortcut is None:
        return None
    return shortcut if create_shortcut(exe_path, shortcut) else None


def delete_start_menu_shortcut(app_name: str = APP_NAME) -> bool:
    shortcut = start_menu_shortcut_path(app_name)
    if shortcut is None:
        return True
    return delete_shortcut(shortcut)


def pin_shortcut_to_start(shortcut_path: Path) -> bool:
    """Ask Explorer to pin the shortcut (best effort)."""
    if sys.platform != "win32":
        return False
    shortcut_path = Path(shortcut_path)
    if not shortcut_path.is_file():
        return False

    folder = str(shortcut_path.parent)
    leaf = shortcut_path.name
    script = (
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
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_creation_flags(),
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def create_all_shortcuts(exe_path: Path) -> dict[str, bool]:
    """Create every shortcut BuzzInstaller uses (best effort)."""
    results = {
        "legacy": create_shortcut(exe_path, SHORTCUT_PATH),
        "start_menu": False,
        "pinned": False,
    }
    start_menu = create_start_menu_shortcut(exe_path)
    if start_menu is not None:
        results["start_menu"] = True
        results["pinned"] = pin_shortcut_to_start(start_menu)
    return results


def remove_all_shortcuts() -> None:
    delete_shortcut(SHORTCUT_PATH)
    delete_start_menu_shortcut()
    try:
        if SHORTCUT_DIR.exists() and not any(SHORTCUT_DIR.iterdir()):
            SHORTCUT_DIR.rmdir()
    except OSError:
        pass
