"""
Core installer/updater logic for BuzzInstaller.

This module handles:
  * Detecting whether BuzzInstaller is installed
  * Determining the installed version (via ``BuzzInstaller.exe --version``)
  * Comparing installed vs latest GitHub release versions
  * Downloading the new executable
  * Safely replacing the running executable
  * Registering Windows integration after a fresh install
  * Deleting BuzzInstaller
  * PATH installation support
  * Installation metadata

The installer is a separate, disposable process.  It never replaces its
own executable — it only replaces BuzzInstaller.exe.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from version import Version, parse_version, VersionError, compare_versions
from github_api import (
    GitHubReleasesClient, Release, ReleaseAsset,
    GitHubAPIError, NetworkError, HTTPError, InvalidResponseError,
    ReleaseNotFoundError, AssetNotFoundError,
)
from downloader import (
    download_asset, DownloadError, DownloadFailedError,
    DownloadCancelledError, CorruptedDownloadError,
)
from metadata import (
    get_install_dir as get_metadata_install_dir,
    get_default_install_dir, load_metadata, save_metadata,
    clear_metadata, is_path_install as metadata_is_path_install,
)
from path_manager import (
    add_to_path, remove_from_path, is_in_path, PathError,
)
from shortcut import (
    create_shortcut as _create_shortcut,
    delete_shortcut as _delete_shortcut,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_NAME = "BuzzInstaller"
EXE_NAME = "BuzzInstaller.exe"

# Shortcut location: C:\BuzzInstaller\BuzzInstaller.lnk
SHORTCUT_DIR = Path("C:/BuzzInstaller")
SHORTCUT_PATH = SHORTCUT_DIR / "BuzzInstaller.lnk"

# Default PATH installation directory: %LOCALAPPDATA%\BuzzInstaller
def get_install_dir() -> Path:
    """Return the installation directory from metadata, or the default."""
    return get_metadata_install_dir()


def get_installed_exe_path() -> Path:
    """Return the path to the installed BuzzInstaller.exe."""
    return get_install_dir() / EXE_NAME


def create_shortcut_for_installed() -> bool:
    """
    Create (or update) the C:\\BuzzInstaller\\BuzzInstaller.lnk shortcut
    pointing at the currently installed BuzzInstaller.exe.

    Returns True on success, False on failure (e.g. permission denied).
    The shortcut is best-effort — installation continues even if this fails.
    """
    exe = get_installed_exe_path()
    if not exe.is_file():
        return False
    return _create_shortcut(exe, SHORTCUT_PATH)


# ---------------------------------------------------------------------------
# Installation detection
# ---------------------------------------------------------------------------
def is_installed() -> bool:
    """True if BuzzInstaller.exe exists in the installation directory."""
    return get_installed_exe_path().is_file()


def get_installed_version() -> Optional[Version]:
    """
    Determine the installed BuzzInstaller version by running
    ``BuzzInstaller.exe --version`` and parsing its output.

    Returns None if the executable is missing or the version cannot be
    determined.
    """
    exe = get_installed_exe_path()
    if not exe.is_file():
        return None

    try:
        result = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        output = (result.stdout or "").strip()
        if not output:
            output = (result.stderr or "").strip()
        if not output:
            return None

        # Expected output: "BuzzInstaller 1.2.3"
        # Extract the version number from the output.
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", output)
        if not m:
            return None
        return parse_version(m.group(1))
    except (subprocess.SubprocessError, OSError, VersionError):
        return None


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------
def is_process_running(exe_path: Path) -> bool:
    """
    Check whether a process with the given executable path is running.

    Uses ``tasklist`` on Windows.  Returns True if any process with the
    executable name is found.
    """
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_path.name}", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # tasklist output: '"Image Name","PID","Session Name","Session#","Mem Usage"'
        return exe_path.name.lower() in result.stdout.lower()
    except (subprocess.SubprocessError, OSError):
        return False


def wait_for_process_exit(exe_path: Path, timeout: float = 60.0,
                          poll_interval: float = 0.5) -> bool:
    """
    Wait until no process with the given executable name is running.

    Returns True if the process exited within *timeout* seconds,
    False otherwise.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_running(exe_path):
            return True
        time.sleep(poll_interval)
    return not is_process_running(exe_path)


def terminate_process(exe_path: Path) -> bool:
    """
    Terminate all processes with the given executable name.

    Uses ``taskkill`` on Windows.  Returns True if successful.
    """
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", exe_path.name],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


# ---------------------------------------------------------------------------
# Installation / update
# ---------------------------------------------------------------------------
def install_exe(downloaded: Path, dest_dir: Path) -> Path:
    """
    Install the downloaded executable to *dest_dir*.

    Creates the directory if needed and copies the file.  Returns the
    destination path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / EXE_NAME
    shutil.copy2(downloaded, dest)
    return dest


def replace_exe(downloaded: Path, dest_dir: Path) -> Path:
    """
    Safely replace the installed BuzzInstaller.exe with *downloaded*.

    The new executable must already be fully downloaded and validated.
    This function only performs the file replacement.

    Returns the destination path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / EXE_NAME

    # If the destination exists, remove it first (it's not running —
    # the caller ensures this).
    if dest.exists():
        dest.unlink()

    shutil.copy2(downloaded, dest)
    return dest


def launch_buzzinstaller(exe_path: Path) -> None:
    """Launch BuzzInstaller.exe detached."""
    try:
        subprocess.Popen(
            [str(exe_path)],
            close_fds=True,
            creationflags=(
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            ) if sys.platform == "win32" else 0,
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Delete functionality
# ---------------------------------------------------------------------------
def delete_buzzinstaller() -> bool:
    """
    Delete the installed BuzzInstaller.

    Steps:
    1. Locate the installed executable.
    2. If running, terminate it.
    3. Delete BuzzInstaller.exe.
    4. If PATH install, remove the directory from PATH.
    5. Unregister Windows integration.
    6. Clear installation metadata.

    Returns True on success.
    """
    exe_path = get_installed_exe_path()
    install_dir = get_install_dir()

    # Terminate if running
    if is_process_running(exe_path):
        terminate_process(exe_path)
        wait_for_process_exit(exe_path, timeout=10)

    # Unregister Windows integration (best-effort)
    try:
        _unregister_integration(exe_path)
    except Exception:
        pass

    # Delete the shortcut (best-effort)
    try:
        _delete_shortcut(SHORTCUT_PATH)
        # Only remove C:\BuzzInstaller if it is now empty
        try:
            if SHORTCUT_DIR.exists() and not any(SHORTCUT_DIR.iterdir()):
                SHORTCUT_DIR.rmdir()
        except OSError:
            pass
    except Exception:
        pass

    # Delete the executable
    if exe_path.exists():
        try:
            exe_path.unlink()
        except OSError as exc:
            raise InstallerError(f"Could not delete {exe_path}: {exc}")

    # Remove from PATH if it was a PATH install
    if metadata_is_path_install():
        try:
            remove_from_path(install_dir)
        except PathError:
            pass

    # Clear metadata
    clear_metadata()

    return True


def _unregister_integration(exe_path: Path) -> None:
    """Unregister Windows integration by invoking --uninstall."""
    if not exe_path.is_file():
        return
    try:
        subprocess.run(
            [str(exe_path), "--uninstall"],
            capture_output=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (subprocess.SubprocessError, OSError):
        pass


# ---------------------------------------------------------------------------
# High-level orchestration
# ---------------------------------------------------------------------------
class InstallerError(Exception):
    """Base error for installer orchestration failures."""


class UpdateNotNeeded(InstallerError):
    """Raised when the installed version is already up to date."""


class NewerInstalled(InstallerError):
    """Raised when the installed version is newer than the latest release."""


class Installer:
    """Orchestrates the install/update flow."""

    def __init__(
        self,
        client: Optional[GitHubReleasesClient] = None,
        install_dir: Optional[Path] = None,
        path_install: bool = False,
        status_cb: Optional[Callable[[str], None]] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.client = client or GitHubReleasesClient()
        self.install_dir = install_dir or get_install_dir()
        self.path_install = path_install
        self.status_cb = status_cb or (lambda msg: None)
        self.progress_cb = progress_cb
        self.cancelled = cancelled or (lambda: False)

    def _status(self, msg: str) -> None:
        self.status_cb(msg)

    # ------------------------------------------------------------------
    # Version check
    # ------------------------------------------------------------------
    def check_for_updates(self) -> dict:
        """
        Check GitHub Releases and determine the current state.

        Returns a dict with:
            release: Release or None
            installed: bool
            installed_version: Version or None
            latest_version: Version or None
            state: str — one of:
                "not_installed", "up_to_date", "update_available",
                "newer_installed", "error"
            message: str — user-facing status message
        """
        result = {
            "release": None,
            "installed": False,
            "installed_version": None,
            "latest_version": None,
            "state": "error",
            "message": "Unable to check for updates.",
        }

        try:
            self._status("Checking for the latest version...")
            release = self.client.get_latest_release()
            result["release"] = release
            result["latest_version"] = release.version

            # Find the executable asset
            asset = release.find_exe_asset()
            if asset is None:
                result["state"] = "error"
                result["message"] = (
                    f"No {EXE_NAME} asset found in release {release.tag_name}"
                )
                return result

            # Check if installed
            installed = is_installed()
            result["installed"] = installed

            if not installed:
                result["state"] = "not_installed"
                result["message"] = "BuzzInstaller is not installed."
                return result

            installed_ver = get_installed_version()
            result["installed_version"] = installed_ver

            if installed_ver is None:
                # Installed but can't determine version — treat as update available
                result["state"] = "update_available"
                result["message"] = (
                    f"BuzzInstaller {release.version} is available."
                )
                return result

            cmp = compare_versions(str(installed_ver), str(release.version))
            if cmp == 0:
                result["state"] = "up_to_date"
                result["message"] = "BuzzInstaller is up to date."
            elif cmp > 0:
                result["state"] = "newer_installed"
                result["message"] = (
                    "Installed version is newer than the latest release."
                )
            else:
                result["state"] = "update_available"
                result["message"] = (
                    f"BuzzInstaller {release.version} is available."
                )

            return result

        except (GitHubAPIError, DownloadError) as exc:
            result["state"] = "error"
            result["message"] = f"Unable to check for updates: {exc}"
            return result
        except Exception as exc:
            result["state"] = "error"
            result["message"] = f"Unable to check for updates: {exc}"
            return result

    # ------------------------------------------------------------------
    # Install / update
    # ------------------------------------------------------------------
    def run(self) -> int:
        """
        Run the full install/update flow.

        Returns:
            0 — success (installed, updated, or already up to date)
            1 — failure
        """
        try:
            self._status("Checking for latest version...")
            release = self.client.get_latest_release()

            # Find the executable asset
            asset = release.find_exe_asset()
            if asset is None:
                raise AssetNotFoundError(
                    f"No {EXE_NAME} asset found in release {release.tag_name}"
                )

            self._status(f"Latest version: {release.tag_name}")

            # Check if installed
            if is_installed():
                installed_ver = get_installed_version()
                if installed_ver is not None:
                    latest_ver = release.version
                    cmp = compare_versions(str(installed_ver), str(latest_ver))
                    if cmp == 0:
                        self._status("Latest version is already installed.")
                        return 0
                    if cmp > 0:
                        self._status(
                            "Installed version is newer than the latest GitHub release."
                        )
                        return 0

            # Download the new executable
            self._status(f"Downloading BuzzInstaller {release.version}...")
            downloaded = self._download(asset)

            # Determine if this is an update or fresh install
            if is_installed():
                self._status("Updating BuzzInstaller...")
                self._update(downloaded)
            else:
                self._status("Installing BuzzInstaller...")
                self._install(downloaded)

            # Save installation metadata
            save_metadata(self.install_dir, self.path_install)

            # Create/update the C:\BuzzInstaller shortcut (best-effort)
            if create_shortcut_for_installed():
                self._status("Shortcut created at C:\\BuzzInstaller\\BuzzInstaller.lnk")
            else:
                self._status(
                    "Warning: Could not create shortcut at "
                    "C:\\BuzzInstaller\\BuzzInstaller.lnk"
                )

            # Clean up temp file
            try:
                downloaded.unlink(missing_ok=True)
            except OSError:
                pass

            # Launch
            self._status("Launching BuzzInstaller...")
            launch_buzzinstaller(get_installed_exe_path())

            return 0

        except UpdateNotNeeded:
            return 0
        except NewerInstalled:
            return 0
        except (GitHubAPIError, DownloadError, InstallerError) as exc:
            self._status(f"Update failed: {exc}")
            return 1
        except Exception as exc:
            self._status(f"Update failed: {exc}")
            return 1

    def _download(self, asset: ReleaseAsset) -> Path:
        """Download the asset to a temp location with progress."""
        import tempfile
        temp_dir = Path(tempfile.gettempdir()) / "BuzzInstallerInstaller"
        temp_dir.mkdir(parents=True, exist_ok=True)

        def progress(received: int, total: int) -> Optional[bool]:
            if self.progress_cb:
                self.progress_cb(received, total)
            if self.cancelled and self.cancelled():
                return False
            return None

        return download_asset(
            asset,
            dest_dir=temp_dir,
            progress_cb=progress,
            cancelled=self.cancelled,
        )

    def _install(self, downloaded: Path) -> None:
        """Fresh install: place the exe and register Windows integration."""
        install_exe(downloaded, self.install_dir)

        # If PATH install was selected, add the directory to the user PATH
        if self.path_install:
            try:
                add_to_path(self.install_dir)
            except PathError as exc:
                self._status(f"Warning: Could not add to PATH: {exc}")

        # Register Windows integration (protocol + context menu)
        try:
            self._register_integration()
        except Exception as exc:
            # Integration registration is best-effort — don't fail the install.
            self._status(f"Warning: Windows integration registration failed: {exc}")

    def _update(self, downloaded: Path) -> None:
        """Update: safely replace the running executable."""
        exe_path = self.install_dir / EXE_NAME

        # Wait for the existing process to exit (if running)
        if is_process_running(exe_path):
            self._status("Waiting for BuzzInstaller to close...")
            if not wait_for_process_exit(exe_path, timeout=30):
                # Ask the user / terminate
                self._status("Closing BuzzInstaller...")
                terminate_process(exe_path)
                if not wait_for_process_exit(exe_path, timeout=10):
                    raise InstallerError(
                        "Could not close BuzzInstaller. Please close it manually."
                    )

        # Replace the executable
        replace_exe(downloaded, self.install_dir)

    def _register_integration(self) -> None:
        """
        Register Windows integration (BZ:// protocol + context menu).

        This reuses the existing integration logic from the main app by
        invoking ``BuzzInstaller.exe --install``.
        """
        exe = self.install_dir / EXE_NAME
        if not exe.is_file():
            return
        try:
            subprocess.run(
                [str(exe), "--install"],
                capture_output=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except (subprocess.SubprocessError, OSError):
            pass