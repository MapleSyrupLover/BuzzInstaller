"""
Path management for BuzzInstaller.

All user-specific data (config, logs, transient files) lives under
%LOCALAPPDATA%\\BuzzInstaller so the application is portable and
uninstallable by simply deleting that folder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "BuzzInstaller"
APP_AUTHOR = "BuzzInstaller"
COMPANY_KEY = "BuzzInstaller"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def get_app_dir() -> Path:
    """
    Root directory for all application data.

    When frozen → %LOCALAPPDATA%\\BuzzInstaller
    When running from source → %LOCALAPPDATA%\\BuzzInstaller
    """
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(local) / APP_AUTHOR


def get_config_path() -> Path:
    return get_app_dir() / "config.json"


def get_log_path() -> Path:
    return get_app_dir() / "logs" / "buzzinstaller.log"


def get_cache_dir() -> Path:
    return get_app_dir() / "cache"


def get_exe_path() -> str:
    """Absolute path to the running executable (or python.exe)."""
    return sys.executable if is_frozen() else sys.executable


def get_default_download_dir() -> Path:
    """User's Downloads folder."""
    # %USERPROFILE%\\Downloads is more reliable than the Known Folder on older setups
    user_profile = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(user_profile) / "Downloads"


def ensure_dirs() -> None:
    """Create all required directories."""
    for p in (get_app_dir(), get_cache_dir(), get_log_path().parent):
        p.mkdir(parents=True, exist_ok=True)
