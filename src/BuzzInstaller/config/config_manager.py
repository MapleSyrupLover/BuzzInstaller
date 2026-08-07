"""
Configuration management.

Settings are persisted as JSON in %LOCALAPPDATA%\\BuzzInstaller\\config.json.
A merge-with-defaults strategy ensures forward compatibility when new
options are added.
"""

from __future__ import annotations

import json
from typing import Any

from config.paths import get_config_path, ensure_dirs
from utils.logging import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Default configuration — edit here when adding new options.
# ---------------------------------------------------------------------------
DEFAULTS: dict[str, Any] = {
    # Download
    "download_folder": "",            # empty → user's Downloads folder
    "auto_extract_zip": True,
    "delete_zip_after_extract": False,
    "launch_folder_after_download": True,
    # Integration
    "start_with_windows": False,
    "minimize_to_tray": False,
    # Global hotkey
    "enable_global_hotkey": True,     # Alt+F1 default
    "global_hotkey": "Alt+F1",        # "Modifier+Key" string
    "hotkey_mode": "clipboard",       # "clipboard" | "selected_text"
    "start_background_at_login": False,  # launch background process at login
    # Appearance
    "dark_mode": False,              # False=light, True=dark, None=system
    # Extra
    "check_for_updates": True,
    "enable_notifications": True,
    "show_downloads_folder_button": True,
}


def load_config() -> dict[str, Any]:
    """Load configuration, merged with defaults for any missing keys."""
    ensure_dirs()
    path = get_config_path()
    if not path.exists():
        save_config(dict(DEFAULTS))
        return dict(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Config read failed (%s); using defaults", exc)
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    merged.update(data)
    return merged


def save_config(config: dict[str, Any]) -> None:
    """Persist configuration to disk."""
    ensure_dirs()
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    logger.debug("Config saved to %s", path)


def get(key: str, default: Any = None) -> Any:
    cfg = load_config()
    return cfg.get(key, default)


def set_(key: str, value: Any) -> None:
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)
