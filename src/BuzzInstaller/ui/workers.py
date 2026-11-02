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
from core.bundle_service import BundleDownloadService, TransferPaused
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
    chunk = Signal(object, str, str)       # (chunk_index, state, detail)
    transferStarted = Signal(str)          # transfer UUID
    succeeded = Signal(object)             # list[UploadResult]
    failed = Signal(str, str)              # (title, message)
    cancelled = Signal()
    paused = Signal(str)                   # transfer UUID

    def __init__(self, paths: list[str], parent=None,
                 resume_uuid: Optional[str] = None) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._resume_uuid = str(resume_uuid or "")
        self._cancel = False
        self._paused = False
        self._emitter: Optional[_ThrottledEmitter] = None
        self._service: Optional[UploadService] = None
        self._transfer_uuid = ""

    def cancel(self) -> None:
        self._cancel = True
        service = self._service
        if service is not None:
            service.cancel()

    def pause(self) -> None:
        """Pause a resumable bundle upload; verified chunks are kept."""
        self._paused = True
        service = self._service
        if service is not None:
            service.pause()

    @property
    def transfer_uuid(self) -> str:
        if self._service is not None:
            return self._service.active_transfer_uuid or self._transfer_uuid
        return self._transfer_uuid

    def _on_progress(self, done: int, total: int) -> Optional[bool]:
        if self._emitter is not None:
            self._emitter.emit(done, total)
        return not self._cancel

    def _on_status(self, text: str) -> None:
        self._note_transfer()
        self.status.emit(text)

    def _on_chunk(self, index: int, state: str, detail: str) -> None:
        self._note_transfer()
        self.chunk.emit(int(index), str(state), str(detail))

    def _note_transfer(self) -> None:
        uuid = self.transfer_uuid
        if uuid and uuid != self._transfer_uuid:
            self._transfer_uuid = uuid
            self.transferStarted.emit(uuid)

    def run(self) -> None:
        service = UploadService()
        self._service = service
        self._emitter = _ThrottledEmitter(self.progress)
        try:
            if self._resume_uuid:
                self.status.emit("Resuming upload…")
                results = [service.upload(
                    "", self._on_progress, lambda: self._cancel,
                    chunk_cb=self._on_chunk, status_cb=self._on_status,
                    resume_uuid=self._resume_uuid,
                )]
            elif len(self._paths) == 1:
                self.status.emit("Uploading…")
                results = [service.upload(
                    self._paths[0], self._on_progress, lambda: self._cancel,
                    chunk_cb=self._on_chunk, status_cb=self._on_status,
                )]
            else:
                self.status.emit(f"Uploading {len(self._paths)} items…")
                results = service.upload_multiple(
                    self._paths, self._on_progress, lambda: self._cancel,
                    chunk_cb=None if len(self._paths) > 1 else self._on_chunk,
                    status_cb=self._on_status,
                )
            self._note_transfer()
            self.progress.emit(1, 1)
            self.succeeded.emit(results)
        except TransferPaused:
            self.paused.emit(self.transfer_uuid)
        except UploadCancelled:
            self.cancelled.emit()
        except Exception as exc:
            if self._cancel or self._paused:
                uuid = self.transfer_uuid
                if self._paused and uuid:
                    self.paused.emit(uuid)
                else:
                    self.cancelled.emit()
                return
            logger.exception("Upload failed")
            title, message = _friendly_error(exc)
            self.failed.emit(title, message)
        finally:
            try:
                service.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
class DownloadWorker(QThread):
    """Download a single BZ code, resuming bundles and falling back to legacy."""

    progress = Signal(object, object)
    status = Signal(str)
    chunk = Signal(object, str, str)       # (chunk_index, state, detail)
    transferStarted = Signal(str)          # transfer UUID
    succeeded = Signal(object)             # DownloadResult
    failed = Signal(str, str)
    cancelled = Signal()
    paused = Signal(str)                   # transfer UUID

    def __init__(self, code: str, config: Optional[dict] = None, parent=None,
                 resume_uuid: Optional[str] = None) -> None:
        super().__init__(parent)
        self._code = code
        self._config = config
        self._resume_uuid = str(resume_uuid or "")
        self._cancel = False
        self._paused = False
        self._emitter: Optional[_ThrottledEmitter] = None
        self._service: Optional[BundleDownloadService] = None
        self._transfer_uuid = ""

    def cancel(self) -> None:
        self._cancel = True
        service = self._service
        if service is not None:
            service.cancel()

    def pause(self) -> None:
        """Pause a resumable bundle download; verified chunks are kept."""
        self._paused = True
        service = self._service
        if service is not None:
            service.pause()

    @property
    def transfer_uuid(self) -> str:
        service = self._service
        if service is not None and service.current_uuid:
            return str(service.current_uuid)
        return self._transfer_uuid

    def _on_progress(self, done: int, total: int) -> Optional[bool]:
        if self._emitter is not None:
            self._emitter.emit(done, total)
        return not self._cancel

    def _on_status(self, text: str) -> None:
        self._note_transfer()
        self.status.emit(text)

    def _on_chunk(self, index: int, state: str, detail: str) -> None:
        self._note_transfer()
        self.chunk.emit(int(index), str(state), str(detail))

    def _note_transfer(self) -> None:
        uuid = self.transfer_uuid
        if uuid and uuid != self._transfer_uuid:
            self._transfer_uuid = uuid
            self.transferStarted.emit(uuid)

    def run(self) -> None:
        service = BundleDownloadService(config=self._config)
        self._service = service
        self._emitter = _ThrottledEmitter(self.progress)
        try:
            if self._resume_uuid:
                self.status.emit("Resuming download…")
            else:
                self.status.emit("Resolving download link…")
            result = service.download(
                self._code,
                progress_cb=self._on_progress,
                cancelled=lambda: self._cancel,
                chunk_cb=self._on_chunk,
                status_cb=self._on_status,
                resume_uuid=self._resume_uuid or None,
            )
            self._note_transfer()
            self.progress.emit(1, 1)
            self.succeeded.emit(result)
        except TransferPaused:
            self.paused.emit(self.transfer_uuid)
        except DownloadCancelled:
            self.cancelled.emit()
        except Exception as exc:
            if self._cancel or self._paused:
                uuid = self.transfer_uuid
                if self._paused and uuid:
                    self.paused.emit(uuid)
                else:
                    self.cancelled.emit()
                return
            logger.exception("Download failed")
            title, message = _friendly_error(exc)
            self.failed.emit(title, message)
        finally:
            try:
                service.close()
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
