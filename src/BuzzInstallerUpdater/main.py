#!/usr/bin/env python
"""BuzzInstallerUpdater - replaces an installed BuzzInstaller build.

The updater is a small, self-contained executable.  It is normally
copied to ``%TEMP%`` and started by BuzzInstaller itself, which passes
the installation directory and its own process ID:

    BuzzInstallerUpdater.exe --install-dir DIR --wait-pid 1234 \
        --current-version 1.6.0 --target-version 1.7.0 \
        --launch-exe DIR\\BuzzInstaller.exe

Headless modes:
    BuzzInstallerUpdater.exe --check     # print update availability
    BuzzInstallerUpdater.exe --version   # print the updater version
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Make the shared package importable both from source and from inside a
# PyInstaller bundle.
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from buzzcommon import install, metadata  # noqa: E402
from buzzcommon.release import GitHubReleasesClient, ReleaseError  # noqa: E402
from buzzcommon.versions import UPDATER_VERSION, is_newer  # noqa: E402


def _creation_flags() -> int:
    if sys.platform == "win32":
        return (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    return 0


def _log_path() -> Path:
    """Keep the updater log outside the install directory.

    The updater replaces the install directory (which is also the app data
    directory by default), so an open log file inside it would make the
    directory impossible to move on Windows.
    """
    return Path(tempfile.gettempdir()) / "BuzzInstallerUpdate" / "updater.log"


def setup_logging() -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            filename=str(path),
            filemode="a",
            encoding="utf-8",
        )
    except OSError:
        try:
            fallback = Path(tempfile.gettempdir()) / "BuzzInstallerUpdate" / "updater.log"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
                filename=str(fallback),
                filemode="a",
                encoding="utf-8",
            )
        except OSError:
            logging.basicConfig(level=logging.INFO)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="BuzzInstallerUpdater",
        description="Update an installed BuzzInstaller build.",
        add_help=True,
    )
    parser.add_argument(
        "--install-dir",
        help="BuzzInstaller installation directory (default: saved location).",
    )
    parser.add_argument(
        "--wait-pid",
        type=int,
        default=0,
        help="Process ID of the BuzzInstaller instance that started the updater.",
    )
    parser.add_argument(
        "--current-version",
        default="",
        help="Version of the build that is being replaced.",
    )
    parser.add_argument(
        "--target-version",
        default="",
        help="Version the caller expects to install (informational).",
    )
    parser.add_argument(
        "--launch-exe",
        default="",
        help="BuzzInstaller.exe to restart after the update.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for updates and print the result (no UI).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"BuzzInstallerUpdater {UPDATER_VERSION}",
    )
    return parser


def resolve_install_dir(value: Optional[str]) -> Path:
    if value:
        return Path(value).expanduser()
    try:
        return metadata.get_install_dir()
    except Exception:
        return metadata.get_default_install_dir()


def resolve_launch_exe(value: Optional[str], install_dir: Path) -> Path:
    if value:
        return Path(value).expanduser()
    return Path(install_dir) / install.EXE_NAME


def maybe_relocate(install_dir: Path) -> None:
    """Copy the updater out of the install directory before replacing it.

    The app normally starts the updater from ``%TEMP%`` already.  This
    safeguard keeps manual launches working too: the install directory is
    moved aside during the update, so an updater running from inside it
    cannot survive.
    """
    if not getattr(sys, "frozen", False):
        return
    if os.environ.get("BUZZINSTALLER_UPDATER_RELOCATED") == "1":
        return
    exe = Path(sys.executable).resolve()
    try:
        exe.relative_to(Path(install_dir).resolve())
    except ValueError:
        return

    target_dir = Path(tempfile.mkdtemp(prefix="buzzinstaller-updater-"))
    target = target_dir / exe.name
    try:
        shutil.copy2(exe, target)
    except OSError:
        logging.exception("Could not relocate the updater")
        return

    env = dict(os.environ)
    env["BUZZINSTALLER_UPDATER_RELOCATED"] = "1"
    try:
        subprocess.Popen(
            [str(target), *sys.argv[1:]],
            cwd=str(target_dir),
            env=env,
            close_fds=True,
            creationflags=_creation_flags(),
        )
    except OSError:
        logging.exception("Could not start the relocated updater")
        return
    sys.exit(0)


def run_check(install_dir: Path, current_version: str) -> int:
    detected = install.installed_version(install_dir) or current_version or "0.0.0"
    try:
        release = GitHubReleasesClient().get_latest_release()
    except ReleaseError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    latest = str(release.version)
    print(f"Latest release: {latest}")
    print(f"Installed version: {detected}")
    if is_newer(latest, detected):
        print("Status: update available")
        return 2
    print("Status: up to date")
    return 0


def run_gui(
    install_dir: Path,
    *,
    wait_pid: int,
    current_version: str,
    target_version: str,
    launch_exe: Path,
) -> int:
    from PySide6.QtWidgets import QApplication

    import ui

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("BuzzInstaller Updater")
    window = ui.UpdaterWindow(
        install_dir=install_dir,
        wait_pid=wait_pid,
        current_version=current_version,
        target_version=target_version,
        launch_exe=launch_exe,
    )
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()


def main() -> int:
    setup_logging()
    args = build_parser().parse_args()

    install_dir = resolve_install_dir(args.install_dir)
    launch_exe = resolve_launch_exe(args.launch_exe, install_dir)

    if args.check:
        return run_check(install_dir, args.current_version)

    logging.info(
        "Updater %s starting (install=%s pid=%s current=%s target=%s)",
        UPDATER_VERSION,
        install_dir,
        args.wait_pid,
        args.current_version,
        args.target_version,
    )
    maybe_relocate(install_dir)
    return run_gui(
        install_dir,
        wait_pid=args.wait_pid,
        current_version=args.current_version,
        target_version=args.target_version,
        launch_exe=launch_exe,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
