"""
Tests for the background process mechanism.

Verifies:
  * The background process is launched directly (no onefile launcher).
  * Existing background single-instance behavior remains intact.
  * The command used for frozen (onedir) mode is the exe + args.
  * The command used for source mode is python + main.py + args.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure the main package is importable
_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from system.background_service import BackgroundService


# ---------------------------------------------------------------------------
# Process spawning
# ---------------------------------------------------------------------------
def test_launch_app_frozen_uses_exe():
    """launch_app uses the bundled exe in frozen (onedir) mode."""
    with patch("system.background_service.sys.frozen", True, create=True), \
         patch("system.background_service.sys.executable", "C:/BuzzInstaller/BuzzInstaller.exe", create=True), \
         patch("system.background_service.subprocess.Popen") as mock_popen:
        BackgroundService.launch_app(["--background"])
        assert mock_popen.called
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        # Should be the exe + --background
        assert cmd[0].lower().endswith("buzzinstaller.exe")
        assert "--background" in cmd


def test_launch_app_source_uses_python():
    """launch_app uses python + main.py in source mode."""
    with patch("system.background_service.sys.frozen", False, create=True), \
         patch("system.background_service.subprocess.Popen") as mock_popen:
        BackgroundService.launch_app(["--background"])
        assert mock_popen.called
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        # Should be python + main.py + --background
        assert cmd[0].lower().endswith("python.exe") or cmd[0].lower().endswith("python")
        assert "--background" in cmd
        assert any("main.py" in str(c) for c in cmd)


def test_launch_app_download_frozen():
    """launch_app for a download uses the exe + BZ://code in frozen mode."""
    with patch("system.background_service.sys.frozen", True, create=True), \
         patch("system.background_service.sys.executable", "C:/BuzzInstaller/BuzzInstaller.exe", create=True), \
         patch("system.background_service.subprocess.Popen") as mock_popen:
        BackgroundService.launch_app(["BZ://code"])
        assert mock_popen.called
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert cmd[0].lower().endswith("buzzinstaller.exe")
        assert "BZ://code" in cmd


def test_launch_app_download_source():
    """launch_app for a download uses python + main.py + BZ://code in source mode."""
    with patch("system.background_service.sys.frozen", False, create=True), \
         patch("system.background_service.subprocess.Popen") as mock_popen:
        BackgroundService.launch_app(["BZ://code"])
        assert mock_popen.called
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert "BZ://code" in cmd


# ---------------------------------------------------------------------------
# Single-instance behavior
# ---------------------------------------------------------------------------
def test_is_background_running():
    """is_background_running returns True when the mutex exists."""
    with patch("system.background_service.sys.platform", "win32"), \
         patch("system.background_service.ctypes.windll.kernel32.OpenMutexW") as mock_open:
        mock_open.return_value = 12345  # non-zero handle
        assert BackgroundService.is_background_running() is True
        mock_open.assert_called_once()


def test_is_background_running_false():
    """is_background_running returns False when the mutex does not exist."""
    with patch("system.background_service.sys.platform", "win32"), \
         patch("system.background_service.ctypes.windll.kernel32.OpenMutexW") as mock_open:
        mock_open.return_value = 0  # no handle
        assert BackgroundService.is_background_running() is False


if __name__ == "__main__":
    # Simple test runner
    tests = [
        test_launch_app_frozen_uses_exe,
        test_launch_app_source_uses_python,
        test_launch_app_download_frozen,
        test_launch_app_download_source,
        test_is_background_running,
        test_is_background_running_false,
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
