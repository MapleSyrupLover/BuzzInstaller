"""
Tests for installation detection and error handling.

These tests do NOT perform destructive update operations — they only
verify detection logic, error handling, and version comparison flows.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure the installer package is importable
_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from installer import (
    is_installed, get_installed_version, get_install_dir,
    get_installed_exe_path, Installer, InstallerError,
    install_exe, replace_exe, _is_zip, _extract_bundle,
)
from github_api import (
    GitHubReleasesClient, Release, ReleaseAsset,
    NetworkError, HTTPError, InvalidResponseError,
    ReleaseNotFoundError, AssetNotFoundError,
)
from downloader import (
    DownloadError, DownloadFailedError, DownloadCancelledError,
    CorruptedDownloadError,
)
from version import parse_version


def _make_http_response(status: int = 200, body: bytes = b"", headers: dict | None = None):
    """Create a mock response that works as a context manager."""
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.read.return_value = body
    if headers is not None:
        mock_resp.headers = headers
    # Make it work with `with ... as resp:`
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# Installation detection tests
# ---------------------------------------------------------------------------
def test_is_installed_false_when_no_exe():
    """is_installed() returns False when no BuzzInstaller.exe exists."""
    with patch("installer.get_installed_exe_path") as mock_exe:
        mock_exe.return_value = Path("C:/nonexistent/BuzzInstaller.exe")
        assert is_installed() is False


def test_is_installed_true_when_exe_exists():
    """is_installed() returns True when BuzzInstaller.exe exists."""
    temp_dir = Path(tempfile.mkdtemp())
    exe = temp_dir / "BuzzInstaller.exe"
    exe.write_bytes(b"MZ fake exe")
    with patch("installer.get_installed_exe_path", return_value=exe):
        assert is_installed() is True


def test_get_installed_version_parses_output():
    """get_installed_version() parses 'BuzzInstaller 1.2.3' output."""
    temp_dir = Path(tempfile.mkdtemp())
    exe = temp_dir / "BuzzInstaller.exe"
    exe.write_bytes(b"MZ fake exe")
    with patch("installer.get_installed_exe_path", return_value=exe), \
         patch("installer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="BuzzInstaller 1.2.3\n",
            stderr="",
            returncode=0,
        )
        ver = get_installed_version()
        assert ver == parse_version("1.2.3")


def test_get_installed_version_none_when_missing():
    """get_installed_version() returns None when exe is missing."""
    with patch("installer.get_installed_exe_path") as mock_exe:
        mock_exe.return_value = Path("C:/nonexistent/BuzzInstaller.exe")
        assert get_installed_version() is None


def test_get_installed_version_none_on_error():
    """get_installed_version() returns None when subprocess fails."""
    temp_dir = Path(tempfile.mkdtemp())
    exe = temp_dir / "BuzzInstaller.exe"
    exe.write_bytes(b"MZ fake exe")
    with patch("installer.get_installed_exe_path", return_value=exe), \
         patch("installer.subprocess.run", side_effect=OSError("boom")):
        assert get_installed_version() is None


def test_get_installed_version_none_on_bad_output():
    """get_installed_version() returns None when output has no version."""
    temp_dir = Path(tempfile.mkdtemp())
    exe = temp_dir / "BuzzInstaller.exe"
    exe.write_bytes(b"MZ fake exe")
    with patch("installer.get_installed_exe_path", return_value=exe), \
         patch("installer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="garbage output\n",
            stderr="",
            returncode=0,
        )
        assert get_installed_version() is None


# ---------------------------------------------------------------------------
# GitHub API error handling tests
# ---------------------------------------------------------------------------
def test_github_network_error():
    """NetworkError is raised when GitHub is unreachable."""
    client = GitHubReleasesClient()
    with patch("urllib.request.urlopen", side_effect=OSError("no internet")):
        try:
            client.get_latest_release()
            assert False, "Expected NetworkError"
        except NetworkError:
            pass


def test_github_http_error():
    """HTTPError is raised for non-200 responses."""
    client = GitHubReleasesClient()
    with patch("urllib.request.urlopen", side_effect=__import__("urllib.error").error.HTTPError(
        "url", 500, "Internal Server Error", None, None
    )):
        try:
            client.get_latest_release()
            assert False, "Expected HTTPError"
        except HTTPError as exc:
            assert exc.status == 500


def test_github_release_not_found():
    """ReleaseNotFoundError is raised for 404."""
    client = GitHubReleasesClient()
    with patch("urllib.request.urlopen", side_effect=__import__("urllib.error").error.HTTPError(
        "url", 404, "Not Found", None, None
    )):
        try:
            client.get_latest_release()
            assert False, "Expected ReleaseNotFoundError"
        except ReleaseNotFoundError:
            pass


def test_github_invalid_response():
    """InvalidResponseError is raised for malformed JSON."""
    client = GitHubReleasesClient()
    mock_resp = _make_http_response(status=200, body=b"not json at all")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        try:
            client.get_latest_release()
            assert False, "Expected InvalidResponseError"
        except InvalidResponseError:
            pass


def test_github_missing_release_data():
    """InvalidResponseError is raised when tag_name is missing."""
    client = GitHubReleasesClient()
    mock_resp = _make_http_response(status=200, body=b'{"message": "Not Found"}')
    with patch("urllib.request.urlopen", return_value=mock_resp):
        try:
            client.get_latest_release()
            assert False, "Expected InvalidResponseError"
        except InvalidResponseError:
            pass


def test_github_missing_exe_asset():
    """find_exe_asset() returns None when no BuzzInstaller.exe asset exists."""
    release = Release(
        tag_name="v1.0.0",
        html_url="https://github.com/MapleSyrupLover/BuzzInstaller/releases/tag/v1.0.0",
        assets=(
            ReleaseAsset(name="README.md", url="https://example.com/README.md", size=100),
            ReleaseAsset(name="other.exe", url="https://example.com/other.exe", size=200),
        ),
    )
    assert release.find_exe_asset() is None


def test_github_finds_exe_asset():
    """find_exe_asset() finds the BuzzInstaller.exe asset."""
    release = Release(
        tag_name="v1.0.0",
        html_url="https://github.com/MapleSyrupLover/BuzzInstaller/releases/tag/v1.0.0",
        assets=(
            ReleaseAsset(name="README.md", url="https://example.com/README.md", size=100),
            ReleaseAsset(name="BuzzInstaller.exe", url="https://example.com/BuzzInstaller.exe", size=5000),
        ),
    )
    asset = release.find_exe_asset()
    assert asset is not None
    assert asset.name == "BuzzInstaller.exe"


def test_github_prefers_bundle_zip():
    """find_exe_asset() prefers the BuzzInstaller.zip bundle over the exe."""
    release = Release(
        tag_name="v1.0.0",
        html_url="https://github.com/MapleSyrupLover/BuzzInstaller/releases/tag/v1.0.0",
        assets=(
            ReleaseAsset(name="BuzzInstaller.exe", url="https://example.com/BuzzInstaller.exe", size=5000),
            ReleaseAsset(name="BuzzInstaller.zip", url="https://example.com/BuzzInstaller.zip", size=50000),
        ),
    )
    asset = release.find_exe_asset()
    assert asset is not None
    assert asset.name == "BuzzInstaller.zip"


def test_github_parses_valid_release():
    """get_latest_release() parses a valid release response."""
    client = GitHubReleasesClient()
    body = b"""{
        "tag_name": "v1.2.3",
        "html_url": "https://github.com/MapleSyrupLover/BuzzInstaller/releases/tag/v1.2.3",
        "assets": [
            {
                "name": "BuzzInstaller.exe",
                "browser_download_url": "https://github.com/MapleSyrupLover/BuzzInstaller/releases/download/v1.2.3/BuzzInstaller.exe",
                "size": 5000,
                "content_type": "application/x-msdownload"
            }
        ]
    }"""
    mock_resp = _make_http_response(status=200, body=body)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        release = client.get_latest_release()
        assert release.tag_name == "v1.2.3"
        assert release.version == parse_version("1.2.3")
        asset = release.find_exe_asset()
        assert asset is not None
        assert asset.name == "BuzzInstaller.exe"


# ---------------------------------------------------------------------------
# Download error handling tests
# ---------------------------------------------------------------------------
def test_download_failed_error():
    """DownloadFailedError is raised on HTTP errors."""
    from downloader import download_asset
    asset = ReleaseAsset(name="BuzzInstaller.exe", url="https://example.com/BuzzInstaller.exe", size=100)
    with patch("urllib.request.urlopen", side_effect=__import__("urllib.error").error.HTTPError(
        "url", 404, "Not Found", None, None
    )):
        try:
            download_asset(asset, dest_dir=Path(tempfile.gettempdir()))
            assert False, "Expected DownloadFailedError"
        except DownloadFailedError:
            pass


def test_download_corrupted_empty():
    """CorruptedDownloadError is raised for empty downloads."""
    from downloader import download_asset
    asset = ReleaseAsset(name="BuzzInstaller.exe", url="https://example.com/BuzzInstaller.exe", size=0)

    mock_resp = _make_http_response(status=200, body=b"", headers={"Content-Length": "0"})

    with patch("urllib.request.urlopen", return_value=mock_resp):
        try:
            download_asset(asset, dest_dir=Path(tempfile.gettempdir()))
            assert False, "Expected CorruptedDownloadError"
        except CorruptedDownloadError:
            pass


def test_download_success():
    """download_asset() returns the downloaded file path."""
    from downloader import download_asset
    asset = ReleaseAsset(name="BuzzInstaller.exe", url="https://example.com/BuzzInstaller.exe", size=5)

    mock_resp = _make_http_response(status=200, headers={"Content-Length": "5"})
    mock_resp.read.side_effect = [b"hello", b""]

    temp_dir = Path(tempfile.mkdtemp())
    with patch("urllib.request.urlopen", return_value=mock_resp):
        path = download_asset(asset, dest_dir=temp_dir)
        assert path.exists()
        assert path.stat().st_size == 5


def test_download_cancelled():
    """DownloadCancelledError is raised when cancelled."""
    from downloader import download_asset
    asset = ReleaseAsset(name="BuzzInstaller.exe", url="https://example.com/BuzzInstaller.exe", size=100)

    mock_resp = _make_http_response(status=200, headers={"Content-Length": "100"})
    mock_resp.read.side_effect = [b"x" * 64, b"x" * 36]

    with patch("urllib.request.urlopen", return_value=mock_resp):
        try:
            download_asset(
                asset,
                dest_dir=Path(tempfile.gettempdir()),
                cancelled=lambda: True,
            )
            assert False, "Expected DownloadCancelledError"
        except DownloadCancelledError:
            pass


# ---------------------------------------------------------------------------
# Bundle ZIP handling tests
# ---------------------------------------------------------------------------
def _make_bundle_zip() -> tuple[Path, Path]:
    """Create a fake onedir bundle ZIP with BuzzInstaller.exe and _internal/."""
    tmp = Path(tempfile.mkdtemp())
    zip_path = tmp / "BuzzInstaller.zip"
    with __import__("zipfile").ZipFile(zip_path, "w") as zf:
        zf.writestr("BuzzInstaller.exe", b"MZ fake exe")
        zf.writestr("_internal/python312.dll", b"fake python dll")
        zf.writestr("PySide6/QtCore.pyd", b"fake qt pyd")
    return zip_path, tmp


def test_is_zip_detects_zip():
    """_is_zip returns True for a ZIP archive."""
    zip_path, _ = _make_bundle_zip()
    assert _is_zip(zip_path) is True


def test_is_zip_false_for_exe():
    """_is_zip returns False for a plain exe."""
    tmp = Path(tempfile.mkdtemp())
    exe = tmp / "BuzzInstaller.exe"
    exe.write_bytes(b"MZ fake exe")
    assert _is_zip(exe) is False


def test_extract_bundle_extracts_entire_dir():
    """_extract_bundle extracts the full onedir directory from a ZIP."""
    zip_path, src_dir = _make_bundle_zip()
    dest_dir = Path(tempfile.mkdtemp()) / "installed"
    exe = _extract_bundle(zip_path, dest_dir)
    assert exe == dest_dir / "BuzzInstaller.exe"
    assert exe.is_file()
    assert (dest_dir / "_internal" / "python312.dll").is_file()
    assert (dest_dir / "PySide6" / "QtCore.pyd").is_file()


def test_extract_bundle_missing_exe_raises():
    """_extract_bundle raises InstallerError when exe is missing."""
    tmp = Path(tempfile.mkdtemp())
    zip_path = tmp / "bad.zip"
    with __import__("zipfile").ZipFile(zip_path, "w") as zf:
        zf.writestr("README.md", b"no exe here")
    dest_dir = Path(tempfile.mkdtemp()) / "installed"
    try:
        _extract_bundle(zip_path, dest_dir)
        assert False, "Expected InstallerError"
    except InstallerError:
        pass


def test_install_exe_with_zip_extracts_directory():
    """install_exe with a ZIP extracts the entire distribution."""
    zip_path, _ = _make_bundle_zip()
    dest_dir = Path(tempfile.mkdtemp()) / "installed"
    dest = install_exe(zip_path, dest_dir)
    assert dest == dest_dir / "BuzzInstaller.exe"
    assert dest.is_file()
    assert (dest_dir / "_internal" / "python312.dll").is_file()


def test_install_exe_with_single_file_copies():
    """install_exe with a plain exe copies just the file."""
    tmp = Path(tempfile.mkdtemp())
    exe = tmp / "BuzzInstaller.exe"
    exe.write_bytes(b"MZ fake exe")
    dest_dir = Path(tempfile.mkdtemp()) / "installed"
    dest = install_exe(exe, dest_dir)
    assert dest == dest_dir / "BuzzInstaller.exe"
    assert dest.is_file()


def test_replace_exe_with_zip_replaces_directory():
    """replace_exe with a ZIP replaces the entire installation directory."""
    zip_path, _ = _make_bundle_zip()
    dest_dir = Path(tempfile.mkdtemp()) / "installed"
    dest_dir.mkdir(parents=True)
    # Old installation with stale files
    (dest_dir / "BuzzInstaller.exe").write_bytes(b"OLD exe")
    (dest_dir / "stale.dll").write_bytes(b"stale")
    (dest_dir / "old_dir").mkdir()
    (dest_dir / "old_dir" / "old.txt").write_text("old")

    dest = replace_exe(zip_path, dest_dir)
    assert dest == dest_dir / "BuzzInstaller.exe"
    assert dest.is_file()
    assert (dest_dir / "_internal" / "python312.dll").is_file()
    # Old files removed
    assert not (dest_dir / "stale.dll").exists()
    assert not (dest_dir / "old_dir").exists()


def test_replace_exe_preserves_user_data():
    """Config, history and logs survive a bundle replacement."""
    zip_path, _ = _make_bundle_zip()
    dest_dir = Path(tempfile.mkdtemp()) / "installed"
    dest_dir.mkdir(parents=True)
    (dest_dir / "BuzzInstaller.exe").write_bytes(b"OLD exe")
    (dest_dir / "config.json").write_text('{"theme": "dark"}')
    (dest_dir / "history.json").write_text('{"entries": []}')
    (dest_dir / "logs").mkdir()
    (dest_dir / "logs" / "app.log").write_text("log line")

    replace_exe(zip_path, dest_dir)

    assert (dest_dir / "config.json").read_text() == '{"theme": "dark"}'
    assert (dest_dir / "history.json").read_text() == '{"entries": []}'
    assert (dest_dir / "logs" / "app.log").read_text() == "log line"
    assert (dest_dir / "_internal" / "python312.dll").is_file()


# ---------------------------------------------------------------------------
# Installer flow tests (non-destructive)
# ---------------------------------------------------------------------------
def test_installer_up_to_date():
    """Installer returns 0 when installed version == latest release."""
    installer = Installer()
    with patch.object(installer, "client") as mock_client, \
         patch("installer.is_installed", return_value=True), \
         patch("installer.get_installed_version", return_value=parse_version("1.0.0")):
        mock_client.get_latest_release.return_value = Release(
            tag_name="v1.0.0",
            html_url="https://github.com/MapleSyrupLover/BuzzInstaller/releases/tag/v1.0.0",
            assets=(ReleaseAsset(name="BuzzInstaller.exe", url="https://example.com/BuzzInstaller.exe", size=100),),
        )
        result = installer.run()
        assert result == 0


def test_installer_newer_installed():
    """Installer returns 0 when installed version > latest release."""
    installer = Installer()
    with patch.object(installer, "client") as mock_client, \
         patch("installer.is_installed", return_value=True), \
         patch("installer.get_installed_version", return_value=parse_version("2.0.0")):
        mock_client.get_latest_release.return_value = Release(
            tag_name="v1.0.0",
            html_url="https://github.com/MapleSyrupLover/BuzzInstaller/releases/tag/v1.0.0",
            assets=(ReleaseAsset(name="BuzzInstaller.exe", url="https://example.com/BuzzInstaller.exe", size=100),),
        )
        result = installer.run()
        assert result == 0


def test_installer_missing_asset():
    """Installer returns 1 when no executable asset exists."""
    installer = Installer()
    with patch.object(installer, "client") as mock_client:
        mock_client.get_latest_release.return_value = Release(
            tag_name="v1.0.0",
            html_url="https://github.com/MapleSyrupLover/BuzzInstaller/releases/tag/v1.0.0",
            assets=(ReleaseAsset(name="README.md", url="https://example.com/README.md", size=100),),
        )
        result = installer.run()
        assert result == 1


def test_installer_network_failure():
    """Installer returns 1 when GitHub is unreachable."""
    installer = Installer()
    with patch.object(installer, "client") as mock_client:
        mock_client.get_latest_release.side_effect = NetworkError("no internet")
        result = installer.run()
        assert result == 1


def test_installer_invalid_response():
    """Installer returns 1 when GitHub response is invalid."""
    installer = Installer()
    with patch.object(installer, "client") as mock_client:
        mock_client.get_latest_release.side_effect = InvalidResponseError("bad JSON")
        result = installer.run()
        assert result == 1


def test_installer_download_failure():
    """Installer returns 1 when download fails."""
    installer = Installer()
    with patch.object(installer, "client") as mock_client, \
         patch("installer.is_installed", return_value=False), \
         patch.object(installer, "_download", side_effect=DownloadFailedError("HTTP 500")):
        mock_client.get_latest_release.return_value = Release(
            tag_name="v1.0.0",
            html_url="https://github.com/MapleSyrupLover/BuzzInstaller/releases/tag/v1.0.0",
            assets=(ReleaseAsset(name="BuzzInstaller.exe", url="https://example.com/BuzzInstaller.exe", size=100),),
        )
        result = installer.run()
        assert result == 1


if __name__ == "__main__":
    # Simple test runner
    tests = [
        test_is_installed_false_when_no_exe,
        test_is_installed_true_when_exe_exists,
        test_get_installed_version_parses_output,
        test_get_installed_version_none_when_missing,
        test_get_installed_version_none_on_error,
        test_get_installed_version_none_on_bad_output,
        test_github_network_error,
        test_github_http_error,
        test_github_release_not_found,
        test_github_invalid_response,
        test_github_missing_release_data,
        test_github_missing_exe_asset,
        test_github_finds_exe_asset,
        test_github_prefers_bundle_zip,
        test_github_parses_valid_release,
        test_download_failed_error,
        test_download_corrupted_empty,
        test_download_success,
        test_download_cancelled,
        test_is_zip_detects_zip,
        test_is_zip_false_for_exe,
        test_extract_bundle_extracts_entire_dir,
        test_extract_bundle_missing_exe_raises,
        test_install_exe_with_zip_extracts_directory,
        test_install_exe_with_single_file_copies,
        test_replace_exe_with_zip_replaces_directory,
        test_replace_exe_preserves_user_data,
        test_installer_up_to_date,
        test_installer_newer_installed,
        test_installer_missing_asset,
        test_installer_network_failure,
        test_installer_invalid_response,
        test_installer_download_failure,
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