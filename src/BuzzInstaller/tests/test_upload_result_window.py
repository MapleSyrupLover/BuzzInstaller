"""
Regression tests for the post-upload result window.

A very small file can finish uploading before the progress window is even
painted.  The result window used to be created as a local variable inside a
signal handler, so PySide6 destroyed it as soon as the handler returned — the
window flashed and disappeared (or was never seen at all).

These tests drive the real ``main.run_upload`` flow with a fake API that
returns instantly and assert that:

  * the result window is shown BEFORE the progress window is hidden,
  * a reference to it is retained for as long as it is open,
  * it is still visible (and on screen) hundreds of milliseconds later,
  * closing it releases the reference and ends the event loop,
  * a failed upload shows an error dialog that also stays open.

The tests run with Qt's offscreen platform.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_SRC = os.path.normpath(os.path.join(_PKG, ".."))
for _path in (_PKG, _SRC):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv[:1])

import main  # noqa: E402
import core.upload_service as upload_service  # noqa: E402
from core.buzzheavier_api import UploadError  # noqa: E402
from ui.progress_window import ProgressWindow  # noqa: E402
from ui.result_window import ResultWindow  # noqa: E402

_EVENTS: list[str] = []
_STATE: dict = {}


def _instrument() -> None:
    """Record the order in which the transfer windows are shown/hidden."""
    _EVENTS.clear()

    def wrap(cls, attr: str, label: str) -> None:
        original = getattr(cls, attr)

        def inner(self, *args, **kwargs):
            _EVENTS.append(label)
            return original(self, *args, **kwargs)

        setattr(cls, attr, inner)

    wrap(ProgressWindow, "show", "progress.show")
    wrap(ProgressWindow, "hide", "progress.hide")
    wrap(ResultWindow, "show", "result.show")


class _FakeAPI:
    """BuzzHeavier API stand-in that finishes an upload instantly."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def upload_file(self, path, progress_cb=None, cancelled=None):
        size = os.path.getsize(path)
        if progress_cb:
            progress_cb(size, size)
        return {
            "code": "tinycode1",
            "url": "https://buzzheavier.com/tinycode1",
            "bz_url": "BZ://tinycode1",
            "name": os.path.basename(os.fspath(path)),
            "size": size,
        }

    def close(self) -> None:
        pass


class _FailingAPI(_FakeAPI):
    def upload_file(self, path, progress_cb=None, cancelled=None):
        raise UploadError("simulated failure")


def _tiny_file(name: str = "tiny.txt") -> str:
    folder = os.path.join(tempfile.gettempdir(), "buzzinstaller-test-upload")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("hello")
    return path


def _run_flow(api_cls, *, settle_ms: int = 350) -> dict:
    """Run ``main.run_upload`` and inspect the windows it leaves open."""
    upload_service.BuzzHeavierAPI = api_cls
    main.UploadService = upload_service.UploadService

    _instrument()
    results: dict = {}
    original_retain = main._retain_window

    def retain(window):
        kept = original_retain(window)
        results["retained"] = window
        results["retained_count"] = len(main._RETAINED_WINDOWS)

        def inspect() -> None:
            results["visible_after"] = window.isVisible()
            results["type"] = type(window).__name__
            results["events"] = list(_EVENTS)
            results["count_after_open"] = len(main._RETAINED_WINDOWS)
            window.close()
            _APP.quit()

        QTimer.singleShot(settle_ms, inspect)
        return kept

    main._retain_window = retain
    try:
        results["rc"] = main.run_upload([_tiny_file()])
    finally:
        main._retain_window = original_retain
        results["count_after_close"] = len(main._RETAINED_WINDOWS)
    return results


def test_result_window_shows_and_stays() -> None:
    out = _run_flow(_FakeAPI)
    assert out.get("retained") is not None, "no window was retained"
    assert out["type"] == "ResultWindow", f"expected ResultWindow, got {out['type']}"
    assert out["visible_after"] is True, "result window disappeared"
    assert out["count_after_open"] == 1, "result window was not retained"
    assert out["count_after_close"] == 0, "retained reference was not released"


def test_result_window_shown_before_progress_hidden() -> None:
    out = _run_flow(_FakeAPI)
    events = out["events"]
    assert "result.show" in events, f"result window never shown: {events}"
    assert "progress.hide" in events, f"progress window never hidden: {events}"
    assert events.index("result.show") < events.index("progress.hide"), (
        f"progress window was hidden before the result window appeared: {events}"
    )


def test_flow_returns_after_result_closed() -> None:
    out = _run_flow(_FakeAPI, settle_ms=50)
    assert out["rc"] == 0, f"unexpected return code {out['rc']}"


def test_failed_upload_keeps_error_dialog() -> None:
    out = _run_flow(_FailingAPI)
    assert out.get("retained") is not None, "no error dialog was retained"
    assert out["type"] == "ErrorDialog", f"expected ErrorDialog, got {out['type']}"
    assert out["visible_after"] is True, "error dialog disappeared"
    assert out["count_after_close"] == 0, "error dialog reference was not released"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def main_() -> int:
    tests = [
        test_result_window_shows_and_stays,
        test_result_window_shown_before_progress_hidden,
        test_flow_returns_after_result_closed,
        test_failed_upload_keeps_error_dialog,
    ]
    failed = 0
    for test in tests:
        started = time.monotonic()
        try:
            test()
            elapsed = time.monotonic() - started
            print(f"PASS: {test.__name__} ({elapsed:.2f}s)")
        except Exception as exc:
            failed += 1
            print(f"FAIL: {test.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main_())
