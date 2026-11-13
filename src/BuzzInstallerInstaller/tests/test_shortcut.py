"""
Tests for the Windows shortcut (.lnk) creation logic.

These tests verify:
  * Creating a shortcut
  * Updating an existing shortcut
  * Correct target path
  * Correct working directory
  * Existing shortcut handling
  * Permission failure handling
  * Shortcut deletion
  * Ensuring unrelated files in C:\BuzzInstaller are not deleted
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure the installer package is importable
_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from shortcut import (
    create_shortcut, delete_shortcut, get_shortcut_target,
    create_start_menu_shortcut, delete_start_menu_shortcut,
    start_menu_shortcut_path,
)
from installer import (
    create_shortcut_for_installed, delete_buzzinstaller,
    SHORTCUT_DIR, SHORTCUT_PATH,
)


def _make_fake_exe() -> Path:
    """Create a fake BuzzInstaller.exe in a temp dir."""
    temp_dir = Path(tempfile.mkdtemp())
    exe = temp_dir / "BuzzInstaller.exe"
    exe.write_bytes(b"MZ fake exe")
    return exe


# ---------------------------------------------------------------------------
# create_shortcut
# ---------------------------------------------------------------------------
def test_create_shortcut():
    """create_shortcut creates a real .lnk file pointing at the exe."""
    exe = _make_fake_exe()
    lnk = Path(tempfile.mkdtemp()) / "BuzzInstaller.lnk"
    assert create_shortcut(exe, lnk) is True
    assert lnk.is_file()
    # The .lnk must be a real binary shortcut, not a text file
    data = lnk.read_bytes()
    assert b"MZ" not in data[:2]  # not a copy of the exe
    assert len(data) > 0


def test_create_shortcut_target_path():
    """The shortcut target must be the actual exe path."""
    exe = _make_fake_exe()
    lnk = Path(tempfile.mkdtemp()) / "BuzzInstaller.lnk"
    assert create_shortcut(exe, lnk) is True
    target = get_shortcut_target(lnk)
    assert target is not None
    assert Path(target).resolve() == exe.resolve()


def test_create_shortcut_working_directory():
    """The shortcut working directory must be the exe's folder."""
    exe = _make_fake_exe()
    lnk = Path(tempfile.mkdtemp()) / "BuzzInstaller.lnk"
    assert create_shortcut(exe, lnk) is True
    # Verify via PowerShell that the working directory is correct
    import subprocess
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{str(lnk).replace(chr(39), chr(39)+chr(39))}'); "
        "Write-Output $sc.WorkingDirectory"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0
    assert Path(result.stdout.strip()).resolve() == exe.parent.resolve()


def test_create_shortcut_updates_existing():
    """Creating a shortcut over an existing one updates the target."""
    exe1 = _make_fake_exe()
    exe2 = _make_fake_exe()
    lnk = Path(tempfile.mkdtemp()) / "BuzzInstaller.lnk"
    assert create_shortcut(exe1, lnk) is True
    assert get_shortcut_target(lnk) == str(exe1)
    # Update to point at exe2
    assert create_shortcut(exe2, lnk) is True
    assert get_shortcut_target(lnk) == str(exe2)


def test_create_shortcut_missing_exe():
    """create_shortcut returns False if the exe does not exist."""
    lnk = Path(tempfile.mkdtemp()) / "BuzzInstaller.lnk"
    assert create_shortcut(Path("C:/nonexistent/BuzzInstaller.exe"), lnk) is False
    assert not lnk.exists()


def test_create_shortcut_permission_denied():
    """create_shortcut returns False on permission failure."""
    exe = _make_fake_exe()
    lnk = Path(tempfile.mkdtemp()) / "BuzzInstaller.lnk"
    with patch("shortcut.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Access denied")
        assert create_shortcut(exe, lnk) is False


# ---------------------------------------------------------------------------
# delete_shortcut
# ---------------------------------------------------------------------------
def test_delete_shortcut():
    """delete_shortcut removes the .lnk file."""
    exe = _make_fake_exe()
    lnk = Path(tempfile.mkdtemp()) / "BuzzInstaller.lnk"
    assert create_shortcut(exe, lnk) is True
    assert lnk.exists()
    assert delete_shortcut(lnk) is True
    assert not lnk.exists()


def test_delete_shortcut_missing():
    """delete_shortcut returns True if the shortcut does not exist."""
    lnk = Path(tempfile.mkdtemp()) / "BuzzInstaller.lnk"
    assert delete_shortcut(lnk) is True


# ---------------------------------------------------------------------------
# get_shortcut_target
# ---------------------------------------------------------------------------
def test_get_shortcut_target_missing():
    """get_shortcut_target returns None for a missing shortcut."""
    lnk = Path(tempfile.mkdtemp()) / "BuzzInstaller.lnk"
    assert get_shortcut_target(lnk) is None


# ---------------------------------------------------------------------------
# create_shortcut_for_installed (installer integration)
# ---------------------------------------------------------------------------
def test_create_shortcut_for_installed():
    """create_shortcut_for_installed creates the shortcut for the installed exe."""
    exe = _make_fake_exe()
    with patch("installer.get_installed_exe_path", return_value=exe), \
         patch("installer.SHORTCUT_PATH", Path(tempfile.mkdtemp()) / "BuzzInstaller.lnk") as mock_lnk:
        assert create_shortcut_for_installed() is True
        assert mock_lnk.is_file()


def test_create_shortcut_for_installed_missing_exe():
    """create_shortcut_for_installed returns False if exe is missing."""
    with patch("installer.get_installed_exe_path",
               return_value=Path("C:/nonexistent/BuzzInstaller.exe")):
        assert create_shortcut_for_installed() is False


# ---------------------------------------------------------------------------
# delete_buzzinstaller shortcut cleanup
# ---------------------------------------------------------------------------
def test_delete_buzzinstaller_removes_shortcut():
    """delete_buzzinstaller removes the shortcut."""
    exe = _make_fake_exe()
    lnk_dir = Path(tempfile.mkdtemp())
    lnk = lnk_dir / "BuzzInstaller.lnk"
    assert create_shortcut(exe, lnk) is True

    with patch("installer.get_installed_exe_path", return_value=exe), \
         patch("installer.get_install_dir", return_value=exe.parent), \
         patch("installer.SHORTCUT_PATH", lnk), \
         patch("installer.SHORTCUT_DIR", lnk_dir), \
         patch("installer.is_process_running", return_value=False), \
         patch("installer.metadata_is_path_install", return_value=False), \
         patch("installer._delete_start_menu_shortcut"), \
         patch("installer.clear_metadata"):
        assert delete_buzzinstaller() is True
    assert not lnk.exists()


def test_delete_buzzinstaller_keeps_unrelated_files():
    """delete_buzzinstaller does NOT delete C:\BuzzInstaller if it has unrelated files."""
    exe = _make_fake_exe()
    lnk_dir = Path(tempfile.mkdtemp())
    lnk = lnk_dir / "BuzzInstaller.lnk"
    assert create_shortcut(exe, lnk) is True
    # Add an unrelated file
    unrelated = lnk_dir / "unrelated.txt"
    unrelated.write_text("keep me")

    with patch("installer.get_installed_exe_path", return_value=exe), \
         patch("installer.get_install_dir", return_value=exe.parent), \
         patch("installer.SHORTCUT_PATH", lnk), \
         patch("installer.SHORTCUT_DIR", lnk_dir), \
         patch("installer.is_process_running", return_value=False), \
         patch("installer.metadata_is_path_install", return_value=False), \
         patch("installer._delete_start_menu_shortcut"), \
         patch("installer.clear_metadata"):
        assert delete_buzzinstaller() is True
    assert not lnk.exists()
    assert unrelated.exists()  # unrelated file preserved
    assert lnk_dir.exists()    # directory not removed (not empty)


def test_start_menu_shortcut_path_uses_appdata():
    """The Start Menu shortcut lives under the current user's Programs folder."""
    temp_dir = Path(tempfile.mkdtemp())
    with patch.dict(os.environ, {"APPDATA": str(temp_dir)}):
        path = start_menu_shortcut_path()
    assert path is not None
    assert path.name == "BuzzInstaller.lnk"
    assert path.parent.name == "Programs"
    assert path.parent.parent.name == "Start Menu"


def test_create_and_delete_start_menu_shortcut():
    """The Start Menu shortcut can be created and removed."""
    exe = _make_fake_exe()
    temp_dir = Path(tempfile.mkdtemp())
    with patch.dict(os.environ, {"APPDATA": str(temp_dir)}):
        lnk = create_start_menu_shortcut(exe)
        assert lnk is not None
        assert lnk.is_file()
        assert get_shortcut_target(lnk) == str(exe)
        assert delete_start_menu_shortcut() is True
        assert not lnk.exists()


def test_create_start_menu_shortcut_missing_exe():
    """No Start Menu shortcut is made when the exe is missing."""
    temp_dir = Path(tempfile.mkdtemp())
    with patch.dict(os.environ, {"APPDATA": str(temp_dir)}):
        assert create_start_menu_shortcut(
            Path("C:/nonexistent/BuzzInstaller.exe")
        ) is None


if __name__ == "__main__":
    # Simple test runner
    tests = [
        test_create_shortcut,
        test_create_shortcut_target_path,
        test_create_shortcut_working_directory,
        test_create_shortcut_updates_existing,
        test_create_shortcut_missing_exe,
        test_create_shortcut_permission_denied,
        test_delete_shortcut,
        test_delete_shortcut_missing,
        test_get_shortcut_target_missing,
        test_create_shortcut_for_installed,
        test_create_shortcut_for_installed_missing_exe,
        test_start_menu_shortcut_path_uses_appdata,
        test_create_and_delete_start_menu_shortcut,
        test_create_start_menu_shortcut_missing_exe,
        test_delete_buzzinstaller_removes_shortcut,
        test_delete_buzzinstaller_keeps_unrelated_files,
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