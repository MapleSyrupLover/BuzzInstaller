"""
BuzzInstallerInstaller — a separate, lightweight installer/updater for
BuzzInstaller.

This application is intentionally independent from the main BuzzInstaller
GUI.  It downloads the latest BuzzInstaller release from GitHub and
installs or updates the executable safely.

The updater is a separate, disposable process.  BuzzInstaller NEVER
replaces its own executable — BuzzInstallerInstaller does that.
"""

__version__ = "1.0.0"
__author__ = "BuzzInstaller"