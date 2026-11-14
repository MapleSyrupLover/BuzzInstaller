"""Shared helpers for the BuzzInstaller setup and updater executables.

This package is bundled into both ``BuzzInstallerSetup.exe`` and
``BuzzInstallerUpdater.exe``.  It contains the GitHub release client,
the downloader, the transactional installation routine and the Windows
integration helpers so the two executables behave identically.
"""

from __future__ import annotations

import os
import sys

# Make ``src/`` importable so ``VERSIONS`` resolves both from source and
# from inside a PyInstaller bundle.
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

__all__ = [
    "download",
    "install",
    "metadata",
    "pathman",
    "release",
    "shortcuts",
    "versions",
]
