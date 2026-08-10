"""
Installer self-update notification.

Checks GitHub Releases for a newer BuzzInstallerInstaller.exe and returns
the direct download URL if one exists.  This is a best-effort check —
any failure (network, missing asset, invalid version) simply means "no
update available" so the installer continues normally.

This module does NOT implement self-updating.  It only detects that a
newer installer exists and provides the direct download URL so the user
can download it manually.
"""

from __future__ import annotations

from typing import Optional

from github_api import GitHubReleasesClient
from version import APP_VERSION, compare_versions


def check_for_installer_update(
    client: Optional[GitHubReleasesClient] = None,
) -> Optional[str]:
    """
    Check GitHub for a newer BuzzInstallerInstaller.exe.

    Returns the direct download URL of the newer installer, or None if:
      * The installed version is equal to or newer than the GitHub version.
      * The release does not contain BuzzInstallerInstaller.exe.
      * GitHub cannot be reached.
      * Any other error occurs (best-effort).

    The returned URL is the GitHub API's ``browser_download_url`` for the
    BuzzInstallerInstaller.exe asset — never the release webpage.
    """
    try:
        client = client or GitHubReleasesClient()
        release = client.get_latest_release()

        # Find the BuzzInstallerInstaller.exe asset specifically.
        asset = release.find_installer_asset()
        if asset is None:
            return None

        # Compare installed version vs GitHub release version.
        cmp = compare_versions(APP_VERSION, str(release.version))
        if cmp >= 0:
            # Installed is equal or newer — no update needed.
            return None

        # A newer installer exists — return the direct download URL.
        return asset.url
    except Exception:
        # Best-effort: any failure means "no update available".
        return None
