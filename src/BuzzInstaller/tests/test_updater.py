"""Tests for the in-app updater (no network access needed)."""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from core import updater


def _release(tag: str, assets=None, draft=False, prerelease=False) -> dict:
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "body": "Release notes",
        "draft": draft,
        "prerelease": prerelease,
        "published_at": "2026-01-01T00:00:00Z",
        "assets": assets or [],
    }


def _asset(name: str, size: int = 1234) -> dict:
    return {
        "name": name,
        "size": size,
        "browser_download_url": f"https://example.test/{name}",
    }


def test_is_newer_semantic():
    assert updater.is_newer("1.10.0", "1.9.0")
    assert updater.is_newer("v2.0.0", "1.99.99")
    assert not updater.is_newer("1.2.2", "1.2.2")
    assert not updater.is_newer("1.2.1", "1.2.2")


def test_is_newer_defaults_to_running_version():
    with patch.object(updater, "APP_VERSION", "0.0.9"):
        assert updater.is_newer("1.0.0")
        assert not updater.is_newer("0.0.8")


def test_check_returns_update_info():
    payload = _release("v9.9.9", [_asset("BuzzInstaller.zip", 2048)])
    with patch.object(updater, "_request_json", return_value=payload):
        info = updater.check_for_update()
    assert info is not None
    assert info.version == "9.9.9"
    assert info.asset_name == "BuzzInstaller.zip"
    assert info.asset_size == 2048
    assert info.notes == "Release notes"


def test_check_up_to_date_returns_none():
    payload = _release("v0.0.1", [_asset("BuzzInstaller.zip")])
    with patch.object(updater, "_request_json", return_value=payload):
        assert updater.check_for_update() is None


def test_check_ignores_draft_and_prerelease():
    payload = _release("v99.0.0", [_asset("BuzzInstaller.zip")], draft=True)
    with patch.object(updater, "_request_json", return_value=payload):
        assert updater.check_for_update() is None
    payload = _release("v99.0.0", [_asset("BuzzInstaller.zip")], prerelease=True)
    with patch.object(updater, "_request_json", return_value=payload):
        assert updater.check_for_update() is None


def test_check_prefers_bundle_over_legacy_exe():
    payload = _release(
        "v99.0.0",
        [_asset("BuzzInstaller.exe", 10), _asset("BuzzInstaller.zip", 20)],
    )
    with patch.object(updater, "_request_json", return_value=payload):
        info = updater.check_for_update()
    assert info is not None
    assert info.asset_name == "BuzzInstaller.zip"


def test_check_missing_asset_raises():
    payload = _release("v99.0.0", [_asset("SomethingElse.exe")])
    with patch.object(updater, "_request_json", return_value=payload):
        try:
            updater.check_for_update()
        except updater.UpdateCheckError:
            pass
        else:
            raise AssertionError("expected UpdateCheckError")


def test_validate_download_accepts_bundle():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "update.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("BuzzInstaller.exe", b"MZ")
            archive.writestr("_internal/runtime.dll", b"DLL")
        updater._validate_download(path)


def test_validate_download_rejects_missing_exe():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "update.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("_internal/runtime.dll", b"DLL")
        try:
            updater._validate_download(path)
        except updater.UpdateDownloadError:
            pass
        else:
            raise AssertionError("expected UpdateDownloadError")


def test_update_script_preserves_user_data():
    script = updater._PS_SCRIPT
    for name in ("config.json", "history.json", "logs", "cache", "updates"):
        assert name in script, f"{name} is not preserved by the update helper"


def test_apply_update_stages_bundle_outside_install_dir():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_dir = root / "install"
        install_dir.mkdir()
        (install_dir / "BuzzInstaller.exe").write_bytes(b"MZ")
        app_dir = root / "appdata"
        bundle = app_dir / "updates" / "BuzzInstaller-9.9.9.zip"
        bundle.parent.mkdir(parents=True)
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("BuzzInstaller.exe", b"MZ")

        with patch.object(updater, "can_self_update", return_value=True), \
             patch.object(updater, "get_install_dir", return_value=install_dir), \
             patch.object(updater, "get_app_dir", return_value=app_dir), \
             patch.object(updater.subprocess, "Popen") as popen:
            updater.apply_update(bundle)
            command = popen.call_args[0][0]
            staged = Path(command[command.index("-ZipPath") + 1])
            assert staged.is_file()
            assert install_dir not in staged.parents
            assert staged != bundle


def test_can_self_update_false_from_source():
    assert not updater.is_frozen()
    assert not updater.can_self_update()


TESTS = [
    test_is_newer_semantic,
    test_is_newer_defaults_to_running_version,
    test_check_returns_update_info,
    test_check_up_to_date_returns_none,
    test_check_ignores_draft_and_prerelease,
    test_check_prefers_bundle_over_legacy_exe,
    test_check_missing_asset_raises,
    test_validate_download_accepts_bundle,
    test_validate_download_rejects_missing_exe,
    test_update_script_preserves_user_data,
    test_apply_update_stages_bundle_outside_install_dir,
    test_can_self_update_false_from_source,
]


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL: {test.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR: {test.__name__}: {exc}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
