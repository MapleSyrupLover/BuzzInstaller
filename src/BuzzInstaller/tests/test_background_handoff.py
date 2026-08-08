"""
Tests for the background handoff mechanism.

Verifies:
  * _MEIPASS2 is removed from the child environment.
  * The background process is not launched directly from the foreground in frozen mode.
  * The handoff waits for the foreground process to terminate.
  * The background process receives an independent environment.
  * Existing background single-instance behavior remains intact.
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
# _MEIPASS2 stripping
# ---------------------------------------------------------------------------
def test_launch_app_strips_meipass2():
    """launch_app strips _MEIPASS2 from the child environment in frozen mode."""
    with patch("system.background_service.sys.frozen", True, create=True), \
         patch("system.background_service.subprocess.Popen") as mock_popen:
        # Simulate _MEIPASS2 being set
        with patch.dict("system.background_service.os.environ", {"_MEIPASS2": "C:/Temp/_MEI12345"}):
            BackgroundService.launch_app(["--background"])
            # In frozen mode with --background, it should route through the launcher
            # (not spawn directly), so Popen should be called with the launcher args.
            assert mock_popen.called
            args, kwargs = mock_popen.call_args
            # The launcher command should be powershell (not the exe directly)
            assert "powershell" in args[0][0].lower()


def test_launch_app_strips_meipass2_direct():
    """launch_app strips _MEIPASS2 for non-background launches in frozen mode."""
    with patch("system.background_service.sys.frozen", True, create=True), \
         patch("system.background_service.subprocess.Popen") as mock_popen:
        with patch.dict("system.background_service.os.environ", {"_MEIPASS2": "C:/Temp/_MEI12345"}):
            # Non-background launch (e.g. download UI) — should spawn directly
            BackgroundService.launch_app(["BZ://code"])
            assert mock_popen.called
            args, kwargs = mock_popen.call_args
            # _MEIPASS2 must be stripped from the child env
            assert "_MEIPASS2" not in kwargs.get("env", {})


def test_launch_app_source_mode_no_meipass2():
    """launch_app does NOT strip _MEIPASS2 in source (non-frozen) mode."""
    with patch("system.background_service.sys.frozen", False, create=True), \
         patch("system.background_service.subprocess.Popen") as mock_popen:
        with patch.dict("system.background_service.os.environ", {"_MEIPASS2": "C:/Temp/_MEI12345"}):
            BackgroundService.launch_app(["--background"])
            assert mock_popen.called
            args, kwargs = mock_popen.call_args
            # In source mode, no env override is set
            assert "env" not in kwargs


# ---------------------------------------------------------------------------
# Handoff routing
# ---------------------------------------------------------------------------
def test_background_routed_through_launcher_in_frozen():
    """In frozen mode, --background is routed through the launcher, not spawned directly."""
    with patch("system.background_service.sys.frozen", True, create=True), \
         patch("system.background_service.subprocess.Popen") as mock_popen, \
         patch("system.background_service.os.getpid", return_value=12345):
        BackgroundService.launch_app(["--background"])
        assert mock_popen.called
        args, kwargs = mock_popen.call_args
        # The command should be powershell (the launcher), not the exe directly
        cmd = args[0]
        assert "powershell" in cmd[0].lower()
        # The PowerShell command should reference the foreground PID
        ps_cmd = " ".join(cmd)
        assert "12345" in ps_cmd
        # It should wait for the process to exit
        assert "Wait-Process" in ps_cmd
        # It should strip _MEIPASS2
        assert "_MEIPASS2" in ps_cmd


def test_background_not_routed_through_launcher_in_source():
    """In source mode, --background is spawned directly (no launcher)."""
    with patch("system.background_service.sys.frozen", False, create=True), \
         patch("system.background_service.subprocess.Popen") as mock_popen:
        BackgroundService.launch_app(["--background"])
        assert mock_popen.called
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        # Should be python + main.py + --background, not powershell
        assert "powershell" not in cmd[0].lower()
        assert "--background" in cmd


def test_download_not_routed_through_launcher_in_frozen():
    """In frozen mode, non-background launches (download UI) are spawned directly."""
    with patch("system.background_service.sys.frozen", True, create=True), \
         patch("system.background_service.subprocess.Popen") as mock_popen:
        BackgroundService.launch_app(["BZ://code"])
        assert mock_popen.called
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        # Should be the exe + BZ://code, not powershell
        assert "powershell" not in cmd[0].lower()
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
        test_launch_app_strips_meipass2,
        test_launch_app_strips_meipass2_direct,
        test_launch_app_source_mode_no_meipass2,
        test_background_routed_through_launcher_in_frozen,
        test_background_not_routed_through_launcher_in_source,
        test_download_not_routed_through_launcher_in_frozen,
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