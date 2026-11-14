"""Minimal GitHub Releases client (standard library only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from buzzcommon.versions import Version, VersionError, parse_version

REPO_OWNER = "MapleSyrupLover"
REPO_NAME = "BuzzInstaller"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
RELEASES_PAGE = f"{REPO_URL}/releases"
LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
)

BUNDLE_ASSET_NAME = "BuzzInstaller.zip"
EXE_ASSET_NAME = "BuzzInstaller.exe"
UPDATER_ASSET_NAME = "BuzzInstallerUpdater.exe"
SETUP_ASSET_NAME = "BuzzInstallerSetup.exe"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
)

API_TIMEOUT = 30


class ReleaseError(Exception):
    """Base error for release API failures."""


class NetworkError(ReleaseError):
    """The network is unreachable."""


class HTTPError(ReleaseError):
    """The GitHub API returned a non-200 status."""

    def __init__(self, status: int, message: str = "") -> None:
        self.status = status
        super().__init__(f"GitHub API returned HTTP {status}: {message}")


class InvalidResponseError(ReleaseError):
    """The GitHub API response is malformed."""


class ReleaseNotFoundError(ReleaseError):
    """No release was found."""


class AssetNotFoundError(ReleaseError):
    """The expected release asset is missing."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    content_type: str = ""


@dataclass(frozen=True)
class Release:
    tag_name: str
    html_url: str
    notes: str = ""
    assets: tuple[ReleaseAsset, ...] = field(default_factory=tuple)

    @property
    def version(self) -> Version:
        return parse_version(self.tag_name)

    @property
    def changelog_url(self) -> str:
        if self.html_url:
            return self.html_url
        return f"{RELEASES_PAGE}/tag/{self.tag_name}"

    def find_asset(self, *names: str) -> Optional[ReleaseAsset]:
        wanted = {name.lower() for name in names}
        for asset in self.assets:
            if asset.name.lower() in wanted:
                return asset
        return None

    def find_bundle_asset(self) -> Optional[ReleaseAsset]:
        return self.find_asset(BUNDLE_ASSET_NAME) or self.find_asset(EXE_ASSET_NAME)

    def find_updater_asset(self) -> Optional[ReleaseAsset]:
        return self.find_asset(UPDATER_ASSET_NAME)


class GitHubReleasesClient:
    """Client for the public GitHub Releases API."""

    def __init__(self, api_url: str = LATEST_RELEASE_URL, timeout: int = API_TIMEOUT) -> None:
        self.api_url = api_url
        self.timeout = timeout

    def get_latest_release(self) -> Release:
        request = urllib.request.Request(
            self.api_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                if status != 200:
                    raise HTTPError(status, response.read(200).decode("utf-8", "replace"))
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ReleaseNotFoundError(
                    f"No release found for {REPO_OWNER}/{REPO_NAME}"
                ) from exc
            raise HTTPError(exc.code, str(exc.reason)) from exc
        except urllib.error.URLError as exc:
            raise NetworkError(f"Cannot reach GitHub: {exc.reason}") from exc
        except OSError as exc:
            raise NetworkError(f"Network error: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvalidResponseError(f"Invalid JSON from GitHub: {exc}") from exc

        if not isinstance(data, dict):
            raise InvalidResponseError("GitHub returned an unexpected response.")
        if data.get("draft") or data.get("prerelease"):
            raise ReleaseNotFoundError("The latest release is a draft or prerelease.")
        return self._parse_release(data)

    @staticmethod
    def _parse_release(data: dict) -> Release:
        tag_name = data.get("tag_name")
        if not tag_name:
            raise InvalidResponseError("Release is missing 'tag_name'")
        try:
            parse_version(str(tag_name))
        except VersionError as exc:
            raise InvalidResponseError(
                f"Release tag {tag_name!r} is not a valid version: {exc}"
            ) from exc

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
                assets.append(
                    ReleaseAsset(
                        name=str(name),
                        url=str(url),
                        size=size,
                        content_type=str(content_type),
                    )
                )

        return Release(
            tag_name=str(tag_name),
            html_url=str(data.get("html_url", "") or ""),
            notes=str(data.get("body", "") or ""),
            assets=tuple(assets),
        )
