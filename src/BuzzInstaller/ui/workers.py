"""
Background workers for the dashboard.

All network / filesystem work runs in ``QThread`` subclasses and reports
back through Qt signals, so the UI never blocks.  Services are released
(including their HTTP sessions) in ``finally`` blocks.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from core.buzzheavier_api import (
    BuzzHeavierError, DownloadCancelled, DownloadError, InvalidCodeError,
    NetworkError, UploadCancelled, UploadError,
)
from core.download_service import DownloadService
from core.upload_service import UploadService
from utils.logging import get_logger

logger = get_logger(__name__)


def _friendly_error(exc: Exception) -> tuple[str, str]:
    """Return a (title, message) pair for any transfer exception."""
    if isinstance(exc, UploadCancelled) or isinstance(exc, DownloadCancelled):
        return "Cancelled", "The transfer was cancelled."
    if isinstance(exc, InvalidCodeError):
        return "Invalid Link", str(exc)
    if isinstance(exc, NetworkError):
        return "Network Error", str(exc)
    if isinstance(exc, UploadError):
        return "Upload Failed", str(exc)
    if isinstance(exc, DownloadError):
        return "Download Failed", str(exc)
    if isinstance(exc, BuzzHeavierError):
        return "Error", str(exc)
    return "Unexpected Error", str(exc)


class _ThrottledEmitter:
    """Emit at most ~10 progress signals per second."""

    def __init__(self, signal) -> None:
        self._signal = signal
        self._last = 0.0

    def emit(self, done: int, total: int, force: bool = False) -> None:
        now = time.monotonic()
        if force or total <= 0 or done >= total or now - self._last >= 0.1:
            self._last = now
            self._signal.emit(int(done), int(total))


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
class UploadWorker(QThread):
    """Upload one or more files/folders sequentially."""

    progress = Signal(object, object)      # (bytes_done, bytes_total)
    status = Signal(str)
    succeeded = Signal(object)             # list[UploadResult]
    failed = Signal(str, str)              # (title, message)
    cancelled = Signal()

    def __init__(self, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._cancel = False
        self._emitter: Optional[_ThrottledEmitter] = None

    def cancel(self) -> None:
        self._cancel = True

    def _on_progress(self, done: int, total: int) -> Optional[bool]:
        if self._emitter is not None:
            self._emitter.emit(done, total)
        return not self._cancel

    def run(self) -> None:
        service = UploadService()
        self._emitter = _ThrottledEmitter(self.progress)
        try:
            if len(self._paths) == 1:
                self.status.emit("Uploading…")
                results = [service.upload(
                    self._paths[0], self._on_progress, lambda: self._cancel
                )]
            else:
                self.status.emit(f"Uploading {len(self._paths)} items…")
                results = service.upload_multiple(
                    self._paths, self._on_progress, lambda: self._cancel
                )
            self.progress.emit(1, 1)
            self.succeeded.emit(results)
        except UploadCancelled:
            self.cancelled.emit()
        except Exception as exc:
            if self._cancel:
                self.cancelled.emit()
                return
            logger.exception("Upload failed")
            title, message = _friendly_error(exc)
            self.failed.emit(title, message)
        finally:
            try:
                service.api.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
class DownloadWorker(QThread):
    """Download a single BZ code."""

    progress = Signal(object, object)
    status = Signal(str)
    succeeded = Signal(object)             # DownloadResult
    failed = Signal(str, str)
    cancelled = Signal()

    def __init__(self, code: str, config: Optional[dict] = None, parent=None) -> None:
        super().__init__(parent)
        self._code = code
        self._config = config
        self._cancel = False
        self._emitter: Optional[_ThrottledEmitter] = None

    def cancel(self) -> None:
        self._cancel = True

    def _on_progress(self, done: int, total: int) -> Optional[bool]:
        if self._emitter is not None:
            self._emitter.emit(done, total)
        return not self._cancel

    def run(self) -> None:
        service = DownloadService()
        self._emitter = _ThrottledEmitter(self.progress)
        try:
            self.status.emit("Resolving download link…")
            result = service.download(
                self._code,
                progress_cb=self._on_progress,
                cancelled=lambda: self._cancel,
                config=self._config,
            )
            self.progress.emit(1, 1)
            self.succeeded.emit(result)
        except DownloadCancelled:
            self.cancelled.emit()
        except Exception as exc:
            if self._cancel:
                self.cancelled.emit()
                return
            logger.exception("Download failed")
            title, message = _friendly_error(exc)
            self.failed.emit(title, message)
        finally:
            try:
                service.api.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------
class UpdateCheckWorker(QThread):
    """Check GitHub Releases for a newer BuzzInstaller build."""

    found = Signal(object)     # UpdateInfo | None
    failed = Signal(str)

    def run(self) -> None:
        from core import updater
        try:
            self.found.emit(updater.check_for_update())
        except Exception as exc:
            logger.info("Update check failed: %s", exc)
            self.failed.emit(str(exc))


class UpdateDownloadWorker(QThread):
    """Download a release bundle with progress."""

    progress = Signal(object, object)
    status = Signal(str)
    finished_ok = Signal(object)   # Path
    failed = Signal(str)

    def __init__(self, info, parent=None) -> None:
        super().__init__(parent)
        self._info = info
        self._cancel = False
        self._emitter = _ThrottledEmitter(self.progress)

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from core import updater
        try:
            self.status.emit(f"Downloading BuzzInstaller {self._info.version}…")
            path = updater.download_update(
                self._info,
                progress_cb=lambda done, total: (
                    self._emitter.emit(done, total) or not self._cancel
                ),
                cancelled=lambda: self._cancel,
            )
            self.finished_ok.emit(path)
        except Exception as exc:
            logger.exception("Update download failed")
            self.failed.emit(str(exc))
