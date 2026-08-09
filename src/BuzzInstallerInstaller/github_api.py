"""
GitHub Releases API client for BuzzInstaller.

Queries the GitHub Releases API to find the latest BuzzInstaller release
and its Windows executable asset.  Uses only the standard library
(``urllib.request``) so the installer stays lightweight and has no
external dependencies.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from version import parse_version, Version, VersionError

# GitHub repository and API endpoint
REPO_OWNER = "MapleSyrupLover"
REPO_NAME = "BuzzInstaller"
RELEASES_API_URL = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
)

# Expected bundle asset name — a ZIP of the entire onedir distribution
# (BuzzInstaller.exe, _internal/, PySide6/, Qt6*.dll, plugins/, etc.)
BUNDLE_ASSET_NAME = "BuzzInstaller.zip"
EXE_ASSET_NAME = "BuzzInstaller.exe"  # kept for backward compatibility

# Real-browser User-Agent (GitHub API requires a User-Agent header)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
)

# Timeout for API requests (seconds)
API_TIMEOUT = 30


class GitHubAPIError(Exception):
    """Base error for GitHub API failures."""


class NetworkError(GitHubAPIError):
    """Raised when the network is unreachable."""


class HTTPError(GitHubAPIError):
    """Raised when the GitHub API returns a non-200 status."""

    def __init__(self, status: int, message: str = "") -> None:
        self.status = status
        super().__init__(f"GitHub API returned HTTP {status}: {message}")


class InvalidResponseError(GitHubAPIError):
    """Raised when the GitHub API response is malformed."""


class ReleaseNotFoundError(GitHubAPIError):
    """Raised when no release is found."""


class AssetNotFoundError(GitHubAPIError):
    """Raised when the expected executable asset is missing."""


@dataclass(frozen=True)
class ReleaseAsset:
    """A single release asset."""

    name: str
    url: str          # browser_download_url
    size: int
    content_type: str = ""


@dataclass(frozen=True)
class Release:
    """A GitHub release."""

    tag_name: str
    html_url: str
    assets: tuple[ReleaseAsset, ...] = field(default_factory=tuple)

    @property
    def version(self) -> Version:
        """The release version parsed from the tag name."""
        return parse_version(self.tag_name)

    def find_exe_asset(self) -> Optional[ReleaseAsset]:
        """Return the BuzzInstaller.exe asset, or None if not found.

        Prefers the onedir bundle ZIP (``BuzzInstaller.zip``) which
        contains the entire distribution directory.  Falls back to the
        legacy single ``BuzzInstaller.exe`` for backward compatibility.
        """
        # Prefer the bundle ZIP (contains the full onedir distribution).
        for asset in self.assets:
            if asset.name.lower() == BUNDLE_ASSET_NAME.lower():
                return asset
        # Fall back to the legacy single-exe asset.
        for asset in self.assets:
            if asset.name.lower() == EXE_ASSET_NAME.lower():
                return asset
        return None


class GitHubReleasesClient:
    """Client for the GitHub Releases API."""

    def __init__(self, api_url: str = RELEASES_API_URL,
                 timeout: int = API_TIMEOUT) -> None:
        self.api_url = api_url
        self.timeout = timeout

    def get_latest_release(self) -> Release:
        """
        Fetch the latest release from GitHub.

        Raises:
            NetworkError — no internet connection / DNS failure
            HTTPError — GitHub API returned an error status
            InvalidResponseError — response is not valid JSON
            ReleaseNotFoundError — no release found
        """
        req = urllib.request.Request(
            self.api_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                if status != 200:
                    raise HTTPError(status, resp.read(200).decode("utf-8", "replace"))
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ReleaseNotFoundError(
                    f"No release found for {REPO_OWNER}/{REPO_NAME}"
                ) from exc
            raise HTTPError(exc.code, exc.reason) from exc
        except urllib.error.URLError as exc:
            raise NetworkError(f"Cannot reach GitHub API: {exc.reason}") from exc
        except OSError as exc:
            raise NetworkError(f"Network error: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvalidResponseError(f"Invalid JSON from GitHub API: {exc}") from exc

        return self._parse_release(data)

    @staticmethod
    def _parse_release(data: dict) -> Release:
        """Parse a release dict from the GitHub API into a Release."""
        tag_name = data.get("tag_name")
        if not tag_name:
            raise InvalidResponseError("Release is missing 'tag_name'")

        # Validate the tag is a version we can parse
        try:
            parse_version(tag_name)
        except VersionError as exc:
            raise InvalidResponseError(
                f"Release tag {tag_name!r} is not a valid version: {exc}"
            ) from exc

        html_url = data.get("html_url", "")
        assets_raw = data.get("assets", [])
        if not isinstance(assets_raw, list):
            raise InvalidResponseError("Release 'assets' is not a list")

        assets: list[ReleaseAsset] = []
        for item in assets_raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            url = item.get("browser_download_url", "")
            size = int(item.get("size", 0) or 0)
            content_type = item.get("content_type", "")
            if name and url:
                assets.append(ReleaseAsset(
                    name=name, url=url, size=size, content_type=content_type,
                ))

        return Release(tag_name=tag_name, html_url=html_url,
                       assets=tuple(assets))