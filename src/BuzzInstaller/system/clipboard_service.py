"""
ClipboardService — the single, thread-safe path for ALL clipboard access.

Root cause fixed
----------------
Windows requires clipboard access (OpenClipboard / SetClipboardData /
GetClipboardData / QClipboard) to happen from a thread that owns a
message queue — i.e. the Qt GUI thread.  Previously:

* ``upload_service`` copied the BZ:// link from the **upload worker**
  thread.
* ``hotkey_service`` read/copied the selection from the **hotkey
  message-loop** thread.

Both silently failed (or were unsafe) because the raw Win32 calls were
invoked off the GUI thread.  This service marshals every operation onto
the Qt GUI thread via a queued signal, then waits for the result.

Capabilities
------------
* ``copy(text) -> bool`` — copies on the GUI thread, then **reads the
  clipboard back** and verifies it equals the expected text.  On failure
  it logs the exact exception, the full traceback, AND the actual
  clipboard contents.
* ``read() -> Optional[str]`` — reads clipboard text on the GUI thread.
* ``clear() -> bool`` — empties the clipboard on the GUI thread.

Every feature (upload auto-copy, "Copy Again", hotkey selected-text
retrieval) goes through this single service so behaviour stays
consistent and maintainable.
"""

from __future__ import annotations

import threading
import traceback
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from utils.logging import get_logger

logger = get_logger(__name__)


class ClipboardService(QObject):
    """Thread-safe clipboard access, always executed on the GUI thread."""

    # Signal carrying (fn, result_dict, event).  QueuedConnection routes
    # it to the GUI thread (the service is moved there).
    _operation = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._operation.connect(self._execute, Qt.QueuedConnection)
        self._ensure_gui_thread_affinity()

    def _ensure_gui_thread_affinity(self) -> None:
        """Move this service onto the GUI thread so queued slots run there."""
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None and QThread.currentThread() is not app.thread():
                self.moveToThread(app.thread())
        except Exception as exc:
            logger.warning("ClipboardService thread affinity setup failed: %s", exc)

    @Slot(object)
    def _execute(self, payload: tuple) -> None:
        """Run *fn* on the GUI thread and publish the result."""
        fn, result, event = payload
        try:
            result["value"] = fn()
            result["ok"] = True
        except Exception as exc:
            result["exc"] = exc
            result["tb"] = traceback.format_exc()
        finally:
            event.set()

    def _dispatch(self, fn: Callable[[], object]) -> object:
        """Run *fn* on the GUI thread and return its value (or raise)."""
        app = None
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
        except Exception as exc:
            logger.error("ClipboardService: cannot obtain QApplication: %r\n%s",
                         exc, traceback.format_exc())
            raise RuntimeError("QApplication unavailable for clipboard access") from exc

        if app is None:
            logger.error("ClipboardService: no QApplication exists — clipboard unavailable")
            raise RuntimeError("No QApplication exists for clipboard access")

        # Already on the GUI thread → run directly.
        if QThread.currentThread() is app.thread():
            return fn()

        # Off the GUI thread → marshal via queued signal and wait.
        result: dict = {}
        event = threading.Event()
        self._operation.emit((fn, result, event))
        if not event.wait(timeout=5.0):
            logger.error(
                "ClipboardService: GUI thread did not service request within 5s\n%s",
                traceback.format_stack(),
            )
            raise TimeoutError("Clipboard GUI-thread request timed out")

        if "exc" in result:
            logger.error(
                "ClipboardService: operation raised %r\n%s",
                result["exc"], result.get("tb", ""),
            )
            raise result["exc"]
        return result.get("value")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def copy(self, text: str) -> bool:
        """Copy *text* to the clipboard and VERIFY it landed.

        Logs the exact exception + traceback on failure, and the actual
        clipboard contents if verification fails.
        """
        try:
            self._dispatch(lambda: _qt_clipboard_set(text))
        except Exception:
            logger.error("Clipboard copy FAILED for %r\n%s",
                         text, traceback.format_exc())
            return False

        # Verify by reading back.
        try:
            actual = self._dispatch(lambda: _qt_clipboard_get())
        except Exception:
            logger.error("Clipboard verification read FAILED after copy of %r\n%s",
                         text, traceback.format_exc())
            return False

        if actual != text:
            logger.error(
                "Clipboard verification FAILED: expected %r, "
                "actual clipboard contains %r", text, actual,
            )
            return False

        logger.info("Clipboard copy verified for %r", text)
        return True

    def read(self) -> Optional[str]:
        """Return the current clipboard text, or None on failure/empty."""
        try:
            value = self._dispatch(lambda: _qt_clipboard_get())
            return value if value else None
        except Exception:
            logger.error("Clipboard read FAILED\n%s", traceback.format_exc())
            return None

    def get_sequence_number(self) -> int:
        """Return the Win32 clipboard sequence number (0 if unavailable).

        The sequence number increments every time the clipboard contents
        change.  The hotkey service uses this to detect when a simulated
        Ctrl+C has actually updated the clipboard, instead of reading
        stale pre-copy content.
        """
        try:
            return self._dispatch(lambda: _qt_clipboard_sequence_number())
        except Exception:
            return 0

    def clear(self) -> bool:
        """Empty the clipboard on the GUI thread."""
        try:
            self._dispatch(lambda: _qt_clipboard_clear())
            return True
        except Exception:
            logger.error("Clipboard clear FAILED\n%s", traceback.format_exc())
            return False

    # ------------------------------------------------------------------
    # Full-format preservation (for selected-text mode Ctrl+C fallback)
    # ------------------------------------------------------------------
    def save_full(self) -> dict:
        """Snapshot the entire clipboard (all formats) on the GUI thread.

        Returns a dict that can be passed to :meth:`restore_full`.  If
        the clipboard cannot be read, returns an empty dict (restore
        becomes a no-op).
        """
        try:
            return self._dispatch(lambda: _qt_clipboard_save_full())
        except Exception:
            logger.error("Clipboard full save FAILED\n%s", traceback.format_exc())
            return {}

    def restore_full(self, snapshot: dict) -> bool:
        """Restore a clipboard snapshot (all formats) on the GUI thread.

        Restores every format captured by :meth:`save_full`.  If the
        snapshot is empty, clears the clipboard.  Always runs even if
        the caller raised an exception (call from a ``finally`` block).
        """
        try:
            self._dispatch(lambda: _qt_clipboard_restore_full(snapshot))
            return True
        except Exception:
            logger.error("Clipboard full restore FAILED\n%s", traceback.format_exc())
            return False


# ---------------------------------------------------------------------------
# Qt clipboard primitives (must run on the GUI thread — ClipboardService
# guarantees this).
# ---------------------------------------------------------------------------
def _qt_clipboard_set(text: str) -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().setText(text)


def _qt_clipboard_get() -> Optional[str]:
    from PySide6.QtWidgets import QApplication
    text = QApplication.clipboard().text()
    return text if text else None


def _qt_clipboard_sequence_number() -> int:
    """Return the Win32 clipboard sequence number (0 if unavailable)."""
    import ctypes
    try:
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:
        return 0


def _qt_clipboard_clear() -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().clear()


def _qt_clipboard_save_full() -> dict:
    """Snapshot every clipboard format (text, HTML, image, files, etc.)."""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QImage
    cb = QApplication.clipboard()
    mime = cb.mimeData()
    snapshot: dict = {}
    if mime is None:
        return snapshot
    for fmt in mime.formats():
        try:
            data = mime.data(fmt)
            snapshot[fmt] = bytes(data)
        except Exception:
            continue
    # Also capture text and image for convenience.
    try:
        snapshot["_text"] = cb.text()
    except Exception:
        pass
    try:
        img = cb.image()
        if not img.isNull():
            snapshot["_image"] = img
    except Exception:
        pass
    return snapshot


def _qt_clipboard_restore_full(snapshot: dict) -> None:
    """Restore a clipboard snapshot captured by ``_qt_clipboard_save_full``."""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QMimeData
    cb = QApplication.clipboard()
    if not snapshot:
        cb.clear()
        return
    mime = QMimeData()
    for fmt, data in snapshot.items():
        if fmt.startswith("_"):
            continue
        try:
            mime.setData(fmt, data)
        except Exception:
            continue
    cb.setMimeData(mime)
    # Restore text/image if present (some apps only read these).
    if "_text" in snapshot:
        try:
            cb.setText(snapshot["_text"])
        except Exception:
            pass
    if "_image" in snapshot:
        try:
            cb.setImage(snapshot["_image"])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level singleton — the single clipboard entry point for the app.
# ---------------------------------------------------------------------------
clipboard_service = ClipboardService()