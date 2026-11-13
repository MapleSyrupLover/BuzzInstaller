"""
Configuration management.

Settings are persisted as JSON in %LOCALAPPDATA%\\BuzzInstaller\\config.json.
A merge-with-defaults strategy ensures forward compatibility when new
options are added.

Concurrency safety
------------------
Multiple BuzzInstaller processes (foreground, background, tray) may
attempt to save the configuration simultaneously.  To prevent corruption
and ``PermissionError`` on ``Path.replace()``:

* A cross-process file lock (``msvcrt.locking``) serialises writers.
* A bounded retry loop handles transient locks (antivirus, indexing,
  another process briefly holding the file).
* The existing valid config is NEVER deleted before the replacement
  succeeds — the temp file is written first, then atomically renamed.
* If saving ultimately fails, the existing config is preserved and the
  error is logged with the actual reason.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
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
    # Resumable / chunked transfers
    "chunked_transfers_enabled": True,
    "upload_chunk_mode": "auto",      # auto | ask | count | custom
    "upload_chunk_count": 4,          # used by mode="count"
    "upload_chunk_size_bytes": 0,     # used by mode="custom" (0 → chunk_size_bytes)
    "chunk_size_bytes": 10 * 1024 * 1024 * 1024,  # 10 GiB default
    "chunk_upload_concurrency": 3,
    "chunk_download_concurrency": 3,
    "chunk_retry_limit": 5,
    "chunk_retry_backoff": 1.0,
    "buzzheavier_account_id": "",
    "buzzheavier_bundle_parent_id": "",
    "keep_bundle_archives": False,
    # Integration
    "start_with_windows": False,
    "minimize_to_tray": False,
    "add_to_path": False,             # add BuzzInstaller dir to user PATH
    # Global hotkey
    "enable_global_hotkey": True,     # Alt+F1 default
    "global_hotkey": "Alt+F1",        # "Modifier+Key" string
    "use_selected_text": True,        # hotkey reads selected text (not clipboard)
    "start_background_at_login": False,  # launch background process at login
    # Clipboard
    "auto_copy_clear_mode": "never",  # "never" | "timeout" | "first_paste"
    "auto_copy_clear_seconds": 10,    # delay used by "timeout"
    # Appearance
    "dark_mode": False,              # False=light, True=dark, None=system
    "ui_style": "discord",           # discord | comfortable | compact
    "accent_color": "",              # empty → palette default, otherwise #RRGGBB
    "ui_radius": 8,                  # corner radius in px
    "ui_scale": 100,                 # font scale percentage
    "close_to_tray": True,           # closing the dashboard hides to tray
    "remember_window_size": True,
    # Updates
    "auto_check_updates": True,      # check GitHub Releases on launch
    "auto_download_updates": False,  # download updates without asking
    "last_update_check": 0,          # UNIX timestamp of the last check
    "skipped_update_version": "",    # "remind me later" version
    # Extra
    "check_for_updates": True,       # legacy alias
    "enable_notifications": True,
    "show_downloads_folder_button": True,
}

# Lock file used to serialise config writes across processes.
_LOCK_FILE_NAME = "config.lock"

# Retry settings for transient file locks.
_MAX_SAVE_RETRIES = 5
_RETRY_DELAY_SECONDS = 0.2


def _get_lock_path():
    """Return the path to the cross-process config lock file."""
    return get_config_path().parent / _LOCK_FILE_NAME


def _acquire_lock(timeout: float = 2.0) -> Any:
    """
    Acquire an exclusive cross-process lock on the config lock file.

    Uses ``msvcrt.locking`` on Windows (byte-range lock on a lock file).
    Returns the open file handle, or None if the lock could not be
    acquired within *timeout* seconds.
    """
    if sys.platform != "win32":
        return None  # No cross-process locking on non-Windows

    lock_path = _get_lock_path()
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Open with read/write so we can lock byte 0
        fh = open(lock_path, "a+b")
        # Lock the first byte exclusively
        import msvcrt
        deadline = time.monotonic() + timeout
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return fh
            except OSError:
                if time.monotonic() >= deadline:
                    fh.close()
                    return None
                time.sleep(0.05)
    except OSError as exc:
        logger.warning("Config lock acquire failed: %s", exc)
        return None


def _release_lock(fh: Any) -> None:
    """Release the config lock."""
    if fh is None:
        return
    try:
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        try:
            fh.close()
        except OSError:
            pass


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


def save_config(config: dict[str, Any]) -> bool:
    """
    Persist configuration to disk atomically.

    Uses a cross-process lock and a bounded retry loop to handle
    transient file locks.  The existing valid config is preserved if
    saving fails.

    Returns True on success, False on failure (existing config preserved).
    """
    ensure_dirs()
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serialise config writes across processes
    lock = _acquire_lock(timeout=2.0)
    try:
        tmp = path.with_suffix(".json.tmp")
        data = json.dumps(config, indent=2, ensure_ascii=False)

        last_error: Exception | None = None
        for attempt in range(_MAX_SAVE_RETRIES):
            try:
                # Write the temp file first
                tmp.write_text(data, encoding="utf-8")

                # Atomically replace the destination
                tmp.replace(path)
                logger.debug("Config saved to %s", path)
                return True

            except PermissionError as exc:
                # Another process may have the file open (e.g. the
                # background process reading it, or antivirus scanning).
                last_error = exc
                logger.warning(
                    "Config save attempt %d/%d failed (PermissionError): %s",
                    attempt + 1, _MAX_SAVE_RETRIES, exc,
                )
                time.sleep(_RETRY_DELAY_SECONDS)

            except OSError as exc:
                last_error = exc
                logger.warning(
                    "Config save attempt %d/%d failed (OSError): %s",
                    attempt + 1, _MAX_SAVE_RETRIES, exc,
                )
                time.sleep(_RETRY_DELAY_SECONDS)

        # All retries exhausted — preserve the existing config
        logger.error(
            "Config save FAILED after %d attempts: %s. "
            "Existing config preserved.",
            _MAX_SAVE_RETRIES, last_error,
        )
        # Clean up the temp file if it was left behind
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    finally:
        _release_lock(lock)


def get(key: str, default: Any = None) -> Any:
    cfg = load_config()
    return cfg.get(key, default)


def set_(key: str, value: Any) -> None:
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)