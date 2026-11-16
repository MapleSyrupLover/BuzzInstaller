"""Windows shortcut helpers for BuzzInstaller."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "BuzzInstaller"

#: Description stored inside the shortcut.  Windows Search also indexes this
#: text, so the app is still found when the user types a related word.
SHORTCUT_DESCRIPTION = "BuzzInstaller - send and receive files with BuzzHeavier"

_START_MENU_SUBDIR = Path("Microsoft") / "Windows" / "Start Menu" / "Programs"

# The long-standing shortcut location used by previous installers.
SHORTCUT_DIR = Path("C:/BuzzInstaller")
SHORTCUT_PATH = SHORTCUT_DIR / "BuzzInstaller.lnk"

#: Per-user ``App Paths`` entry.  Registering ``BuzzInstaller.exe`` here is
#: what makes ``BuzzInstaller`` resolve from the Run dialog and from Windows
#: Search without the user having to hunt for the executable.
APP_PATHS_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\App Paths\BuzzInstaller.exe"
)


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _ps_quote(path: str) -> str:
    return path.replace("'", "''")


def notify_shell(*paths: Path) -> None:
    """Ask Explorer to refresh its view of the given shortcut paths.

    Without this the new Start Menu entry can take a while to show up in
    Search/Start until Explorer next rescans the folder.
    """
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

        seen_dirs: set[str] = set()
        for path in paths:
            path = Path(path)
            shell32.SHChangeNotify(
                SHCNE_CREATE, SHCNF_PATHW, str(path), None
            )
            parent = str(path.parent)
            if parent not in seen_dirs:
                seen_dirs.add(parent)
                shell32.SHChangeNotify(
                    SHCNE_UPDATEDIR, SHCNF_PATHW, parent, None
                )
        shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        # Shell notifications are a nicety; never fail an install over them.
        pass


def register_app_path(exe_path: Path) -> bool:
    """Register *exe_path* under ``HKCU\\...\\App Paths``.

    Once registered, typing ``BuzzInstaller`` in the Windows Run dialog (or
    the Start menu search box) resolves straight to the executable.
    """
    if sys.platform != "win32":
        return False
    exe_path = Path(exe_path)
    if not exe_path.is_file():
        return False
    try:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, APP_PATHS_KEY, 0, winreg.KEY_WRITE
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(exe_path))
            winreg.SetValueEx(
                key, "Path", 0, winreg.REG_SZ, str(exe_path.parent)
            )
        return True
    except OSError:
        return False


def remove_app_path() -> bool:
    """Remove the per-user ``App Paths`` entry (best effort)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, APP_PATHS_KEY)
        return True
    except (FileNotFoundError, OSError):
        return False


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
        f"$sc.Description = '{_ps_quote(SHORTCUT_DESCRIPTION)}'; "
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
    if not create_shortcut(exe_path, shortcut):
        return None
    # Make Explorer pick the entry up immediately so it is searchable
    # without waiting for the next folder rescan.
    notify_shell(shortcut)
    return shortcut


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
    exe_path = Path(exe_path)
    results = {
        "legacy": create_shortcut(exe_path, SHORTCUT_PATH),
        "start_menu": False,
        "pinned": False,
        "app_paths": register_app_path(exe_path),
    }
    start_menu = create_start_menu_shortcut(exe_path)
    if start_menu is not None:
        results["start_menu"] = True
        results["pinned"] = pin_shortcut_to_start(start_menu)
    if results["legacy"]:
        notify_shell(SHORTCUT_PATH)
    return results


def remove_all_shortcuts() -> None:
    delete_shortcut(SHORTCUT_PATH)
    delete_start_menu_shortcut()
    remove_app_path()
    try:
        if SHORTCUT_DIR.exists() and not any(SHORTCUT_DIR.iterdir()):
            SHORTCUT_DIR.rmdir()
    except OSError:
        pass
