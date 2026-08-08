"""
Tests for the improved installer behavior:

  * Installation detection states
  * Version check states (up-to-date, update available, newer installed)
  * PATH installation
  * PATH duplicate prevention
  * Custom installation directory
  * Installation metadata
  * Delete installation
  * Download cancellation
  * Config save with locked file
  * Concurrent config save attempts

These tests do NOT perform destructive update operations.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure the installer package is importable (FIRST — its version.py must win)
_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

# Also ensure the main BuzzInstaller package is importable for config tests.
# Insert right after the installer package so its version.py doesn't shadow
# ours, but BEFORE the tests directory / CWD.
_MAIN_PKG = os.path.normpath(os.path.join(_PKG, "..", "BuzzInstaller"))
if _MAIN_PKG not in sys.path:
    sys.path.insert(1, _MAIN_PKG)

from installer import (
    Installer, InstallerError, delete_buzzinstaller, get_install_dir,
    get_installed_exe_path, is_installed, get_installed_version,
    install_exe, replace_exe,
)
from metadata import (
    load_metadata, save_metadata, clear_metadata, get_metadata_path,
    get_default_install_dir, get_install_dir as get_meta_install_dir,
)
from github_api import Release, ReleaseAsset, GitHubReleasesClient
from version import parse_version, Version, compare_versions
from downloader import DownloadFailedError, DownloadCancelledError

def _make_release(tag: str = "v1.0.0", with_exe: bool = True) -> Release:
    assets = []
    if with_exe:
        assets.append(
            ReleaseAsset(
                name="BuzzInstaller.exe",
                url=f"https://example.com/{tag}/BuzzInstaller.exe",
                size=1000,
            )
        )
    return Release(
        tag_name=tag,
        html_url=f"https://github.com/MapleSyrupLover/BuzzInstaller/releases/tag/{tag}",
        assets=tuple(assets),
    )


def _make_client(release: Release, error: Exception | None = None):
    client = MagicMock(spec=GitHubReleasesClient)
    if error:
        client.get_latest_release.side_effect = error
    else:
        client.get_latest_release.return_value = release
    return client


# ---------------------------------------------------------------------------
# Version check states
# ---------------------------------------------------------------------------
def test_check_not_installed():
    """check_for_updates returns not_installed when no installation exists."""
    installer = Installer(client=_make_client(_make_release()))
    with patch("installer.is_installed", return_value=False):
        result = installer.check_for_updates()
    assert result["state"] == "not_installed"
    assert "not installed" in result["message"].lower()


def test_check_up_to_date():
    """check_for_updates returns up_to_date when versions match."""
    installer = Installer(client=_make_client(_make_release("v1.0.0")))
    with patch("installer.is_installed", return_value=True), \
         patch("installer.get_installed_version", return_value=parse_version("1.0.0")):
        result = installer.check_for_updates()
    assert result["state"] == "up_to_date"
    assert "up to date" in result["message"].lower()


def test_check_update_available():
    """check_for_updates returns update_available when installed < latest."""
    installer = Installer(client=_make_client(_make_release("v2.0.0")))
    with patch("installer.is_installed", return_value=True), \
         patch("installer.get_installed_version", return_value=parse_version("1.0.0")):
        result = installer.check_for_updates()
    assert result["state"] == "update_available"
    assert result["installed_version"] == parse_version("1.0.0")
    assert result["latest_version"] == parse_version("2.0.0")


def test_check_newer_installed():
    """check_for_updates returns newer_installed when installed > latest."""
    installer = Installer(client=_make_client(_make_release("v1.0.0")))
    with patch("installer.is_installed", return_value=True), \
         patch("installer.get_installed_version", return_value=parse_version("2.0.0")):
        result = installer.check_for_updates()
    assert result["state"] == "newer_installed"
    assert "newer" in result["message"].lower()


def test_check_error_no_release():
    """check_for_updates returns error when no exe asset found."""
    installer = Installer(client=_make_client(_make_release(with_exe=False)))
    result = installer.check_for_updates()
    assert result["state"] == "error"


def test_check_network_error():
    """check_for_updates returns error on network failure."""
    from github_api import NetworkError
    installer = Installer(client=_make_client(None, NetworkError("no internet")))
    result = installer.check_for_updates()
    assert result["state"] == "error"


# ---------------------------------------------------------------------------
# PATH installation
# ---------------------------------------------------------------------------
def test_path_added_on_install():
    """Installer adds directory to PATH when path_install is True."""
    installer = Installer(
        client=_make_client(_make_release("v1.0.0")),
        install_dir=Path("C:/fake/install/dir"),
        path_install=True,
    )
    with patch.object(installer, "client") as mock_client, \
         patch("installer.is_installed", return_value=False), \
         patch.object(installer, "_download", return_value=Path("C:/tmp/downloaded.exe")), \
         patch("installer.install_exe") as mock_install, \
         patch("installer.add_to_path") as mock_add_path, \
         patch.object(installer, "_register_integration"), \
         patch("installer.save_metadata"), \
         patch("installer.launch_buzzinstaller"), \
         patch("installer.get_installed_exe_path", return_value=Path("C:/fake/install/dir/BuzzInstaller.exe")):
        mock_client.get_latest_release.return_value = _make_release("v1.0.0")
        result = installer.run()
    assert result == 0
    mock_add_path.assert_called_once()


def test_path_not_added_on_custom():
    """Installer does NOT add to PATH when path_install is False."""
    installer = Installer(
        client=_make_client(_make_release("v1.0.0")),
        install_dir=Path("C:/custom/dir"),
        path_install=False,
    )
    with patch.object(installer, "client") as mock_client, \
         patch("installer.is_installed", return_value=False), \
         patch.object(installer, "_download", return_value=Path("C:/tmp/downloaded.exe")), \
         patch("installer.install_exe") as mock_install, \
         patch("installer.add_to_path") as mock_add_path, \
         patch.object(installer, "_register_integration"), \
         patch("installer.save_metadata"), \
         patch("installer.launch_buzzinstaller"), \
         patch("installer.get_installed_exe_path", return_value=Path("C:/custom/dir/BuzzInstaller.exe")):
        mock_client.get_latest_release.return_value = _make_release("v1.0.0")
        result = installer.run()
    assert result == 0
    mock_add_path.assert_not_called()


# ---------------------------------------------------------------------------
# Installation metadata
# ---------------------------------------------------------------------------
def test_metadata_save_and_load():
    """save_metadata and load_metadata round-trip correctly."""
    with patch("metadata.get_metadata_path") as mock_path:
        tmp_dir = Path(tempfile.mkdtemp())
        meta_file = tmp_dir / "install_metadata.json"
        mock_path.return_value = meta_file

        save_metadata(Path("C:/test/dir"), path_install=True)
        meta = load_metadata()
        assert meta["install_dir"] == str(Path("C:/test/dir"))
        assert meta["path_install"] is True


def test_metadata_default_when_missing():
    """load_metadata returns defaults when file is missing."""
    with patch("metadata.get_metadata_path") as mock_path:
        mock_path.return_value = Path("C:/nonexistent/install_metadata.json")
        meta = load_metadata()
        assert meta["install_dir"] == str(get_default_install_dir())
        assert meta["path_install"] is False


def test_metadata_corrupt_returns_default():
    """load_metadata returns defaults when file is corrupt."""
    with patch("metadata.get_metadata_path") as mock_path:
        tmp_dir = Path(tempfile.mkdtemp())
        meta_file = tmp_dir / "install_metadata.json"
        meta_file.write_text("not json at all", encoding="utf-8")
        mock_path.return_value = meta_file
        meta = load_metadata()
        assert meta["install_dir"] == str(get_default_install_dir())


# ---------------------------------------------------------------------------
# Delete installation
# ---------------------------------------------------------------------------
def test_delete_buzzinstaller():
    """delete_buzzinstaller removes the exe and clears metadata."""
    tmp_dir = Path(tempfile.mkdtemp())
    exe = tmp_dir / "BuzzInstaller.exe"
    exe.write_bytes(b"MZ fake exe")

    with patch("installer.get_installed_exe_path", return_value=exe), \
         patch("installer.get_install_dir", return_value=tmp_dir), \
         patch("installer.is_process_running", return_value=False), \
         patch("installer.metadata_is_path_install", return_value=False), \
         patch("installer._unregister_integration"), \
         patch("installer.clear_metadata") as mock_clear:
        ok = delete_buzzinstaller()
    assert ok is True
    assert not exe.exists()
    mock_clear.assert_called_once()


def test_delete_not_installed():
    """delete_buzzinstaller handles missing exe gracefully."""
    with patch("installer.get_installed_exe_path", return_value=Path("C:/nonexistent/BuzzInstaller.exe")), \
         patch("installer.is_process_running", return_value=False), \
         patch("installer.metadata_is_path_install", return_value=False), \
         patch("installer.clear_metadata"):
        ok = delete_buzzinstaller()
    assert ok is True


# ---------------------------------------------------------------------------
# Custom installation directory
# ---------------------------------------------------------------------------
def test_custom_install_dir_used():
    """Installer uses the custom install_dir when provided."""
    custom_dir = Path("C:/Users/elial/Apps/BuzzInstaller")
    installer = Installer(
        client=_make_client(_make_release("v1.0.0")),
        install_dir=custom_dir,
        path_install=False,
    )
    assert installer.install_dir == custom_dir


# ---------------------------------------------------------------------------
# Download cancellation
# ---------------------------------------------------------------------------
def test_download_cancelled_raises():
    """DownloadCancelledError propagates and cancels cleanly."""
    installer = Installer(client=_make_client(_make_release("v1.0.0")))
    with patch.object(installer, "client") as mock_client, \
         patch.object(installer, "_download", side_effect=DownloadCancelledError("cancelled")), \
         patch("installer.is_installed", return_value=False), \
         patch("installer.get_installed_version", return_value=None):
        mock_client.get_latest_release.return_value = _make_release("v1.0.0")
        result = installer.run()
    assert result == 1


# ---------------------------------------------------------------------------
# Successful replacement
# ---------------------------------------------------------------------------
def test_successful_replacement():
    """replace_exe copies the new file over the old one."""
    tmp_dir = Path(tempfile.mkdtemp())
    old_exe = tmp_dir / "BuzzInstaller.exe"
    old_exe.write_bytes(b"OLD EXE")
    new_exe = tmp_dir / "new.exe"
    new_exe.write_bytes(b"NEW EXE")

    dest = replace_exe(new_exe, tmp_dir)
    assert dest == tmp_dir / "BuzzInstaller.exe"
    assert dest.read_bytes() == b"NEW EXE"


# ---------------------------------------------------------------------------
# Config save with locked file (mocked)
# ---------------------------------------------------------------------------
def test_config_save_retries_on_permission_error():
    """save_config retries when replace fails with PermissionError."""
    import config.config_manager as cm
    tmp_dir = Path(tempfile.mkdtemp())
    config_file = tmp_dir / "config.json"

    with patch("config.config_manager._acquire_lock", return_value=None), \
         patch("config.config_manager.get_config_path", return_value=config_file), \
         patch.object(Path, "replace", side_effect=[
             PermissionError(5, "Access is denied", "config.json.tmp", "config.json"),
             None,  # second attempt succeeds
         ]), \
         patch("time.sleep") as mock_sleep:
        ok = cm.save_config({"test": True})
    assert ok is True
    assert mock_sleep.call_count >= 1


def test_config_save_preserves_on_failure():
    """save_config returns False and preserves existing config on failure."""
    import config.config_manager as cm
    from config.paths import get_config_path

    tmp_dir = Path(tempfile.mkdtemp())
    config_file = tmp_dir / "config.json"
    config_file.write_text('{"existing": "value"}', encoding="utf-8")

    with patch("config.config_manager._acquire_lock", return_value=None), \
         patch("config.config_manager.get_config_path", return_value=config_file), \
         patch.object(Path, "replace", side_effect=PermissionError(5, "Access is denied")), \
         patch("time.sleep"):
        ok = cm.save_config({"new": "value"})

    assert ok is False
    # Existing config preserved
    data = json.loads(config_file.read_text(encoding="utf-8"))
    assert data == {"existing": "value"}


def test_config_save_concurrent():
    """Concurrent config saves do not corrupt the file."""
    import config.config_manager as cm
    from config.paths import get_config_path

    tmp_dir = Path(tempfile.mkdtemp())
    config_file = tmp_dir / "config.json"
    lock_file = tmp_dir / "config.lock"

    with patch("config.config_manager.get_config_path", return_value=config_file), \
         patch("config.config_manager._get_lock_path", return_value=lock_file):

        results = []

        def save_worker(kv: dict):
            ok = cm.save_config(kv)
            results.append(ok)

        threads = []
        for i in range(5):
            t = threading.Thread(target=save_worker, args=({"worker": i, "value": i},))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # All saves should succeed (or at least not corrupt)
        assert all(results), f"Some saves failed: {results}"
        # File should be valid JSON
        data = json.loads(config_file.read_text(encoding="utf-8"))
        assert "worker" in data


if __name__ == "__main__":
    tests = [
        test_check_not_installed,
        test_check_up_to_date,
        test_check_update_available,
        test_check_newer_installed,
        test_check_error_no_release,
        test_check_network_error,
        test_path_added_on_install,
        test_path_not_added_on_custom,
        test_metadata_save_and_load,
        test_metadata_default_when_missing,
        test_metadata_corrupt_returns_default,
        test_delete_buzzinstaller,
        test_delete_not_installed,
        test_custom_install_dir_used,
        test_download_cancelled_raises,
        test_successful_replacement,
        test_config_save_retries_on_permission_error,
        test_config_save_preserves_on_failure,
        test_config_save_concurrent,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL: {t.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR: {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)