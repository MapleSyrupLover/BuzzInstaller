"""
Tests for the installer self-update notification.

Verifies:
  1. Installed version == GitHub version → no update prompt.
  2. Installed version < GitHub version → update prompt is shown.
  3. Installed version > GitHub version → no update prompt.
  4. Semantic comparison: 1.10.0 > 1.9.0.
  5. GitHub API unavailable → installer continues normally.
  6. Release missing BuzzInstallerInstaller.exe → installer continues normally.
  7. Correct GitHub asset is selected.
  8. The direct download URL points to BuzzInstallerInstaller.exe.
  9. YES opens the direct download URL.
  10. NO continues normal installer functionality.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch, MagicMock

# Ensure the installer package is importable
_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from github_api import Release, ReleaseAsset, GitHubReleasesClient
from update_check import check_for_installer_update
from version import APP_VERSION, compare_versions


def _make_release(tag: str, with_installer: bool = True) -> Release:
    """Create a release with the given tag and optional installer asset."""
    assets = [
        ReleaseAsset(
            name="BuzzInstaller.zip",
            url=f"https://example.com/{tag}/BuzzInstaller.zip",
            size=50000,
        ),
        ReleaseAsset(
            name="BuzzInstaller.exe",
            url=f"https://example.com/{tag}/BuzzInstaller.exe",
            size=5000,
        ),
    ]
    if with_installer:
        assets.append(
            ReleaseAsset(
                name="BuzzInstallerInstaller.exe",
                url=f"https://example.com/{tag}/BuzzInstallerInstaller.exe",
                size=2000,
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
# 1. Installed version == GitHub version → no update prompt
# ---------------------------------------------------------------------------
def test_no_update_when_versions_equal():
    """Installed == GitHub → no download URL returned."""
    release = _make_release(f"v{APP_VERSION}")
    client = _make_client(release)
    url = check_for_installer_update(client)
    assert url is None


# ---------------------------------------------------------------------------
# 2. Installed version < GitHub version → update prompt is shown
# ---------------------------------------------------------------------------
def test_update_available_when_installed_older():
    """Installed < GitHub → direct download URL returned."""
    release = _make_release("v99.0.0")
    client = _make_client(release)
    url = check_for_installer_update(client)
    assert url is not None
    assert "BuzzInstallerInstaller.exe" in url


# ---------------------------------------------------------------------------
# 3. Installed version > GitHub version → no update prompt
# ---------------------------------------------------------------------------
def test_no_update_when_installed_newer():
    """Installed > GitHub → no download URL returned."""
    release = _make_release("v0.0.1")
    client = _make_client(release)
    url = check_for_installer_update(client)
    assert url is None


# ---------------------------------------------------------------------------
# 4. Semantic comparison: 1.10.0 > 1.9.0
# ---------------------------------------------------------------------------
def test_semantic_comparison():
    """1.10.0 > 1.9.0 numerically."""
    assert compare_versions("1.10.0", "1.9.0") == 1
    assert compare_versions("1.9.0", "1.10.0") == -1
    assert compare_versions("1.10.0", "1.10.0") == 0


# ---------------------------------------------------------------------------
# 5. GitHub API unavailable → installer continues normally
# ---------------------------------------------------------------------------
def test_github_unavailable_returns_none():
    """Network error → no download URL, no crash."""
    from github_api import NetworkError
    client = _make_client(None, NetworkError("no internet"))
    url = check_for_installer_update(client)
    assert url is None


def test_github_http_error_returns_none():
    """HTTP error → no download URL, no crash."""
    from github_api import HTTPError
    client = _make_client(None, HTTPError(500, "Internal Server Error"))
    url = check_for_installer_update(client)
    assert url is None


# ---------------------------------------------------------------------------
# 6. Release missing BuzzInstallerInstaller.exe → installer continues normally
# ---------------------------------------------------------------------------
def test_missing_installer_asset_returns_none():
    """Release without BuzzInstallerInstaller.exe → no download URL."""
    release = _make_release("v99.0.0", with_installer=False)
    client = _make_client(release)
    url = check_for_installer_update(client)
    assert url is None


# ---------------------------------------------------------------------------
# 7. Correct GitHub asset is selected
# ---------------------------------------------------------------------------
def test_correct_asset_selected():
    """find_installer_asset returns BuzzInstallerInstaller.exe, not others."""
    release = _make_release("v99.0.0")
    asset = release.find_installer_asset()
    assert asset is not None
    assert asset.name == "BuzzInstallerInstaller.exe"


def test_find_installer_asset_ignores_others():
    """find_installer_asset does NOT return BuzzInstaller.zip or .exe."""
    release = _make_release("v99.0.0")
    asset = release.find_installer_asset()
    assert asset is not None
    assert "BuzzInstaller.zip" not in asset.name
    assert asset.name != "BuzzInstaller.exe"


# ---------------------------------------------------------------------------
# 8. Direct download URL points to BuzzInstallerInstaller.exe
# ---------------------------------------------------------------------------
def test_direct_download_url_is_installer():
    """The returned URL is the direct asset download, not the release page."""
    release = _make_release("v99.0.0")
    client = _make_client(release)
    url = check_for_installer_update(client)
    assert url is not None
    assert "BuzzInstallerInstaller.exe" in url
    # Must NOT be the release webpage
    assert "/releases/tag/" not in url
    assert "/releases/download/" in url or "example.com" in url


# ---------------------------------------------------------------------------
# 9. YES opens the direct download URL
# ---------------------------------------------------------------------------
def test_yes_opens_download_url():
    """UpdateDialog._on_yes opens the direct URL via webbrowser."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from ui import UpdateDialog
    dlg = UpdateDialog("https://example.com/BuzzInstallerInstaller.exe")
    with patch("webbrowser.open") as mock_open:
        dlg._on_yes()
        mock_open.assert_called_once_with(
            "https://example.com/BuzzInstallerInstaller.exe"
        )
    assert dlg.result() is True


# ---------------------------------------------------------------------------
# 10. NO continues normal installer functionality
# ---------------------------------------------------------------------------
def test_no_continues_normally():
    """UpdateDialog._on_no sets result False (continue)."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from ui import UpdateDialog
    dlg = UpdateDialog("https://example.com/BuzzInstallerInstaller.exe")
    dlg._on_no()
    assert dlg.result() is False


if __name__ == "__main__":
    tests = [
        test_no_update_when_versions_equal,
        test_update_available_when_installed_older,
        test_no_update_when_installed_newer,
        test_semantic_comparison,
        test_github_unavailable_returns_none,
        test_github_http_error_returns_none,
        test_missing_installer_asset_returns_none,
        test_correct_asset_selected,
        test_find_installer_asset_ignores_others,
        test_direct_download_url_is_installer,
        test_yes_opens_download_url,
        test_no_continues_normally,
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
