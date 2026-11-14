"""In-app update checker and updater launcher for BuzzInstaller.

The heavy lifting (download, transactional replacement, restart) is done
by the dedicated ``BuzzInstallerUpdater.exe``.  This module only:

* checks the public GitHub Releases API for a newer build,
* makes sure the updater executable is available outside the install
  directory, and
* starts the updater detached and reports the update info.

The old PowerShell based helper has been removed: it could hang on
process/version checks and leave the old build installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Make ``src/`` importable so the shared ``buzzcommon`` package resolves
# both from source and inside the PyInstaller bundle.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from buzzcommon import download
from buzzcommon.release import (
    BUNDLE_ASSET_NAME,
    REPO_URL,
    RELEASES_PAGE,
    UPDATER_ASSET_NAME,
    GitHubReleasesClient,
    Release,
    ReleaseAsset,
    ReleaseError,
)
from buzzcommon.versions import is_newer as _is_newer

from config.paths import get_app_dir
from utils.file_utils import format_size
from utils.logging import get_logger
from version import APP_VERSION

logger = get_logger(__name__)

EXE_NAME = "BuzzInstaller.exe"
UPDATER_EXE_NAME = UPDATER_ASSET_NAME
ASSET_NAME = BUNDLE_ASSET_NAME


class UpdaterError(Exception):
    """Base class for updater failures."""


class UpdateCheckError(UpdaterError):
    """Raised when the release check cannot be completed."""


class UpdateLaunchError(UpdaterError):
    """Raised when the updater cannot be prepared or started."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag: str
    html_url: str
    notes: str
    asset_name: str
    asset_url: str
    asset_size: int
    published_at: str = ""

    @property
    def size_str(self) -> str:
        return format_size(self.asset_size)


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------
def is_newer(latest: str, current: Optional[str] = None) -> bool:
    """True when *latest* is newer than *current*.

    *current* defaults to the running version; passing it explicitly keeps
    the helper easy to test against simulated installations.
    """
    if current is None:
        current = current_version()
    return _is_newer(latest, current)


def current_version() -> str:
    return APP_VERSION


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_install_dir() -> Path:
    """Directory containing the running BuzzInstaller executable."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # Running from source - the repository root (self-update is disabled).
    return Path(__file__).resolve().parents[3]


def get_update_cache_dir() -> Path:
    return get_app_dir() / "updates"


def get_updater_path() -> Path:
    """Path of the updater executable inside the installation directory."""
    return get_install_dir() / UPDATER_EXE_NAME


def can_self_update() -> bool:
    """Only an installed (frozen) build can replace itself."""
    return is_frozen() and (get_install_dir() / EXE_NAME).is_file()


def changelog_url(info: Optional[UpdateInfo] = None) -> str:
    if info is not None and info.html_url:
        return info.html_url
    return RELEASES_PAGE


# ---------------------------------------------------------------------------
# Release checking
# ---------------------------------------------------------------------------
def _to_update_info(release: Release) -> UpdateInfo:
    asset = release.find_bundle_asset()
    if asset is None:
        raise UpdateCheckError(
            f"Release {release.tag_name} does not contain {BUNDLE_ASSET_NAME}."
        )
    return UpdateInfo(
        version=str(release.version),
        tag=release.tag_name,
        html_url=release.changelog_url or RELEASES_PAGE,
        notes=release.notes,
        asset_name=asset.name,
        asset_url=asset.url,
        asset_size=asset.size,
    )


def _latest_release(timeout: int = 15) -> Release:
    try:
        return GitHubReleasesClient(timeout=timeout).get_latest_release()
    except ReleaseError as exc:
        raise UpdateCheckError(str(exc)) from exc


def check_for_update(timeout: int = 15) -> Optional[UpdateInfo]:
    """Return update information when a newer release exists, else ``None``.

    Raises :class:`UpdateCheckError` on network / API failures so callers
    can distinguish "up to date" from "could not check".
    """
    release = _latest_release(timeout=timeout)
    if not is_newer(str(release.version)):
        return None
    return _to_update_info(release)


# ---------------------------------------------------------------------------
# Updater preparation
# ---------------------------------------------------------------------------
def _updater_staging_dir() -> Path:
    base = Path(tempfile.gettempdir()) / "BuzzInstallerUpdater"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=str(base)))


def _download_updater(dest_dir: Path) -> Path:
    """Download the latest updater executable from GitHub."""
    release = _latest_release()
    asset: Optional[ReleaseAsset] = release.find_updater_asset()
    if asset is None:
        raise UpdateLaunchError(
            "The latest release does not contain BuzzInstallerUpdater.exe. "
            "Reinstall BuzzInstaller using BuzzInstallerSetup.exe."
        )
    try:
        path = download.download_asset(asset, dest_dir=dest_dir)
    except download.DownloadError as exc:
        raise UpdateLaunchError(f"Could not download the updater: {exc}") from exc
    if not path.is_file() or not path.stat().st_size:
        raise UpdateLaunchError("The downloaded updater file is empty.")
    return path


def prepare_updater(info: Optional[UpdateInfo] = None) -> Path:
    """Return a copy of the updater outside the installation directory.

    The install directory is moved aside while the update is applied, so
    the executable that performs the update must not live inside it.
    Older installations do not ship the updater; in that case the latest
    release asset is downloaded once.
    """
    staging = _updater_staging_dir()
    staging.mkdir(parents=True, exist_ok=True)
    local = get_updater_path()
    target = staging / UPDATER_EXE_NAME
    if local.is_file():
        try:
            if local.stat().st_size > 0:
                shutil.copy2(local, target)
                return target
        except OSError as exc:
            logger.warning("Could not copy the installed updater: %s", exc)

    logger.info("Updater not found in the installation - downloading it.")
    downloaded = _download_updater(staging)
    if downloaded != target:
        try:
            downloaded.replace(target)
        except OSError as exc:
            raise UpdateLaunchError(f"Could not stage the updater: {exc}") from exc
    return target


def launch_updater(
    info: Optional[UpdateInfo] = None,
    *,
    updater_path: Optional[Path] = None,
) -> Path:
    """Start the updater detached. Returns the updater executable path."""
    if not can_self_update():
        raise UpdateLaunchError(
            "Self-update is only available in an installed build. "
            "Use BuzzInstallerSetup.exe to install BuzzInstaller."
        )

    path = Path(updater_path) if updater_path else prepare_updater(info)
    if not path.is_file():
        raise UpdateLaunchError("The updater executable is missing.")

    install_dir = get_install_dir()
    exe_path = install_dir / EXE_NAME
    target_version = info.version if info is not None else ""

    args = [
        str(path),
        "--install-dir", str(install_dir),
        "--wait-pid", str(os.getpid()),
        "--current-version", current_version(),
    ]
    if target_version:
        args += ["--target-version", target_version]
    args += ["--launch-exe", str(exe_path)]

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )

    try:
        subprocess.Popen(
            args,
            close_fds=True,
            cwd=str(path.parent),
            creationflags=creationflags,
        )
    except OSError as exc:
        raise UpdateLaunchError(f"Could not start the updater: {exc}") from exc

    logger.info("Updater started from %s; exiting for update", path)
    return path


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
def cleanup_downloads() -> None:
    """Remove stale update downloads and staging copies (best effort)."""
    cutoff = time.time() - 7 * 24 * 3600
    for directory in (
        get_update_cache_dir(),
        Path(tempfile.gettempdir()) / "BuzzInstallerUpdater",
        Path(tempfile.gettempdir()) / "BuzzInstallerUpdate",
    ):
        if not directory.is_dir():
            continue
        for item in directory.iterdir():
            try:
                if item.stat().st_mtime >= cutoff:
                    continue
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
            except OSError:
                pass
