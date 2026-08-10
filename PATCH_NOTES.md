# Patch Notes

## Version: Hotkey Fix

### Fixed: Alt+F1 automatic copy not working in Discord, Notepad, and other apps

**Root cause**

When Alt+F1 was pressed, the WM_HOTKEY message fired at the moment F1 went down, while both Alt and F1 were still physically held. The hotkey service then injected Ctrl+C via keybd_event, but the target application saw Alt+Ctrl+C or F1+Ctrl+C instead of a clean Ctrl+C. Since those combinations are not copy commands, the selected text was never copied to the clipboard.

A secondary issue caused the service to read the clipboard immediately after the simulated Ctrl+C, before the target application had posted the new content. This meant it could read stale pre-copy clipboard contents and download an old BZ link instead of the newly selected one.

**Changes**

1. `src/BuzzInstaller/system/hotkey_service.py`
   - Before injecting Ctrl+C, the service now checks whether Alt is still held and sends an Alt key-up.
   - It also checks whether the hotkey key (F1) is still held and sends an F1 key-up.
   - This ensures the target application receives a clean Ctrl+C.

2. `src/BuzzInstaller/system/clipboard_service.py`
   - Added `get_sequence_number()` which returns the Win32 clipboard sequence number. This number increments every time the clipboard contents change.

3. `src/BuzzInstaller/system/hotkey_service.py`
   - After simulating Ctrl+C, the service now records the clipboard sequence number before the copy, then waits for the sequence number to change. This proves the target application actually updated the clipboard with the selected text before the service reads it.
   - Added `_wait_for_clipboard_change()` helper which polls the sequence number with retries.

**Tests**

- Added tests for Alt release before Ctrl+C injection.
- Added tests for F1 release before Ctrl+C injection.
- Added tests confirming no key release happens when keys are not held.
- All 22 hotkey service tests pass.

**How to apply**

The source fix is in place. To make it permanent in the packaged executable, rebuild with:

```
python src/BuzzInstaller/build/build_exe.py