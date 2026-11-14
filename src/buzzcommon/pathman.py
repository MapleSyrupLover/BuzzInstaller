"""Per-user PATH management (never touches the system PATH)."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    import winreg
else:  # pragma: no cover - development on non-Windows
    winreg = None  # type: ignore[assignment]

ENV_KEY = r"Environment"
PATH_VALUE = "Path"
BUZZINSTALLER_PATH_MARKER = "BuzzInstallerPath"

WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


class PathError(Exception):
    """Raised when a PATH operation fails."""


def _normalise_path(path: str) -> str:
    value = path.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1].strip()
    value = os.path.expandvars(value)
    try:
        value = os.path.abspath(value)
    except Exception:
        pass
    value = os.path.normcase(value)
    while len(value) > 3 and value.endswith(("\\", "/")):
        value = value[:-1]
    return value


def _get_user_path_raw() -> Optional[str]:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, PATH_VALUE)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PathError(f"PATH registry value could not be read: {exc}") from exc


def _set_user_path_raw(value: str) -> None:
    if winreg is None:
        raise PathError("PATH editing is only supported on Windows.")
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_WRITE
        ) as key:
            winreg.SetValueEx(key, PATH_VALUE, 0, winreg.REG_EXPAND_SZ, value)
    except OSError as exc:
        raise PathError(f"PATH registry value could not be modified: {exc}") from exc


def _get_marker() -> Optional[str]:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, BUZZINSTALLER_PATH_MARKER)
            return str(value)
    except (FileNotFoundError, OSError):
        return None


def _set_marker(path: str) -> None:
    if winreg is None:
        return
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_WRITE
        ) as key:
            winreg.SetValueEx(key, BUZZINSTALLER_PATH_MARKER, 0, winreg.REG_SZ, path)
    except OSError as exc:
        raise PathError(f"Could not record PATH marker: {exc}") from exc


def _clear_marker() -> None:
    if winreg is None:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_WRITE) as key:
            winreg.DeleteValue(key, BUZZINSTALLER_PATH_MARKER)
    except (FileNotFoundError, OSError):
        pass


def _broadcast_environment_change() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            None,
        )
    except Exception:
        pass


def is_in_path(directory: Path) -> bool:
    try:
        raw = _get_user_path_raw()
        if raw is None:
            return False
        target = _normalise_path(str(directory))
        for entry in raw.split(";"):
            if entry.strip() and _normalise_path(entry) == target:
                return True
        return False
    except PathError:
        return False


def add_to_path(directory: Path) -> bool:
    """Add *directory* to the user PATH. Returns True if it was added."""
    target = _normalise_path(str(directory))
    raw = _get_user_path_raw()
    entries = raw.split(";") if raw else []

    for entry in entries:
        if entry.strip() and _normalise_path(entry) == target:
            _set_marker(str(directory))
            return False

    new_entry = str(directory)
    if raw and not raw.endswith(";"):
        raw += ";"
    new_path = raw + new_entry if raw else new_entry
    _set_user_path_raw(new_path)
    _set_marker(str(directory))
    _broadcast_environment_change()
    return True


def remove_from_path(directory: Path) -> bool:
    """Remove *directory* from the user PATH. Returns True if it was removed."""
    target = _normalise_path(str(directory))
    marker = _get_marker()
    raw = _get_user_path_raw()
    if raw is None:
        _clear_marker()
        return False

    kept: list[str] = []
    removed = False
    for entry in raw.split(";"):
        if not entry.strip():
            continue
        norm = _normalise_path(entry)
        if norm == target:
            if marker is not None and _normalise_path(marker) == norm:
                removed = True
                continue
            if marker is None:
                removed = True
                continue
        kept.append(entry)

    if not removed:
        _clear_marker()
        return False

    _set_user_path_raw(";".join(kept))
    _clear_marker()
    _broadcast_environment_change()
    return True
