"""
Tests for the version comparison logic.
"""

from __future__ import annotations

import sys
import os

# Ensure the installer package is importable
_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from version import (
    parse_version, Version, VersionError, compare_versions, normalize_version,
)


def test_equal_versions():
    """1.0.0 == 1.0.0"""
    assert compare_versions("1.0.0", "1.0.0") == 0
    assert parse_version("1.0.0") == parse_version("1.0.0")


def test_less_than():
    """1.0.0 < 1.1.0"""
    assert compare_versions("1.0.0", "1.1.0") == -1
    assert parse_version("1.0.0") < parse_version("1.1.0")


def test_patch_less_than():
    """1.9.0 < 1.10.0 (numeric, not string comparison)"""
    assert compare_versions("1.9.0", "1.10.0") == -1
    assert parse_version("1.9.0") < parse_version("1.10.0")


def test_v_prefix_equality():
    """v1.2.0 == 1.2.0"""
    assert compare_versions("v1.2.0", "1.2.0") == 0
    assert parse_version("v1.2.0") == parse_version("1.2.0")


def test_major_newer():
    """2.0.0 > 1.9.9"""
    assert compare_versions("2.0.0", "1.9.9") == 1
    assert parse_version("2.0.0") > parse_version("1.9.9")


def test_parse_version_forms():
    """Various version string forms parse correctly."""
    assert parse_version("v1.2.3") == Version(1, 2, 3)
    assert parse_version("1.2.3") == Version(1, 2, 3)
    assert parse_version("v1.2") == Version(1, 2, 0)
    assert parse_version("1.2") == Version(1, 2, 0)
    assert parse_version("v1") == Version(1, 0, 0)
    assert parse_version("1") == Version(1, 0, 0)


def test_normalize_version():
    """Normalization strips the v prefix."""
    assert normalize_version("v1.2.3") == "1.2.3"
    assert normalize_version("1.2.3") == "1.2.3"


def test_invalid_version():
    """Invalid version strings raise VersionError."""
    for bad in ("", "abc", "1.2.3.4", "v", "1..2", "1.2.x"):
        try:
            parse_version(bad)
            assert False, f"Expected VersionError for {bad!r}"
        except VersionError:
            pass


def test_version_comparison_operators():
    """All comparison operators work correctly."""
    v1 = parse_version("1.0.0")
    v2 = parse_version("1.0.1")
    assert v1 < v2
    assert v1 <= v2
    assert v2 > v1
    assert v2 >= v1
    assert v1 != v2
    assert v1 == parse_version("1.0.0")


def test_compare_versions_returns_int():
    """compare_versions returns -1, 0, or 1."""
    assert compare_versions("1.0.0", "1.0.0") in (-1, 0, 1)
    assert compare_versions("1.0.0", "1.0.0") == 0
    assert compare_versions("1.0.0", "1.1.0") == -1
    assert compare_versions("1.1.0", "1.0.0") == 1


if __name__ == "__main__":
    # Simple test runner
    tests = [
        test_equal_versions,
        test_less_than,
        test_patch_less_than,
        test_v_prefix_equality,
        test_major_newer,
        test_parse_version_forms,
        test_normalize_version,
        test_invalid_version,
        test_version_comparison_operators,
        test_compare_versions_returns_int,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL: {t.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR: {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)