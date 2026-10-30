"""Tests for the build helpers (no PyInstaller invocation)."""

from __future__ import annotations

import py_compile
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_ROOT / "tools"))

import build


def test_read_versions_has_both_entries():
    versions = build.read_versions()
    assert "BUZZINSTALLER_VERSION" in versions
    assert "BUZZINSTALLER_INSTALLER_VERSION" in versions
    for value in versions.values():
        assert build._version_tuple(value)[:3] == tuple(
            int(part) for part in value.split(".")[:3]
        )


def test_version_tuple_pads_missing_parts():
    assert build._version_tuple("1.3") == (1, 3, 0, 0)
    assert build._version_tuple("v1.3.0") == (1, 3, 0, 0)
    assert build._version_tuple("2.0.1.7") == (2, 0, 1, 7)


def test_write_version_file_is_valid_python():
    original = build.BUILD_ROOT
    with tempfile.TemporaryDirectory() as tmp:
        build.BUILD_ROOT = Path(tmp)
        try:
            path = build.write_version_file(
                "BuzzInstaller", "1.4.2", "Desc", "BuzzInstaller.exe"
            )
            py_compile.compile(str(path), doraise=True)
            text = path.read_text(encoding="utf-8")
        finally:
            build.BUILD_ROOT = original
    assert "filevers=(1, 4, 2, 0)" in text
    assert "prodvers=(1, 4, 2, 0)" in text
    assert "'1.4.2'" in text
    assert "'BuzzInstaller.exe'" in text


def test_zip_layout_expectations():
    """The updater relies on the exe sitting at the archive root."""
    assert build.APP_ZIP.name == "BuzzInstaller.zip"
    assert build.APP_OUT.name == "BuzzInstaller"
    assert build.INSTALLER_EXE.name == "BuzzInstallerInstaller.exe"


TESTS = [
    test_read_versions_has_both_entries,
    test_version_tuple_pads_missing_parts,
    test_write_version_file_is_valid_python,
    test_zip_layout_expectations,
]


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL: {test.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR: {test.__name__}: {exc}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
