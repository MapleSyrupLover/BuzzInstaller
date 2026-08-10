"""
Semantic version comparison for BuzzInstaller releases.

Versions follow the ``vMAJOR.MINOR.PATCH`` convention (the leading ``v``
is optional).  Comparison is done numerically per component — never as
string comparison — so ``1.10.0`` correctly sorts newer than ``1.9.0``.

The actual version values live in ``src/VERSIONS.py`` — the single
place to bump versions.
"""

from __future__ import annotations

import os
import sys
import re
from dataclasses import dataclass
from typing import Optional

# Ensure the parent (src/) is on sys.path so VERSIONS.py is importable.
_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from VERSIONS import BUZZINSTALLER_INSTALLER_VERSION as APP_VERSION  # noqa: E402

# Matches: v1.2.3, 1.2.3, v1.2, 1.2, v1, 1
_VERSION_RE = re.compile(r"^[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


@dataclass(frozen=True)
class Version:
    """A parsed semantic version (major.minor.patch)."""

    major: int
    minor: int = 0
    patch: int = 0

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __lt__(self, other: "Version") -> bool:
        return (self.major, self.minor, self.patch) < (
            other.major, other.minor, other.patch,
        )

    def __le__(self, other: "Version") -> bool:
        return (self.major, self.minor, self.patch) <= (
            other.major, other.minor, other.patch,
        )

    def __gt__(self, other: "Version") -> bool:
        return (self.major, self.minor, self.patch) > (
            other.major, other.minor, other.patch,
        )

    def __ge__(self, other: "Version") -> bool:
        return (self.major, self.minor, self.patch) >= (
            other.major, other.minor, other.patch,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return (self.major, self.minor, self.patch) == (
            other.major, other.minor, other.patch,
        )

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch))


class VersionError(ValueError):
    """Raised when a version string cannot be parsed."""


def parse_version(text: str) -> Version:
    """
    Parse a version string into a :class:`Version`.

    Accepts ``v1.2.3``, ``1.2.3``, ``v1.2``, ``1.2``, ``v1``, ``1``.
    Raises :class:`VersionError` for invalid input.
    """
    if not isinstance(text, str):
        raise VersionError(f"Version must be a string, got {type(text).__name__}")
    m = _VERSION_RE.match(text.strip())
    if not m:
        raise VersionError(f"Invalid version string: {text!r}")
    major = int(m.group(1))
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    return Version(major, minor, patch)


def normalize_version(text: str) -> str:
    """Return the canonical ``MAJOR.MINOR.PATCH`` form of *text*."""
    return str(parse_version(text))


def compare_versions(a: str, b: str) -> int:
    """
    Compare two version strings.

    Returns:
        -1 if ``a < b``
         0 if ``a == b``
         1 if ``a > b``
    """
    va = parse_version(a)
    vb = parse_version(b)
    if va < vb:
        return -1
    if va > vb:
        return 1
    return 0
