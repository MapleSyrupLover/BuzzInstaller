"""Core update pipeline used by ``BuzzInstallerUpdater.exe``."""

from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from buzzcommon import download, install, shortcuts
from buzzcommon.release import GitHubReleasesClient, ReleaseError
from buzzcommon.versions import is_newer

EXE_NAME = install.EXE_NAME
UPDATER_NAME = install.UPDATER_NAME

StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


class UpdateError(Exception):
    """Raised when the update cannot be completed."""


@dataclass
class UpdateResult:
    updated: bool
    version: str
    installed_version: str
    release_url: str
    notes: str
    message: str


def _noop_status(_: str) -> None:
    pass


def _current_updater_copy(install_dir: Path) -> Optional[Path]:
    """Copy the running/installed updater outside the install directory.

    The install directory is moved aside during the update, so the updater
    must be preserved from a location that is not inside it.
    """
    candidates: list[Path] = []
    if install.is_frozen():
        candidates.append(Path(sys.executable))
    candidates.append(Path(install_dir) / UPDATER_NAME)

    install_resolved = install_dir.resolve()
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            if install._is_under(candidate.resolve(), install_resolved):
                continue
            target_dir = Path(tempfile.mkdtemp(prefix="buzzinstaller-updater-copy-"))
            target = target_dir / UPDATER_NAME
            shutil.copy2(candidate, target)
            return target
        except OSError:
            continue
    return None


def run_update(
    install_dir: Path,
    *,
    current_version: Optional[str] = None,
    wait_pid: Optional[int] = None,
    launch_exe: Optional[Path] = None,
    status_cb: StatusCallback = _noop_status,
    progress_cb: Optional[ProgressCallback] = None,
    cancelled: Optional[CancelCallback] = None,
) -> UpdateResult:
    """Check GitHub, download the latest build and install it."""
    install_dir = Path(install_dir)
    exe_path = Path(launch_exe) if launch_exe else install_dir / EXE_NAME

    def status(message: str) -> None:
        status_cb(message)

    status("Checking for the latest version…")
    try:
        release = GitHubReleasesClient().get_latest_release()
    except ReleaseError as exc:
        raise UpdateError(f"Could not check for updates: {exc}") from exc

    latest = str(release.version)
    detected = install.installed_version(install_dir) or current_version or "0.0.0"
    if not is_newer(latest, detected):
        status("BuzzInstaller is already up to date.")
        try:
            install.launch_app(exe_path)
        except install.InstallError:
            pass
        return UpdateResult(
            updated=False,
            version=latest,
            installed_version=detected,
            release_url=release.changelog_url,
            notes=release.notes,
            message="BuzzInstaller is already up to date.",
        )

    bundle_asset = release.find_bundle_asset()
    if bundle_asset is None:
        raise UpdateError(
            "The latest release does not contain a BuzzInstaller bundle."
        )

    status("Closing BuzzInstaller…")
    if wait_pid:
        # Give the app a moment to exit cleanly; anything still running is
        # terminated below so an update can never hang on a stuck process.
        install.wait_for_pid(int(wait_pid), timeout=6)
    try:
        install.stop_app_processes(install_dir)
    except install.InstallError as exc:
        raise UpdateError(str(exc)) from exc

    # Preserve the updater outside the install directory before replacing it.
    updater_source = _current_updater_copy(install_dir)
    updater_asset = release.find_updater_asset()
    local_updater_version = (
        install.get_file_version(updater_source) if updater_source else None
    )
    refresh_updater = updater_asset is not None and (
        local_updater_version is None or is_newer(latest, local_updater_version)
    )
    if refresh_updater:
        try:
            status("Downloading the latest updater…")
            updated_updater = download.download_asset(
                updater_asset,
                progress_cb=progress_cb,
                cancelled=cancelled,
            )
            if install.get_file_version(updated_updater) is not None:
                updater_source = updated_updater
        except download.DownloadError as exc:
            # A failure to refresh the updater must not block the app update;
            # keep the current updater copy instead.
            if updater_source is None:
                raise UpdateError(str(exc)) from exc
    if updater_source is None:
        raise UpdateError(
            "The current updater could not be preserved and the release does "
            "not contain BuzzInstallerUpdater.exe."
        )

    try:
        status(f"Downloading BuzzInstaller {latest}…")
        bundle_path = download.download_asset(
            bundle_asset,
            progress_cb=progress_cb,
            cancelled=cancelled,
        )
    except download.DownloadCancelledError as exc:
        raise UpdateError("Update cancelled.") from exc
    except download.DownloadError as exc:
        raise UpdateError(str(exc)) from exc

    status("Installing the update…")
    try:
        installed_exe = install.install_bundle(
            bundle_path,
            install_dir,
            expected_version=latest,
            extra_files={UPDATER_NAME: updater_source},
            data_dir=install.app_data_dir(),
        )
    except install.InstallError as exc:
        raise UpdateError(str(exc)) from exc

    # Re-create the Start Menu shortcut and the App Paths entry so a system
    # that could not find BuzzInstaller before the update can find it now —
    # even if the app fails to start afterwards.
    try:
        shortcuts.create_all_shortcuts(installed_exe)
    except Exception:
        pass

    status("Starting BuzzInstaller…")
    launch_warning = ""
    try:
        install.launch_app(installed_exe)
    except install.InstallError as exc:
        launch_warning = str(exc)

    message = f"BuzzInstaller {latest} has been installed and restarted."
    if launch_warning:
        message = (
            f"BuzzInstaller {latest} has been installed, but it could not be "
            f"restarted automatically: {launch_warning}"
        )

    return UpdateResult(
        updated=True,
        version=latest,
        installed_version=detected,
        release_url=release.changelog_url,
        notes=release.notes,
        message=message,
    )
