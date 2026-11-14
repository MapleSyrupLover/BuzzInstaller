#!/usr/bin/env python
"""BuzzInstallerSetup - fresh-install setup and maintenance tool.

The setup is a one-shot installer: it installs the latest BuzzInstaller
plus ``BuzzInstallerUpdater.exe`` and never updates an existing install.
Updates are applied by the updater launched from inside the app.

Headless modes:
    BuzzInstallerSetup.exe --install [--dir PATH] [--path]
    BuzzInstallerSetup.exe --delete
    BuzzInstallerSetup.exe --clean
    BuzzInstallerSetup.exe --version
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from buzzcommon import install, metadata  # noqa: E402
from buzzcommon.versions import SETUP_VERSION  # noqa: E402

import setup_core  # noqa: E402


def setup_logging() -> None:
    # Log outside the installation/app-data folder: the setup must be able
    # to move or delete that folder on Windows, which fails while a log file
    # inside it is open.
    try:
        import tempfile

        log_dir = Path(tempfile.gettempdir()) / "BuzzInstallerSetup"
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            filename=str(log_dir / "setup.log"),
            filemode="a",
            encoding="utf-8",
        )
    except OSError:
        logging.basicConfig(level=logging.INFO)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="BuzzInstallerSetup",
        description="Install BuzzInstaller (fresh installs only).",
        add_help=True,
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install the latest BuzzInstaller without showing the UI.",
    )
    parser.add_argument(
        "--dir",
        default="",
        help="Installation directory (default: %LOCALAPPDATA%\\BuzzInstaller).",
    )
    parser.add_argument(
        "--path",
        action="store_true",
        help="Add the installation directory to the user PATH.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete BuzzInstaller without showing the UI.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove every BuzzInstaller trace without showing the UI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"BuzzInstallerSetup {SETUP_VERSION}",
    )
    return parser


def _resolve_dir(value: object, default: Optional[Path] = None) -> Path:
    text = str(value or "").strip()
    if text:
        return Path(text).expanduser()
    if default is not None:
        return default
    return metadata.get_install_dir()


def run_headless(args: argparse.Namespace) -> int:
    target = _resolve_dir(args.dir)

    if args.install:
        try:
            outcome = setup_core.install_latest(
                target,
                path_install=bool(args.path),
                status_cb=lambda message: print(message),
            )
        except setup_core.SetupError as exc:
            print(f"Installation failed: {exc}", file=sys.stderr)
            return 1
        print(outcome.message)
        try:
            setup_core.launch_installed(outcome.exe_path)
        except Exception as exc:
            print(f"Warning: could not launch BuzzInstaller: {exc}", file=sys.stderr)
        return 0

    if args.delete:
        ok = setup_core.delete_installation(target)
        print("BuzzInstaller deleted." if ok else "Deletion failed.")
        return 0 if ok else 1

    if args.clean:
        results = setup_core.clean_installation()
        ok = all(bool(value) for value in results.values())
        print("BuzzInstaller cleaned." if ok else f"Clean incomplete: {results}")
        return 0 if ok else 1

    return -1


def main() -> int:
    setup_logging()
    args = build_parser().parse_args()

    if args.install or args.delete or args.clean:
        code = run_headless(args)
        if code >= 0:
            return code

    from PySide6.QtWidgets import QApplication

    import ui

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("BuzzInstaller Setup")
    window = ui.SetupWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
