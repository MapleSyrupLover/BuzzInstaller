"""Tests for the shared buzzcommon package (no network access needed)."""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from buzzcommon import install
from buzzcommon.versions import (
    is_newer,
    normalize_version,
    parse_version,
    same_version,
)


def test_parse_version_accepts_prefixes():
    assert normalize_version("v1.2.3") == "1.2.3"
    assert parse_version("1.2") == parse_version("1.2.0")
    assert str(parse_version("1.2.3.4")) == "1.2.3.4"


def test_is_newer_semantic():
    assert is_newer("1.10.0", "1.9.0")
    assert not is_newer("1.0.0", "1.0.0")
    assert not is_newer("1.0.0", "1.0.1")
    assert same_version("1.2", "1.2.0")


def test_install_bundle_replaces_and_preserves_user_data():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_dir = root / "BuzzInstaller"
        install_dir.mkdir()
        (install_dir / "BuzzInstaller.exe").write_bytes(b"MZ old")
        (install_dir / "old-file.dll").write_bytes(b"old")
        (install_dir / "config.json").write_text("{}", encoding="utf-8")
        (install_dir / "logs").mkdir()
        (install_dir / "logs" / "app.log").write_text("keep", encoding="utf-8")

        bundle = root / "BuzzInstaller.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("BuzzInstaller.exe", b"MZ new")
            archive.writestr("_internal/runtime.dll", b"DLL")

        updater = root / "BuzzInstallerUpdater.exe"
        updater.write_bytes(b"MZ updater")

        exe = install.install_bundle(
            bundle,
            install_dir,
            extra_files={install.UPDATER_NAME: updater},
            data_dir=install_dir,
        )

        assert exe == install_dir / "BuzzInstaller.exe"
        assert exe.read_bytes() == b"MZ new"
        assert (install_dir / "BuzzInstallerUpdater.exe").read_bytes() == b"MZ updater"
        assert (install_dir / "config.json").read_text(encoding="utf-8") == "{}"
        assert (install_dir / "logs" / "app.log").read_text(encoding="utf-8") == "keep"
        assert not (install_dir / "old-file.dll").exists()


def test_install_bundle_rolls_back_on_invalid_bundle():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_dir = root / "BuzzInstaller"
        install_dir.mkdir()
        (install_dir / "BuzzInstaller.exe").write_bytes(b"MZ old")
        bundle = root / "broken.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("_internal/runtime.dll", b"DLL")

        try:
            install.install_bundle(bundle, install_dir, data_dir=install_dir)
        except install.InstallError:
            pass
        else:
            raise AssertionError("expected InstallError")

        assert (install_dir / "BuzzInstaller.exe").read_bytes() == b"MZ old"


def test_installed_version_uses_marker_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        install_dir = Path(tmp)
        (install_dir / "BuzzInstaller.exe").write_bytes(b"")
        (install_dir / "installed-version.txt").write_text("1.6.0", encoding="utf-8")
        assert install.installed_version(install_dir) == "1.6.0"


TESTS = [
    test_parse_version_accepts_prefixes,
    test_is_newer_semantic,
    test_install_bundle_replaces_and_preserves_user_data,
    test_install_bundle_rolls_back_on_invalid_bundle,
    test_installed_version_uses_marker_fallback,
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
