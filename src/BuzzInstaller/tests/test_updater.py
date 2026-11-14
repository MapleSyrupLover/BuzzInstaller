"""Tests for the in-app updater (no network access needed)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from core import updater
from buzzcommon.release import Release, ReleaseAsset


def _release(tag: str, assets=None, notes: str = "Release notes") -> Release:
    return Release(
        tag_name=tag,
        html_url=f"https://github.com/x/y/releases/tag/{tag}",
        notes=notes,
        assets=tuple(assets or ()),
    )


def _asset(name: str, size: int = 1234) -> ReleaseAsset:
    return ReleaseAsset(
        name=name,
        url=f"https://example.test/{name}",
        size=size,
    )


def _client(release: Release):
    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_latest_release(self):
            return release

    return _FakeClient


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
    release = _release(
        "v9.9.9",
        [
            _asset("BuzzInstaller.zip", 2048),
            _asset("BuzzInstallerUpdater.exe", 512),
        ],
    )
    with patch.object(updater, "GitHubReleasesClient", _client(release)):
        info = updater.check_for_update()
    assert info is not None
    assert info.version == "9.9.9"
    assert info.tag == "v9.9.9"
    assert info.asset_name == "BuzzInstaller.zip"
    assert info.asset_size == 2048
    assert info.notes == "Release notes"
    assert info.size_str


def test_check_up_to_date_returns_none():
    release = _release("v0.0.1", [_asset("BuzzInstaller.zip")])
    with patch.object(updater, "GitHubReleasesClient", _client(release)):
        assert updater.check_for_update() is None


def test_check_missing_asset_raises():
    release = _release("v99.0.0", [_asset("SomethingElse.exe")])
    with patch.object(updater, "GitHubReleasesClient", _client(release)):
        try:
            updater.check_for_update()
        except updater.UpdateCheckError:
            pass
        else:
            raise AssertionError("expected UpdateCheckError")


def test_check_wraps_network_errors():
    class _Broken:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_latest_release(self):
            from buzzcommon.release import NetworkError

            raise NetworkError("offline")

    with patch.object(updater, "GitHubReleasesClient", _Broken):
        try:
            updater.check_for_update()
        except updater.UpdateCheckError as exc:
            assert "offline" in str(exc)
        else:
            raise AssertionError("expected UpdateCheckError")


def test_can_self_update_false_from_source():
    assert not updater.is_frozen()
    assert not updater.can_self_update()


def test_prepare_updater_copies_local_updater_outside_install_dir():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_dir = root / "install"
        install_dir.mkdir()
        local = install_dir / updater.UPDATER_EXE_NAME
        local.write_bytes(b"MZ updater")
        staging = root / "staging"

        with patch.object(updater, "get_updater_path", return_value=local), \
             patch.object(updater, "_updater_staging_dir", return_value=staging):
            path = updater.prepare_updater()

        assert path.is_file()
        assert path.read_bytes() == b"MZ updater"
        assert install_dir not in path.parents


def test_prepare_updater_downloads_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        staging = root / "staging"
        staging.mkdir()
        downloaded = staging / "BuzzInstallerUpdater.exe"
        downloaded.write_bytes(b"MZ downloaded")
        missing = root / "install" / "BuzzInstallerUpdater.exe"

        with patch.object(updater, "get_updater_path", return_value=missing), \
             patch.object(updater, "_updater_staging_dir", return_value=staging), \
             patch.object(updater, "_download_updater", return_value=downloaded):
            path = updater.prepare_updater()

        assert path == downloaded
        assert path.read_bytes() == b"MZ downloaded"


def test_launch_updater_passes_expected_arguments():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_dir = root / "install"
        install_dir.mkdir()
        (install_dir / "BuzzInstaller.exe").write_bytes(b"MZ")
        updater_exe = root / "BuzzInstallerUpdater.exe"
        updater_exe.write_bytes(b"MZ updater")
        info = updater.UpdateInfo(
            version="9.9.9",
            tag="v9.9.9",
            html_url="https://example.test/release",
            notes="",
            asset_name="BuzzInstaller.zip",
            asset_url="https://example.test/BuzzInstaller.zip",
            asset_size=123,
        )

        with patch.object(updater, "can_self_update", return_value=True), \
             patch.object(updater, "get_install_dir", return_value=install_dir), \
             patch.object(updater, "prepare_updater", return_value=updater_exe), \
             patch.object(updater.subprocess, "Popen") as popen:
            result = updater.launch_updater(info)

        assert result == updater_exe
        args = popen.call_args[0][0]
        assert args[0] == str(updater_exe)
        assert "--install-dir" in args
        assert args[args.index("--install-dir") + 1] == str(install_dir)
        assert args[args.index("--wait-pid") + 1] == str(os.getpid())
        assert args[args.index("--target-version") + 1] == "9.9.9"
        assert args[args.index("--launch-exe") + 1] == str(install_dir / "BuzzInstaller.exe")


def test_launch_updater_requires_installed_build():
    with patch.object(updater, "can_self_update", return_value=False):
        try:
            updater.launch_updater(None)
        except updater.UpdateLaunchError:
            pass
        else:
            raise AssertionError("expected UpdateLaunchError")


def test_changelog_url_falls_back_to_releases_page():
    assert updater.changelog_url(None) == updater.RELEASES_PAGE


TESTS = [
    test_is_newer_semantic,
    test_is_newer_defaults_to_running_version,
    test_check_returns_update_info,
    test_check_up_to_date_returns_none,
    test_check_missing_asset_raises,
    test_check_wraps_network_errors,
    test_can_self_update_false_from_source,
    test_prepare_updater_copies_local_updater_outside_install_dir,
    test_prepare_updater_downloads_when_missing,
    test_launch_updater_passes_expected_arguments,
    test_launch_updater_requires_installed_build,
    test_changelog_url_falls_back_to_releases_page,
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
