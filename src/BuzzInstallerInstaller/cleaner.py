"""Deep-clean BuzzInstaller from the system."""
from __future__ import annotations
import os, shutil, subprocess, sys, winreg
from pathlib import Path

HKCU = winreg.HKEY_CURRENT_USER
PROTOCOL_KEY = r"Software\Classes\BZ"
CTX_FILES = r"Software\Classes\*\shell\BuzzInstallerSend"
CTX_DIRS = r"Software\Classes\Directory\shell\BuzzInstallerSend"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUES = ("BuzzInstaller", "BuzzInstallerBackground")
LOCAL = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
APP_DATA = Path(LOCAL) / "BuzzInstaller"
INSTALLER_DATA = Path(LOCAL) / "BuzzInstallerInstaller"
SHORTCUT_DIR = Path("C:/BuzzInstaller")


def _del_tree(root, subkey):
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as k:
            while True:
                try:
                    child = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _del_tree(root, f"{subkey}\\{child}")
        try:
            winreg.DeleteKey(root, subkey)
            return True
        except (FileNotFoundError, OSError):
            return False
    except (FileNotFoundError, OSError):
        return False


def _del_value(root, subkey, name):
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_WRITE) as k:
            winreg.DeleteValue(k, name)
            return True
    except (FileNotFoundError, OSError):
        return False


def _kill_processes():
    if sys.platform != "win32":
        return
    try:
        subprocess.run(["taskkill", "/F", "/IM", "BuzzInstaller.exe"],
                       capture_output=True, timeout=10,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except (subprocess.SubprocessError, OSError):
        pass


def _remove_path():
    try:
        with winreg.OpenKey(HKCU, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            try:
                val, _ = winreg.QueryValueEx(k, "Path")
            except FileNotFoundError:
                return
            entries = [e for e in val.split(";") if e and "BuzzInstaller" not in e]
            new = ";".join(entries)
            if new != val:
                winreg.SetValueEx(k, "Path", 0, winreg.REG_EXPAND_SZ, new)
    except OSError:
        pass


def clean_buzzinstaller() -> dict:
    """Remove ALL BuzzInstaller traces. Returns per-step results."""
    r = {}
    _kill_processes()
    r["processes"] = True
    r["registry_protocol"] = _del_tree(HKCU, PROTOCOL_KEY)
    r["registry_context_menu"] = _del_tree(HKCU, CTX_FILES) or _del_tree(HKCU, CTX_DIRS)
    r["registry_run"] = any(_del_value(HKCU, RUN_KEY, n) for n in RUN_VALUES)
    for d in (APP_DATA, INSTALLER_DATA, SHORTCUT_DIR):
        try:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
    r["app_data"] = not APP_DATA.exists()
    r["installer_data"] = not INSTALLER_DATA.exists()
    r["shortcut_dir"] = not SHORTCUT_DIR.exists()
    _remove_path()
    r["path"] = True
    return r