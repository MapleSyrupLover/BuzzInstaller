"""
Single source of truth for all application versions.

BOTH BuzzInstaller and BuzzInstallerInstaller import their version from
here.  To bump a version, edit ONLY this file.
"""

# Version of the main BuzzInstaller application (the onedir distribution)
BUZZINSTALLER_VERSION = "1.5.1"

# Version of the separate installer/updater executable
BUZZINSTALLER_INSTALLER_VERSION = "1.5.1"