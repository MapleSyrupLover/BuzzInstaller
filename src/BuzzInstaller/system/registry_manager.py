"""
Registry manager — thin wrapper around the ``winreg`` module.

Provides safe, high-level create/read/delete primitives so the rest of
the application never touches raw registry calls directly.
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path
from typing import Any, Optional

from utils.logging import get_logger

logger = get_logger(__name__)


# Root hives (friendly aliases)
HKCR = winreg.HKEY_CLASSES_ROOT
HKCU = winreg.HKEY_CURRENT_USER
HKLM = winreg.HKEY_LOCAL_MACHINE

# Value types
REG_SZ = winreg.REG_SZ
REG_EXPAND_SZ = winreg.REG_EXPAND_SZ
REG_DWORD = winreg.REG_DWORD
REG_BINARY = winreg.REG_BINARY


class RegistryError(Exception):
    """Raised on registry access errors."""


def _to_winreg_root(root: Any) -> int:
    if isinstance(root, int):
        return root
    raise TypeError(f"Unsupported root: {root!r}")


class RegistryManager:
    """Create, read, and delete registry keys/values."""

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    @staticmethod
    def set_value(root, subkey: str, name: str, value: Any,
                  vtype: int = REG_SZ) -> None:
        """Set *value* in *root\\\\subkey* with the given *name*."""
        hkey = _to_winreg_root(root)
        try:
            with winreg.CreateKeyEx(hkey, subkey, 0, winreg.KEY_WRITE) as k:
                winreg.SetValueEx(k, name, 0, vtype, value)
            logger.debug("Registry set: %s\\%s [%s] = %r", subkey, name, vtype, value)
        except OSError as exc:
            raise RegistryError(f"Cannot set registry value: {exc}") from exc

    @staticmethod
    def create_key(root, subkey: str) -> None:
        """Create or open (and leave open) a key."""
        hkey = _to_winreg_root(root)
        try:
            winreg.CreateKeyEx(hkey, subkey, 0, winreg.KEY_WRITE)
            logger.debug("Registry key created: %s", subkey)
        except OSError as exc:
            raise RegistryError(f"Cannot create key: {exc}") from exc

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    @staticmethod
    def get_value(root, subkey: str, name: str, default: Any = None) -> Any:
        """Return a value, or *default* if it doesn't exist."""
        hkey = _to_winreg_root(root)
        try:
            with winreg.OpenKey(hkey, subkey, 0, winreg.KEY_READ) as k:
                return winreg.QueryValueEx(k, name)[0]
        except FileNotFoundError:
            return default
        except OSError as exc:
            logger.debug("Registry read error (%s\\%s\\%s): %s", root, subkey, name, exc)
            return default

    @staticmethod
    def key_exists(root, subkey: str) -> bool:
        """True if *root\\\\subkey* exists."""
        hkey = _to_winreg_root(root)
        try:
            with winreg.OpenKey(hkey, subkey, 0, winreg.KEY_READ):
                return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    @staticmethod
    def delete_key_tree(root, subkey: str) -> bool:
        """Delete *subkey* and all its subkeys. Returns True if deleted."""
        hkey = _to_winreg_root(root)
        try:
            winreg.DeleteTree(hkey, subkey)
            logger.debug("Registry key deleted: %s", subkey)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning("Registry delete failed (%s): %s", subkey, exc)
            return False

    @staticmethod
    def delete_value(root, subkey: str, name: str) -> bool:
        """Delete a single value. Returns True if deleted."""
        hkey = _to_winreg_root(root)
        try:
            with winreg.OpenKey(hkey, subkey, 0, winreg.KEY_WRITE) as k:
                winreg.DeleteValue(k, name)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning("Registry value delete failed: %s", exc)
            return False
