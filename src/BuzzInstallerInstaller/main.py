#!/usr/bin/env python
"""
BuzzInstallerInstaller — entry point.

A separate, lightweight installer/updater for BuzzInstaller.

Usage:
    BuzzInstallerInstaller.exe            # Show UI and run install/update
    BuzzInstallerInstaller.exe --check    # Check for updates (no UI)
    BuzzInstallerInstaller.exe --version  # Show version
    BuzzInstallerInstaller.exe --help     # Show help
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure this package's modules are importable
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

from installer import Installer, get_install_dir, get_installed_exe_path, is_installed
from metadata import get_install_dir as get_metadata_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="BuzzInstallerInstaller",
        description="Install or update BuzzInstaller from GitHub Releases",
        add_help=True,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for updates and print the result (no UI)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete BuzzInstaller (no UI, no confirmation)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="BuzzInstallerInstaller 1.0.0",
    )
    return parser


def run_check() -> int:
    """Headless check: print the latest version and whether an update is needed."""
    from github_api import GitHubReleasesClient
    from version import compare_versions

    try:
        client = GitHubReleasesClient()
        release = client.get_latest_release()
        print(f"Latest release: {release.tag_name}")

        if is_installed():
            from installer import get_installed_version
            installed = get_installed_version()
            if installed is not None:
                print(f"Installed version: {installed}")
                cmp = compare_versions(str(installed), str(release.version))
                if cmp == 0:
                    print("Status: up to date")
                    return 0
                if cmp > 0:
                    print("Status: installed version is newer than latest release")
                    return 0
                print("Status: update available")
                return 2
            print("Status: installed version unknown")
            return 1
        else:
            print("Status: not installed")
            return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    args = build_parser().parse_args()

    if args.check:
        return run_check()

    if args.delete:
        from installer import delete_buzzinstaller
        ok = delete_buzzinstaller()
        print("BuzzInstaller deleted." if ok else "Deletion failed.", file=sys.stderr if not ok else sys.stdout)
        return 0 if ok else 1

    # Run the UI
    from ui import run_ui
    installer = Installer(install_dir=get_metadata_dir())
    return run_ui(installer)


if __name__ == "__main__":
    sys.exit(main())