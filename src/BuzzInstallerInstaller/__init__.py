"""
BuzzInstallerInstaller — a separate, lightweight installer/updater for
BuzzInstaller.

This application is intentionally independent from the main BuzzInstaller
GUI.  It downloads the latest BuzzInstaller release from GitHub and
installs or updates the executable safely.

The updater is a separate, disposable process.  BuzzInstaller NEVER
replaces its own executable — BuzzInstallerInstaller does that.
"""

import os
import sys

# Ensure the parent (src/) is on sys.path so VERSIONS.py is importable.
_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from VERSIONS import BUZZINSTALLER_INSTALLER_VERSION as __version__  # noqa: E402
__author__ = "BuzzInstaller"
