"""Fresh-install logic for ``BuzzInstallerSetup.exe``.

The setup executable intentionally never acts as an updater: if a working
BuzzInstaller installation already exists the user is told to use the
in-app updater instead.  It still offers delete and deep-clean actions.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from buzzcommon import download, install, metadata, pathman, shortcuts
from buzzcommon.release import GitHubReleasesClient, ReleaseError
from buzzcommon.versions import SETUP_VERSION

StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]

EXE_NAME = install.EXE_NAME
UPDATER_NAME = install.UPDATER_NAME


class SetupError(Exception):
    """Raised when a fresh install cannot be completed."""


@dataclass
class InstallOutcome:
    version: str
    install_dir: Path
    exe_path: Path
    registered: bool
    shortcuts: dict[str, bool]
    message: str


@dataclass
class ExistingInstall:
    install_dir: Path
    version: Optional[str]
    installed: bool


def _noop(_: str) -> None:
    pass


def detect_existing(install_dir: Optional[Path] = None) -> ExistingInstall:
    """Describe the installation in *install_dir* (if any)."""
    target = Path(install_dir) if install_dir else metadata.get_install_dir()
    exe = target / EXE_NAME
    return ExistingInstall(
        install_dir=target,
        version=install.installed_version(target) if exe.is_file() else None,
        installed=exe.is_file(),
    )


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(
            os.path.realpath(b)
        )
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def install_latest(
    install_dir: Path,
    *,
    path_install: bool = False,
    status_cb: StatusCallback = _noop,
    progress_cb: Optional[ProgressCallback] = None,
    cancelled: Optional[CancelCallback] = None,
) -> InstallOutcome:
    """Download and install the latest BuzzInstaller release.

    Refuses to overwrite an existing installation: the setup is for fresh
    installs only.
    """
    install_dir = Path(install_dir)
    existing = detect_existing(install_dir)
    if existing.installed:
        version = existing.version or "an existing version"
        raise SetupError(
            f"BuzzInstaller {version} is already installed in {install_dir}. "
            "Use the update button inside BuzzInstaller to update it."
        )

    known = detect_existing()
    if known.installed and not _same_dir(known.install_dir, install_dir):
        version = known.version or "an existing version"
        raise SetupError(
            f"BuzzInstaller {version} is already installed in "
            f"{known.install_dir}. This setup performs fresh installs only; "
            "use the update button inside BuzzInstaller to update it."
        )

    def status(message: str) -> None:
        status_cb(message)

    status("Checking the latest release…")
    try:
        release = GitHubReleasesClient().get_latest_release()
    except ReleaseError as exc:
        raise SetupError(f"Could not check for the latest release: {exc}") from exc

    version = str(release.version)
    bundle_asset = release.find_bundle_asset()
    updater_asset = release.find_updater_asset()
    if bundle_asset is None:
        raise SetupError(
            "The latest release does not contain a BuzzInstaller bundle."
        )
    if updater_asset is None:
        raise SetupError(
            "The latest release does not contain BuzzInstallerUpdater.exe."
        )

    work_dir = Path(tempfile.mkdtemp(prefix="buzzinstaller-setup-"))
    try:
        try:
            status(f"Downloading BuzzInstaller {version}…")
            bundle_path = download.download_asset(
                bundle_asset,
                dest_dir=work_dir,
                progress_cb=progress_cb,
                cancelled=cancelled,
            )
            status("Downloading the updater…")
            updater_path = download.download_asset(
                updater_asset,
                dest_dir=work_dir,
                progress_cb=progress_cb,
                cancelled=cancelled,
            )
        except download.DownloadCancelledError as exc:
            raise SetupError("Installation cancelled.") from exc
        except download.DownloadError as exc:
            raise SetupError(str(exc)) from exc

        status("Closing running BuzzInstaller processes…")
        try:
            install.stop_app_processes(install_dir if install_dir.exists() else None)
        except install.InstallError as exc:
            raise SetupError(str(exc)) from exc

        status(f"Installing BuzzInstaller {version}…")
        try:
            exe_path = install.install_bundle(
                bundle_path,
                install_dir,
                expected_version=version,
                extra_files={UPDATER_NAME: updater_path},
                data_dir=install_dir,
            )
        except install.InstallError as exc:
            raise SetupError(str(exc)) from exc

        status("Configuring Windows integration…")
        if path_install:
            try:
                pathman.add_to_path(install_dir)
            except pathman.PathError:
                pass
        else:
            try:
                pathman.remove_from_path(install_dir)
            except pathman.PathError:
                pass

        shortcut_results = shortcuts.create_all_shortcuts(exe_path)
        metadata.save_metadata(install_dir, path_install)
        registered = install.register_integration(exe_path)

        return InstallOutcome(
            version=version,
            install_dir=install_dir,
            exe_path=exe_path,
            registered=registered,
            shortcuts=shortcut_results,
            message=f"BuzzInstaller {version} has been installed.",
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def launch_installed(exe_path: Path) -> None:
    install.launch_app(exe_path)


def delete_installation(install_dir: Optional[Path] = None) -> bool:
    target = Path(install_dir) if install_dir else metadata.get_install_dir()
    # Release the setup log if the target is the default app-data folder.
    logging.shutdown()
    return install.delete_install(target)


def clean_installation() -> dict[str, bool]:
    # The setup log lives inside the app data folder that is about to be
    # deleted; close it first so Windows can remove the directory.
    logging.shutdown()
    return install.clean_all()


def setup_version() -> str:
    return SETUP_VERSION
