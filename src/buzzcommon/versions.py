"""Version helpers shared by the setup and updater executables."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import VERSIONS as _VERSIONS  # noqa: E402

APP_VERSION = str(getattr(_VERSIONS, "BUZZINSTALLER_VERSION", "0.0.0"))
UPDATER_VERSION = str(
    getattr(_VERSIONS, "BUZZINSTALLER_UPDATER_VERSION", APP_VERSION)
)
SETUP_VERSION = str(getattr(_VERSIONS, "BUZZINSTALLER_SETUP_VERSION", APP_VERSION))
INSTALLER_VERSION = str(
    getattr(_VERSIONS, "BUZZINSTALLER_INSTALLER_VERSION", APP_VERSION)
)

_VERSION_RE = re.compile(r"^\s*[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?\s*$")


class VersionError(ValueError):
    """Raised when a version string cannot be parsed."""


@dataclass(frozen=True, order=True)
class Version:
    """A parsed version (major.minor.patch.build)."""

    major: int
    minor: int = 0
    patch: int = 0
    build: int = 0

    def __str__(self) -> str:
        if self.build:
            return f"{self.major}.{self.minor}.{self.patch}.{self.build}"
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_version(text: str) -> Version:
    """Parse ``v1.2.3`` / ``1.2.3.0`` into a :class:`Version`."""
    if not isinstance(text, str):
        raise VersionError(f"Version must be a string, got {type(text).__name__}")
    match = _VERSION_RE.match(text)
    if not match:
        raise VersionError(f"Invalid version string: {text!r}")
    return Version(
        int(match.group(1)),
        int(match.group(2) or 0),
        int(match.group(3) or 0),
        int(match.group(4) or 0),
    )


def normalize_version(text: str) -> str:
    return str(parse_version(text))


def compare_versions(a: str, b: str) -> int:
    """Return -1/0/1 for ``a < b`` / ``a == b`` / ``a > b``."""
    va = parse_version(a)
    vb = parse_version(b)
    return (va > vb) - (va < vb)


def is_newer(latest: str, current: str) -> bool:
    """True when *latest* is newer than *current*."""
    try:
        return parse_version(latest) > parse_version(current)
    except VersionError:
        return False


def same_version(a: str, b: str) -> bool:
    """Compare two version strings while ignoring missing build components."""
    try:
        return parse_version(a) == parse_version(b)
    except VersionError:
        return False
