"""
Tests for the auto-copied BZ:// link lifetime (Settings -> Clipboard).

Verifies:
  * Stored values are validated; unknown modes fall back to "never".
  * "never" leaves the link on the clipboard.
  * "timeout" clears the link after the configured delay.
  * A newer copy cancels a pending expiry.
  * Expiry never removes content that was replaced by another app.
  * "first_paste" falls back to the timeout when no hook is available.
  * "first_paste" arms and disarms the keyboard watcher (Windows only).

The tests run with Qt's offscreen platform so they never touch the real
system clipboard.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure the main package is importable
_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv[:1])

from system import clipboard_service as cs  # noqa: E402
from system.clipboard_service import clipboard_service  # noqa: E402


def _pump(seconds: float) -> None:
    """Let Qt timers fire without blocking the GUI thread."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _APP.processEvents()
        time.sleep(0.01)


@contextmanager
def _auto_mode(mode: str, seconds: int = 1):
    """Force copy_auto() to behave as if these settings were stored."""
    with patch.object(
        cs.ClipboardService,
        "_auto_copy_settings",
        staticmethod(lambda: (mode, seconds)),
    ):
        yield


def _reset() -> None:
    """Cancel timers/hooks and empty the test clipboard."""
    try:
        clipboard_service._dispatch(clipboard_service._cancel_expiry)
        clipboard_service.clear()
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------
def test_settings_unknown_mode_falls_back_to_never():
    cfg = {"auto_copy_clear_mode": "bogus", "auto_copy_clear_seconds": 5}
    with patch("config.config_manager.load_config", return_value=cfg):
        assert clipboard_service._auto_copy_settings() == ("never", 5)


def test_settings_negative_delay_is_clamped():
    cfg = {"auto_copy_clear_mode": "timeout", "auto_copy_clear_seconds": -5}
    with patch("config.config_manager.load_config", return_value=cfg):
        assert clipboard_service._auto_copy_settings() == ("timeout", 1)


def test_settings_invalid_delay_uses_defaults():
    cfg = {"auto_copy_clear_mode": "timeout", "auto_copy_clear_seconds": "abc"}
    with patch("config.config_manager.load_config", return_value=cfg):
        assert clipboard_service._auto_copy_settings() == ("never", 10)


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------
def test_never_mode_keeps_link():
    with _auto_mode("never"):
        assert clipboard_service.copy_auto("BZ://never")
    _pump(0.3)
    assert clipboard_service.read() == "BZ://never"
    assert clipboard_service._expiry_timer is None


def test_timeout_clears_link():
    with _auto_mode("timeout", 1):
        assert clipboard_service.copy_auto("BZ://timeout")
    assert clipboard_service.read() == "BZ://timeout"
    _pump(1.4)
    assert clipboard_service.read() is None


def test_timeout_spares_newer_copy():
    with _auto_mode("timeout", 1):
        assert clipboard_service.copy_auto("BZ://old")
    _pump(0.2)
    assert clipboard_service.copy("replacement")
    _pump(1.2)
    assert clipboard_service.read() == "replacement"


def test_expiry_ignores_replaced_clipboard():
    with _auto_mode("timeout", 5):
        assert clipboard_service.copy_auto("BZ://expiring")
    generation = clipboard_service._current_generation()
    sequence = cs._qt_clipboard_sequence_number()

    # Another app replaces the clipboard without notifying us.
    clipboard_service._dispatch(lambda: cs._qt_clipboard_set("other-app"))
    clipboard_service._dispatch(
        lambda: clipboard_service._expire_if_current(
            "BZ://expiring", generation, sequence
        )
    )
    assert clipboard_service.read() == "other-app"


def test_first_paste_falls_back_to_timeout_without_hook():
    with _auto_mode("first_paste", 1), patch.object(
        cs.ClipboardService, "_install_paste_hook", lambda self: False
    ):
        assert clipboard_service.copy_auto("BZ://fallback")
    assert clipboard_service._paste_text == ""
    _pump(1.4)
    assert clipboard_service.read() is None


def test_first_paste_hook_armed_and_cancelled():
    if sys.platform != "win32":
        print("SKIP: first-paste hook is Windows-only")
        return
    try:
        with _auto_mode("first_paste", 10):
            assert clipboard_service.copy_auto("BZ://armed")
        assert clipboard_service._paste_hook, "expected a keyboard hook"
        # A newer copy must disarm the watcher.
        assert clipboard_service.copy("next-copy")
        assert clipboard_service._paste_hook is None
        assert clipboard_service._paste_text == ""
    finally:
        _reset()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def main() -> int:
    tests = [
        test_settings_unknown_mode_falls_back_to_never,
        test_settings_negative_delay_is_clamped,
        test_settings_invalid_delay_uses_defaults,
        test_never_mode_keeps_link,
        test_timeout_clears_link,
        test_timeout_spares_newer_copy,
        test_expiry_ignores_replaced_clipboard,
        test_first_paste_falls_back_to_timeout_without_hook,
        test_first_paste_hook_armed_and_cancelled,
    ]
    failed = 0
    for test in tests:
        _reset()
        try:
            test()
            print(f"PASS: {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL: {test.__name__}: {exc}")
            traceback.print_exc()
    _reset()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
