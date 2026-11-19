"""
Single source of truth for all application versions.

Every BuzzInstaller executable imports its version from here.  To bump a
version, edit ONLY this file.
"""

# Version of the main BuzzInstaller application (the onedir distribution)
BUZZINSTALLER_VERSION = "1.7.5"

# Version of the standalone updater that replaces an installed build
BUZZINSTALLER_UPDATER_VERSION = "1.7.5"

# Version of the fresh-install setup executable
BUZZINSTALLER_SETUP_VERSION = "1.7.5"

# Version of the legacy installer/updater executable (kept for compatibility)
BUZZINSTALLER_INSTALLER_VERSION = "1.7.5"
