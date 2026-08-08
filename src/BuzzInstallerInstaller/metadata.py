"""
Installation metadata for BuzzInstallerInstaller.

Stores minimal per-user metadata about where BuzzInstaller is installed
and whether PATH installation was selected.  The installer can recover
even if this metadata is missing (it falls back to the default install
directory).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

APP_NAME = "BuzzInstaller"
METADATA_DIR_NAME = "BuzzInstallerInstaller"
METADATA_FILE_NAME = "install_metadata.json"

# Default installation directory: %LOCALAPPDATA%\BuzzInstaller
def get_default_install_dir() -> Path:
    """Return the default per-user installation directory."""
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(local) / APP_NAME


def get_metadata_path() -> Path:
    """Return the path to the installer metadata file."""
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(local) / METADATA_DIR_NAME / METADATA_FILE_NAME


def load_metadata() -> dict[str, Any]:
    """
    Load installation metadata.

    Returns a dict with keys:
        install_dir: str  — absolute path to the installation directory
        path_install: bool — whether PATH installation was selected

    Returns defaults if the metadata file is missing or corrupt.
    """
    default = {
        "install_dir": str(get_default_install_dir()),
        "path_install": False,
    }
    path = get_metadata_path()
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default
        # Validate install_dir
        install_dir = data.get("install_dir")
        if not install_dir or not isinstance(install_dir, str):
            return default
        return {
            "install_dir": install_dir,
            "path_install": bool(data.get("path_install", False)),
        }
    except (json.JSONDecodeError, OSError):
        return default


def save_metadata(install_dir: Path, path_install: bool = False) -> None:
    """Save installation metadata."""
    path = get_metadata_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "install_dir": str(install_dir),
        "path_install": bool(path_install),
    }
    # Atomic write: write to temp then replace
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def clear_metadata() -> None:
    """Remove installation metadata (e.g. after deletion)."""
    path = get_metadata_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def get_install_dir() -> Path:
    """Return the installation directory from metadata, or the default."""
    meta = load_metadata()
    return Path(meta["install_dir"])


def is_path_install() -> bool:
    """Return whether PATH installation was selected."""
    meta = load_metadata()
    return bool(meta.get("path_install", False))