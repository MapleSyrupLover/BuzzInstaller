"""
PATH manager — manages the user's Windows PATH environment variable.

Adds/removes the directory containing BuzzInstaller.exe from the
**user** PATH (``HKCU\\Environment\\Path``) — never the system PATH.

Key behaviours
--------------
* Reads/writes ``HKCU\\Environment\\Path`` (no admin required).
* Preserves all existing PATH entries (including ``%VAR%`` expansions).
* Avoids duplicate entries (case-insensitive, normalised comparison).
* Tracks the exact entry BuzzInstaller added via a companion registry
  value (``BuzzInstallerPath``) so removal only deletes what we added.
* Broadcasts ``WM_SETTINGCHANGE`` so newly launched apps see the change.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Optional

from system.registry_manager import (
    RegistryManager, HKCU, REG_EXPAND_SZ, REG_SZ, RegistryError,
)
from config.paths import get_exe_path
from utils.logging import get_logger

logger = get_logger(__name__)

# Registry location for the user PATH
ENV_KEY = r"Environment"
PATH_VALUE = "Path"

# Companion value that records the exact entry BuzzInstaller added.
# This lets us remove ONLY our own entry, never a manually-added one.
BUZZINSTALLER_PATH_MARKER = "BuzzInstallerPath"

# Windows message to broadcast after changing environment variables
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


class PathError(Exception):
    """Raised when a PATH operation fails."""


def _normalise_path(path: str) -> str:
    """Normalise a path for comparison.

    * Strips surrounding quotes.
    * Expands environment variables (``%VAR%``).
    * Resolves to an absolute path.
    * Normalises case (Windows is case-insensitive).
    * Strips trailing slashes (except drive root).
    """
    p = path.strip()
    # Strip surrounding quotes
    if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
        p = p[1:-1].strip()
    # Expand environment variables
    p = os.path.expandvars(p)
    # Resolve to absolute
    try:
        p = os.path.abspath(p)
    except Exception:
        pass
    # Normalise case for comparison
    p = os.path.normcase(p)
    # Strip trailing slashes (but keep drive root like C:\)
    while len(p) > 3 and p.endswith(("\\", "/")):
        p = p[:-1]
    return p


def _get_user_path_raw() -> Optional[str]:
    """Read the raw user PATH value from the registry.

    Returns ``None`` if the value doesn't exist.
    """
    reg = RegistryManager()
    try:
        value = reg.get_value(HKCU, ENV_KEY, PATH_VALUE)
        if value is None:
            return None
        return str(value)
    except Exception as exc:
        raise PathError(f"PATH registry value could not be read: {exc}") from exc


def _set_user_path_raw(value: str) -> None:
    """Write the user PATH value to the registry."""
    reg = RegistryManager()
    try:
        # Use REG_EXPAND_SZ so %VAR% entries are preserved and expanded
        reg.set_value(HKCU, ENV_KEY, PATH_VALUE, value, REG_EXPAND_SZ)
    except RegistryError as exc:
        raise PathError(f"PATH registry value could not be modified: {exc}") from exc


def _get_buzzinstaller_marker() -> Optional[str]:
    """Read the companion marker value that records our added entry."""
    reg = RegistryManager()
    try:
        value = reg.get_value(HKCU, ENV_KEY, BUZZINSTALLER_PATH_MARKER)
        if value is None:
            return None
        return str(value)
    except Exception:
        return None


def _set_buzzinstaller_marker(path: str) -> None:
    """Write the companion marker value."""
    reg = RegistryManager()
    try:
        reg.set_value(HKCU, ENV_KEY, BUZZINSTALLER_PATH_MARKER, path, REG_SZ)
    except RegistryError as exc:
        raise PathError(f"Could not record PATH marker: {exc}") from exc


def _clear_buzzinstaller_marker() -> None:
    """Remove the companion marker value."""
    reg = RegistryManager()
    try:
        reg.delete_value(HKCU, ENV_KEY, BUZZINSTALLER_PATH_MARKER)
    except Exception:
        pass


def _broadcast_environment_change() -> None:
    """Notify Windows that environment variables changed.

    Newly launched applications will see the updated PATH.  Already-open
    terminals retain their old environment — this is expected Windows
    behaviour, not a failure.
    """
    if sys.platform != "win32":
        return
    try:
        result = ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,  # HWND_BROADCAST
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            None,
        )
        logger.debug("WM_SETTINGCHANGE broadcast result: %s", result)
    except Exception as exc:
        logger.warning("WM_SETTINGCHANGE broadcast failed: %s", exc)


def get_buzzinstaller_dir() -> str:
    """Return the absolute directory containing BuzzInstaller.exe.

    Resolves dynamically from the running executable — never hardcoded.
    """
    exe = get_exe_path()
    if not exe:
        raise PathError("Executable location could not be determined")
    return os.path.dirname(os.path.abspath(exe))


def is_in_path() -> bool:
    """True if the BuzzInstaller directory is currently in the user PATH."""
    try:
        raw = _get_user_path_raw()
        if raw is None:
            return False
        target = _normalise_path(get_buzzinstaller_dir())
        for entry in raw.split(";"):
            if not entry.strip():
                continue
            if _normalise_path(entry) == target:
                return True
        return False
    except PathError:
        return False


def add_to_path() -> bool:
    """Add the BuzzInstaller directory to the user PATH.

    Returns ``True`` if the PATH was modified, ``False`` if it was
    already present (no duplicate added).
    """
    try:
        buzz_dir = get_buzzinstaller_dir()
    except PathError as exc:
        raise PathError(str(exc)) from exc

    target = _normalise_path(buzz_dir)

    # Read current PATH
    raw = _get_user_path_raw()
    entries = raw.split(";") if raw else []

    # Check for existing entry (case-insensitive, normalised)
    for entry in entries:
        if not entry.strip():
            continue
        if _normalise_path(entry) == target:
            logger.info("BuzzInstaller already in PATH — no duplicate added")
            # Ensure the marker is set so we can remove it later
            _set_buzzinstaller_marker(buzz_dir)
            return False

    # Append the new entry
    new_entry = buzz_dir
    if raw and not raw.endswith(";"):
        raw += ";"
    new_path = raw + new_entry if raw else new_entry

    _set_user_path_raw(new_path)
    _set_buzzinstaller_marker(buzz_dir)
    _broadcast_environment_change()
    logger.info("Added %s to user PATH", buzz_dir)
    return True


def remove_from_path() -> bool:
    """Remove the BuzzInstaller directory from the user PATH.

    Only removes the entry that BuzzInstaller itself added (tracked via
    the companion marker).  Manually-added identical paths are preserved
    unless they match the marker exactly.

    Returns ``True`` if an entry was removed, ``False`` if nothing to do.
    """
    try:
        buzz_dir = get_buzzinstaller_dir()
    except PathError as exc:
        raise PathError(str(exc)) from exc

    target = _normalise_path(buzz_dir)
    marker = _get_buzzinstaller_marker()

    raw = _get_user_path_raw()
    if raw is None:
        _clear_buzzinstaller_marker()
        return False

    entries = raw.split(";")
    kept: list[str] = []
    removed = False

    for entry in entries:
        if not entry.strip():
            continue
        norm = _normalise_path(entry)
        # Remove if it matches our target AND (it's the marked entry OR
        # the marker is missing but the path is clearly ours).
        if norm == target:
            # Only remove if this is the entry we added (marker matches)
            # or if the marker is missing but the path is the same as
            # our executable directory (safe to remove).
            if marker is not None:
                # Compare marker against this entry
                if _normalise_path(marker) == norm:
                    removed = True
                    continue
                # Marker exists but doesn't match this entry — keep it
                kept.append(entry)
                continue
            else:
                # No marker — only remove if the entry is exactly our
                # executable directory (not a manually-added variant)
                removed = True
                continue
        kept.append(entry)

    if not removed:
        _clear_buzzinstaller_marker()
        return False

    new_path = ";".join(kept)
    _set_user_path_raw(new_path)
    _clear_buzzinstaller_marker()
    _broadcast_environment_change()
    logger.info("Removed %s from user PATH", buzz_dir)
    return True


def get_path_entries() -> list[str]:
    """Return the current user PATH entries as a list."""
    raw = _get_user_path_raw()
    if raw is None:
        return []
    return [e for e in raw.split(";") if e.strip()]