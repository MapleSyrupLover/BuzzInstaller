"""
Tests for HotkeyService — the Alt+F1 Quick Receive flow.

Verifies:
  1. Alt+F1 triggers copy simulation (Ctrl+C is simulated).
  2. Ctrl+C is simulated BEFORE clipboard retrieval.
  3. ClipboardService is used (not raw Win32).
  4. A selected BZ:// link is detected without manual Ctrl+C.
  5. Existing clipboard-based behavior still works (fallback).
  6. No duplicate download occurs.
  7. Modifier keys are released correctly.
  8. Copy simulation failure is handled safely.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch, MagicMock

# Ensure the main package is importable
_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from system.hotkey_service import HotkeyService, _BZ_LINK_RE


def _make_service():
    """Create a HotkeyService with mocked user32 and clipboard."""
    svc = HotkeyService()
    svc._user32 = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# 1. Alt+F1 triggers copy simulation
# ---------------------------------------------------------------------------
def test_hotkey_triggers_copy_simulation():
    """_on_hotkey calls _simulate_copy_and_read_with_retry."""
    svc = _make_service()
    with patch.object(svc, "_simulate_copy_and_read_with_retry",
                      return_value="BZ://abc12345") as mock_copy, \
         patch.object(svc, "_find_bz_code", return_value="abc12345") as mock_find, \
         patch.object(svc, "_on_download") as mock_dl:
        svc._on_hotkey()
        mock_copy.assert_called_once()
        mock_find.assert_called_with("BZ://abc12345")
        mock_dl.assert_called_once_with("abc12345")


# ---------------------------------------------------------------------------
# 2. Ctrl+C is simulated BEFORE clipboard retrieval
# ---------------------------------------------------------------------------
def test_ctrl_c_before_clipboard_read():
    """_simulate_copy_and_read_with_retry simulates Ctrl+C before read."""
    svc = _make_service()
    order = []

    def fake_ctrl_c():
        order.append("ctrl_c")

    def fake_save():
        order.append("save")
        return {}

    def fake_restore(_snap):
        order.append("restore")
        return True

    def fake_read():
        order.append("read")
        return "BZ://abc12345"

    with patch.object(svc, "_simulate_ctrl_c", side_effect=fake_ctrl_c), \
         patch("system.hotkey_service.clipboard_service.save_full",
               side_effect=fake_save), \
         patch("system.hotkey_service.clipboard_service.restore_full",
               side_effect=fake_restore), \
         patch("system.hotkey_service.clipboard_service.read",
               side_effect=fake_read):
        text = svc._simulate_copy_and_read_with_retry(attempts=1, delay_ms=0)
        assert text == "BZ://abc12345"
        # Ctrl+C must come before the clipboard read
        assert order.index("ctrl_c") < order.index("read")
        assert order.index("save") < order.index("ctrl_c")


# ---------------------------------------------------------------------------
# 3. ClipboardService is used
# ---------------------------------------------------------------------------
def test_uses_clipboard_service():
    """The service uses clipboard_service, not raw Win32 clipboard calls."""
    svc = _make_service()
    with patch("system.hotkey_service.clipboard_service.save_full",
               return_value={}) as mock_save, \
         patch("system.hotkey_service.clipboard_service.read",
               return_value="BZ://abc12345") as mock_read, \
         patch.object(svc, "_simulate_ctrl_c") as mock_ctrl_c:
        svc._simulate_copy_and_read_with_retry(attempts=1, delay_ms=0)
        mock_save.assert_called_once()
        mock_read.assert_called_once()
        mock_ctrl_c.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Selected BZ:// link detected without manual Ctrl+C
# ---------------------------------------------------------------------------
def test_selected_bz_link_detected_without_manual_copy():
    """Full flow: hotkey simulates Ctrl+C, reads clipboard, finds BZ link."""
    svc = _make_service()
    downloaded = []

    def on_download(code):
        downloaded.append(code)

    svc._on_download = on_download

    # Simulated Ctrl+C causes clipboard to contain the selected text.
    clip = {"text": "Here is a link: BZ://afe80ff4p12u"}

    def fake_ctrl_c():
        # This effectively "copies" the selection to the clipboard.
        clip["text"] = "BZ://afe80ff4p12u"

    def fake_read():
        return clip["text"]

    with patch.object(svc, "_simulate_ctrl_c", side_effect=fake_ctrl_c), \
         patch("system.hotkey_service.clipboard_service.save_full",
               return_value={}), \
         patch("system.hotkey_service.clipboard_service.restore_full",
               return_value=True), \
         patch("system.hotkey_service.clipboard_service.read",
               side_effect=fake_read):
        svc._on_hotkey()

    assert downloaded == ["afe80ff4p12u"]


# ---------------------------------------------------------------------------
# 5. Existing clipboard-based behavior still works (fallback)
# ---------------------------------------------------------------------------
def test_clipboard_fallback_when_copy_empty():
    """If copy produces nothing, falls back to existing clipboard."""
    svc = _make_service()
    downloaded = []
    svc._on_download = lambda code: downloaded.append(code)

    # First read (after Ctrl+C) returns None, fallback read returns the link.
    reads = iter([None, "BZ://abc12345"])

    def fake_read():
        return next(reads, None)

    with patch.object(svc, "_simulate_ctrl_c"), \
         patch("system.hotkey_service.clipboard_service.save_full",
               return_value={}), \
         patch("system.hotkey_service.clipboard_service.restore_full",
               return_value=True), \
         patch("system.hotkey_service.clipboard_service.read",
               side_effect=fake_read):
        svc._on_hotkey()

    assert downloaded == ["abc12345"]


# ---------------------------------------------------------------------------
# 6. No duplicate download occurs
# ---------------------------------------------------------------------------
def test_no_duplicate_download():
    """Hotkey fires once → download called exactly once."""
    svc = _make_service()
    calls = []
    svc._on_download = lambda code: calls.append(code)

    with patch.object(svc, "_simulate_copy_and_read_with_retry",
                      return_value="BZ://abc12345") as mock_copy, \
         patch.object(svc, "_find_bz_code",
                      side_effect=["abc12345", "abc12345"]) as mock_find:
        svc._on_hotkey()
        assert len(calls) == 1
        # Fallback path not triggered => find called once.
        assert mock_find.call_count == 1


# ---------------------------------------------------------------------------
# 7. Modifier keys are released correctly
# ---------------------------------------------------------------------------
def test_keys_released_on_sendinput_failure():
    """_release_keys_safely sends key-up for Ctrl and C."""
    svc = _make_service()
    svc._user32.SendInput.return_value = 4  # all sent

    sent_inputs = []

    def _capture_sendinput(n, inputs, size):
        sent_inputs.append((n, inputs))
        return 4

    svc._user32.SendInput.side_effect = _capture_sendinput
    svc._release_keys_safely()

    # Must be 2 inputs: C up, Ctrl up
    assert len(sent_inputs) == 1
    n, inputs = sent_inputs[0]
    assert n == 2
    # Both are key-up events
    from system.hotkey_service import KEYEVENTF_KEYUP
    assert inputs[0]._input.ki.dwFlags == KEYEVENTF_KEYUP
    assert inputs[1]._input.ki.dwFlags == KEYEVENTF_KEYUP


def test_keys_released_when_simulate_fails():
    """When _simulate_ctrl_c raises, _release_keys_safely is called."""
    svc = _make_service()

    with patch.object(svc, "_simulate_ctrl_c", side_effect=RuntimeError("boom")), \
         patch.object(svc, "_release_keys_safely") as mock_release:
        result = svc._simulate_copy_and_read_with_retry()
        mock_release.assert_called_once()
        assert result is None


def test_keybd_event_used_primarily():
    """_simulate_ctrl_c uses keybd_event as the primary mechanism."""
    svc = _make_service()
    svc._user32.keybd_event.return_value = None  # keybd_event returns void
    svc._user32.GetAsyncKeyState.return_value = 0  # Alt/F1 NOT held
    svc._vk = 0x70  # F1

    svc._simulate_ctrl_c()
    # keybd_event should be called 4 times (Ctrl down, C down, C up, Ctrl up)
    assert svc._user32.keybd_event.call_count == 4
    # SendInput should NOT be called when keybd_event succeeds
    svc._user32.SendInput.assert_not_called()


def test_alt_released_before_ctrl_c_when_held():
    """When Alt is physically held, keybd_event sends Alt key-up first."""
    svc = _make_service()
    svc._user32.keybd_event.return_value = None
    svc._user32.GetAsyncKeyState.return_value = 0x8000  # Alt AND F1 held
    svc._vk = 0x70  # F1

    svc._simulate_ctrl_c()

    # keybd_event called 6 times: Alt up, F1 up, Ctrl down, C down, C up, Ctrl up
    assert svc._user32.keybd_event.call_count == 6
    # First call must be Alt key-up
    from system.hotkey_service import VK_MENU, KEYEVENTF_KEYUP
    first_call = svc._user32.keybd_event.call_args_list[0]
    assert first_call.args[0] == VK_MENU
    assert first_call.args[2] == KEYEVENTF_KEYUP
    # Second call must be F1 key-up
    second_call = svc._user32.keybd_event.call_args_list[1]
    assert second_call.args[0] == 0x70  # VK_F1
    assert second_call.args[2] == KEYEVENTF_KEYUP


def test_alt_not_released_when_not_held():
    """When Alt is NOT held, no Alt key-up is sent."""
    svc = _make_service()
    svc._user32.keybd_event.return_value = None
    svc._user32.GetAsyncKeyState.return_value = 0  # Alt/F1 NOT held
    svc._vk = 0x70  # F1

    svc._simulate_ctrl_c()

    # keybd_event called exactly 4 times (no Alt/F1 release)
    assert svc._user32.keybd_event.call_count == 4
    from system.hotkey_service import VK_MENU
    for call in svc._user32.keybd_event.call_args_list:
        assert call.args[0] != VK_MENU
        assert call.args[0] != 0x70


def test_sendinput_fallback_when_keybd_event_fails():
    """When keybd_event raises, SendInput is used as fallback."""
    svc = _make_service()
    svc._user32.keybd_event.side_effect = OSError("keybd_event failed")
    svc._user32.SendInput.return_value = 4  # SendInput succeeds
    svc._user32.GetAsyncKeyState.return_value = 0  # Alt NOT held

    svc._simulate_ctrl_c()
    # SendInput called at least twice: once from _release_keys_safely,
    # once from the SendInput fallback within _simulate_ctrl_c.
    assert svc._user32.SendInput.call_count >= 2


def test_sendinput_failure_releases_keys():
    """When SendInput returns <4, keys are released and error logged."""
    svc = _make_service()
    svc._user32.keybd_event.side_effect = OSError("keybd_event failed")
    svc._user32.SendInput.return_value = 2  # partial
    svc._user32.GetAsyncKeyState.return_value = 0  # Alt NOT held

    with patch.object(svc, "_release_keys_safely") as mock_release:
        try:
            svc._simulate_ctrl_c()
            assert False, "Expected RuntimeError"
        except RuntimeError:
            pass
        # Called twice: once after keybd_event fails, once after SendInput fails
        assert mock_release.call_count == 2


# ---------------------------------------------------------------------------
# 8. Copy simulation failure is handled safely
# ---------------------------------------------------------------------------
def test_copy_failure_does_not_crash():
    """_simulate_copy_and_read_with_retry returns None on failure, no raise."""
    svc = _make_service()
    svc._user32.keybd_event.side_effect = OSError("keybd_event failed")
    svc._user32.SendInput.side_effect = OSError("SendInput failed")

    # keybd_event fails, SendInput fails → returns None, no crash
    result = svc._simulate_copy_and_read_with_retry()
    assert result is None


def test_clipboard_read_failure_does_not_crash():
    """Clipboard read failure returns None, no raise."""
    svc = _make_service()

    with patch("system.hotkey_service.clipboard_service.save_full",
               return_value={}), \
         patch("system.hotkey_service.clipboard_service.read",
               side_effect=OSError("clipboard locked")):
        result = svc._simulate_copy_and_read_with_retry(attempts=2, delay_ms=0)
        assert result is None


# ---------------------------------------------------------------------------
# BZ:// link detection
# ---------------------------------------------------------------------------
def test_find_bz_code_exact():
    """Exact BZ:// link is detected."""
    svc = _make_service()
    assert svc._find_bz_code("BZ://abc12345") == "abc12345"


def test_find_bz_code_within_text():
    """BZ:// link within surrounding text is detected."""
    svc = _make_service()
    assert svc._find_bz_code("Check this out: BZ://abc12345") == "abc12345"


def test_find_bz_code_uppercase():
    """Uppercase BZ:// is detected (case-insensitive)."""
    svc = _make_service()
    assert svc._find_bz_code("bz://ABC12345") == "abc12345"


def test_find_bz_code_https():
    """Regular URL is not matched."""
    svc = _make_service()
    assert svc._find_bz_code("https://buzzheavier.com/abc12345") is None


def test_find_bz_code_no_match():
    """Text without a BZ:// link returns None."""
    svc = _make_service()
    assert svc._find_bz_code("no link here") is None


def test_find_bz_code_short_code():
    """Codes shorter than 8 chars are rejected."""
    svc = _make_service()
    assert svc._find_bz_code("BZ://abc123") is None


def test_find_bz_code_empty():
    """Empty text returns None."""
    svc = _make_service()
    assert svc._find_bz_code("") is None
    assert svc._find_bz_code(None) is None


if __name__ == "__main__":
    tests = [
        test_hotkey_triggers_copy_simulation,
        test_ctrl_c_before_clipboard_read,
        test_uses_clipboard_service,
        test_selected_bz_link_detected_without_manual_copy,
        test_clipboard_fallback_when_copy_empty,
        test_no_duplicate_download,
        test_keys_released_on_sendinput_failure,
        test_keys_released_when_simulate_fails,
        test_keybd_event_used_primarily,
        test_alt_released_before_ctrl_c_when_held,
        test_alt_not_released_when_not_held,
        test_sendinput_fallback_when_keybd_event_fails,
        test_sendinput_failure_releases_keys,
        test_copy_failure_does_not_crash,
        test_clipboard_read_failure_does_not_crash,
        test_find_bz_code_exact,
        test_find_bz_code_within_text,
        test_find_bz_code_uppercase,
        test_find_bz_code_https,
        test_find_bz_code_no_match,
        test_find_bz_code_short_code,
        test_find_bz_code_empty,
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
