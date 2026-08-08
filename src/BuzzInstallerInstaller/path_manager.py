"""
PATH management for BuzzInstallerInstaller.

Adds/removes the BuzzInstaller installation directory from the **user**
PATH (``HKCU\\Environment\\Path``) — never the system PATH.  No admin
required.  Avoids duplicate entries and tracks what was added so removal
only deletes our own entry.
"""

from __future__ import annotations

import ctypes
import os
import sys
import winreg
from pathlib import Path
from typing import Optional

# Registry location for the user PATH
ENV_KEY = r"Environment"
PATH_VALUE = "Path"

# Companion value that records the exact entry we added
BUZZINSTALLER_PATH_MARKER = "BuzzInstallerPath"

# Windows message to broadcast after changing environment variables
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


class PathError(Exception):
    """Raised when a PATH operation fails."""


def _normalise_path(path: str) -> str:
    """Normalise a path for comparison."""
    p = path.strip()
    if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
        p = p[1:-1].strip()
    p = os.path.expandvars(p)
    try:
        p = os.path.abspath(p)
    except Exception:
        pass
    p = os.path.normcase(p)
    while len(p) > 3 and p.endswith(("\\", "/")):
        p = p[:-1]
    return p


def _get_user_path_raw() -> Optional[str]:
    """Read the raw user PATH value from the registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, PATH_VALUE)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PathError(f"PATH registry value could not be read: {exc}") from exc


def _set_user_path_raw(value: str) -> None:
    """Write the user PATH value to the registry."""
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_WRITE
        ) as k:
            winreg.SetValueEx(k, PATH_VALUE, 0, winreg.REG_EXPAND_SZ, value)
    except OSError as exc:
        raise PathError(f"PATH registry value could not be modified: {exc}") from exc


def _get_marker() -> Optional[str]:
    """Read the companion marker value."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, BUZZINSTALLER_PATH_MARKER)
            return str(value)
    except (FileNotFoundError, OSError):
        return None


def _set_marker(path: str) -> None:
    """Write the companion marker value."""
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_WRITE
        ) as k:
            winreg.SetValueEx(k, BUZZINSTALLER_PATH_MARKER, 0, winreg.REG_SZ, path)
    except OSError as exc:
        raise PathError(f"Could not record PATH marker: {exc}") from exc


def _clear_marker() -> None:
    """Remove the companion marker value."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_WRITE) as k:
            winreg.DeleteValue(k, BUZZINSTALLER_PATH_MARKER)
    except (FileNotFoundError, OSError):
        pass


def _broadcast_environment_change() -> None:
    """Notify Windows that environment variables changed."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,  # HWND_BROADCAST
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
    """True if *directory* is currently in the user PATH."""
    try:
        raw = _get_user_path_raw()
        if raw is None:
            return False
        target = _normalise_path(str(directory))
        for entry in raw.split(";"):
            if not entry.strip():
                continue
            if _normalise_path(entry) == target:
                return True
        return False
    except PathError:
        return False


def add_to_path(directory: Path) -> bool:
    """
    Add *directory* to the user PATH.

    Returns True if the PATH was modified, False if it was already present.
    """
    target = _normalise_path(str(directory))

    raw = _get_user_path_raw()
    entries = raw.split(";") if raw else []

    # Check for existing entry (case-insensitive, normalised)
    for entry in entries:
        if not entry.strip():
            continue
        if _normalise_path(entry) == target:
            # Ensure the marker is set so we can remove it later
            _set_marker(str(directory))
            return False

    # Append the new entry
    new_entry = str(directory)
    if raw and not raw.endswith(";"):
        raw += ";"
    new_path = raw + new_entry if raw else new_entry

    _set_user_path_raw(new_path)
    _set_marker(str(directory))
    _broadcast_environment_change()
    return True


def remove_from_path(directory: Path) -> bool:
    """
    Remove *directory* from the user PATH.

    Only removes the entry that matches the marker (i.e. the one we added).
    Returns True if an entry was removed.
    """
    target = _normalise_path(str(directory))
    marker = _get_marker()

    raw = _get_user_path_raw()
    if raw is None:
        _clear_marker()
        return False

    entries = raw.split(";")
    kept: list[str] = []
    removed = False

    for entry in entries:
        if not entry.strip():
            continue
        norm = _normalise_path(entry)
        if norm == target:
            # Only remove if this is the entry we added (marker matches)
            if marker is not None and _normalise_path(marker) == norm:
                removed = True
                continue
            # If no marker, only remove if the entry is exactly our directory
            if marker is None:
                removed = True
                continue
        kept.append(entry)

    if not removed:
        _clear_marker()
        return False

    new_path = ";".join(kept)
    _set_user_path_raw(new_path)
    _clear_marker()
    _broadcast_environment_change()
    return True