"""Installation metadata shared by the setup and updater."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "BuzzInstaller"
METADATA_DIR_NAME = "BuzzInstallerInstaller"
METADATA_FILE_NAME = "install_metadata.json"


def get_app_data_dir() -> Path:
    """%LOCALAPPDATA%\\BuzzInstaller — the app's config/data directory."""
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(local) / APP_NAME


def get_default_install_dir() -> Path:
    """Default installation directory (also the default data directory)."""
    return get_app_data_dir()


def get_metadata_path() -> Path:
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(local) / METADATA_DIR_NAME / METADATA_FILE_NAME


def load_metadata() -> dict[str, Any]:
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
    path = get_metadata_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "install_dir": str(install_dir),
        "path_install": bool(path_install),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def clear_metadata() -> None:
    try:
        get_metadata_path().unlink(missing_ok=True)
    except OSError:
        pass


def get_install_dir() -> Path:
    return Path(load_metadata()["install_dir"])


def is_path_install() -> bool:
    return bool(load_metadata().get("path_install", False))
