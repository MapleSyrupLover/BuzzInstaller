"""
Upload service — orchestrates a file upload through BuzzHeavier.

Responsibilities
----------------
* Call :class:`BuzzHeavierAPI` to perform the PUT upload.
* Report byte-level progress via a callback.
* Copy the resulting ``BZ://`` link to the clipboard on success.
* Collect cancellation and error semantics for the UI layer.

The service is **synchronous** — UI code is expected to run it in a
background thread and communicate via callbacks.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.buzzheavier_api import (
    BuzzHeavierAPI,
    BuzzHeavierError,
    UploadCancelled,
    NetworkError,
    UploadError,
)
from system.clipboard_service import clipboard_service
from utils.logging import get_logger

logger = get_logger(__name__)

# A progress callback: (bytes_done, bytes_total) -> None | False (to cancel)
ProgressCallback = Callable[[int, int], Optional[bool]]
ChunkCallback = Callable[[int, str, str], None]
StatusCallback = Callable[[str], None]


@dataclass
class UploadResult:
    code: str
    url: str
    bz_url: str
    file_name: str
    size: int
    sha256: str = ""
    clipboard_ok: bool = True
    transfer_uuid: str = ""
    total_chunks: int = 0
    chunk_size: int = 0
    chunked: bool = False

    @property
    def size_str(self) -> str:
        from utils.file_utils import format_size
        return format_size(self.size)


class UploadService:
    """Upload files to BuzzHeavier and manage the clipboard side-effect."""

    def __init__(self, api: Optional[BuzzHeavierAPI] = None) -> None:
        self.api = api or BuzzHeavierAPI()
        self._active_bundle = None
        self._active_lock = threading.Lock()
        self._paused = False
        self._cancelled = False
        self._last_transfer_uuid = ""

    # -- controls --------------------------------------------------------
    def pause(self) -> None:
        """Pause a resumable bundle upload (completed chunks are kept)."""
        self._paused = True
        with self._active_lock:
            service = self._active_bundle
        if service is not None:
            service.pause()

    def cancel(self) -> None:
        self._cancelled = True
        with self._active_lock:
            service = self._active_bundle
        if service is not None:
            service.cancel()

    @property
    def active_is_bundle(self) -> bool:
        with self._active_lock:
            return self._active_bundle is not None

    @property
    def active_transfer_uuid(self) -> str:
        with self._active_lock:
            service = self._active_bundle
            last = self._last_transfer_uuid
        if service is not None:
            return str(getattr(service, "current_uuid", "") or last)
        return str(last or "")

    def _cancel_checker(self, external: Optional[Callable[[], bool]]):
        def check() -> bool:
            if self._cancelled or self._paused:
                return True
            if external is not None:
                return bool(external())
            return False
        return check

    def close(self) -> None:
        try:
            self.api.close()
        except Exception:
            pass

    # -- upload ----------------------------------------------------------
    def upload(
        self,
        file_path: str | Path,
        progress_cb: Optional[ProgressCallback] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        chunk_cb: Optional[ChunkCallback] = None,
        status_cb: Optional[StatusCallback] = None,
        resume_uuid: Optional[str] = None,
        force_legacy: bool = False,
    ) -> UploadResult:
        """
        Upload a single file.

        Parameters
        ----------
        file_path
            File or folder to upload.  Folders and files larger than one
            chunk use the resumable bundle path; ordinary small files keep
            the original single-request path.
        progress_cb
            ``(bytes_sent, total_bytes)``.  Returning ``False`` aborts.
        cancelled
            Callable returning ``True`` when the user has requested cancel.
        chunk_cb
            ``(chunk_index, state, detail)`` for resumable transfers.
        status_cb
            Human-readable status text for resumable transfers.
        resume_uuid
            Resume an interrupted bundle transfer instead of starting one.
        force_legacy
            Disable chunking for this call (used by tests/diagnostics).
        """
        self._paused = False
        self._cancelled = False
        self._last_transfer_uuid = ""
        path = Path(file_path) if str(file_path) else Path("")
        checker = self._cancel_checker(cancelled)

        if not force_legacy and (resume_uuid or self._should_bundle(path)):
            return self._upload_bundle(
                path, progress_cb, checker, chunk_cb, status_cb, resume_uuid,
            )

        # If it's a folder, zip it first
        upload_target = path
        if path.is_dir():
            upload_target = self._zip_directory(path)
            logger.info("Zipped folder %s → %s", path, upload_target)

        try:
            data = self.api.upload_file(upload_target, progress_cb, checker)
        except UploadCancelled:
            raise
        except NetworkError:
            raise
        except UploadError:
            raise
        except Exception as exc:
            logger.exception("Unexpected upload error")
            raise UploadError(str(exc)) from exc
        finally:
            # Clean up temp zip if we created one
            if upload_target != path and upload_target.exists():
                try:
                    upload_target.unlink()
                except OSError:
                    pass

        result = UploadResult(
            code=data["code"],
            url=data["url"],
            bz_url=data["bz_url"],
            file_name=data["name"],
            size=data["size"],
            sha256=data.get("sha256", ""),
        )
        self._finish_result(result)
        return result

    def _should_bundle(self, path: Path) -> bool:
        try:
            from core.bundle_service import should_use_bundle
            return should_use_bundle(path)
        except Exception:
            logger.exception("Bundle detection failed; using legacy upload")
            return False

    def _upload_bundle(
        self,
        path: Path,
        progress_cb: Optional[ProgressCallback],
        cancelled: Callable[[], bool],
        chunk_cb: Optional[ChunkCallback],
        status_cb: Optional[StatusCallback],
        resume_uuid: Optional[str],
    ) -> UploadResult:
        from core.bundle_service import BundleUploadService

        service = BundleUploadService()
        with self._active_lock:
            self._active_bundle = service
        try:
            data = service.upload(
                path,
                progress_cb=progress_cb,
                cancelled=cancelled,
                chunk_cb=chunk_cb,
                status_cb=status_cb,
                resume_uuid=resume_uuid,
            )
        finally:
            with self._active_lock:
                self._last_transfer_uuid = (
                    str(getattr(service, "current_uuid", "") or "")
                    or self._last_transfer_uuid
                )
                self._active_bundle = None
            service.close()

        result = UploadResult(
            code=data.code,
            url=data.url,
            bz_url=data.bz_url,
            file_name=data.file_name,
            size=data.size,
            sha256=data.sha256,
            transfer_uuid=data.transfer_uuid,
            total_chunks=data.total_chunks,
            chunk_size=data.chunk_size,
            chunked=data.chunked,
        )
        self._finish_result(result)
        return result

    @staticmethod
    def _finish_result(result: UploadResult) -> None:
        clipboard_ok = clipboard_service.copy(result.bz_url)
        if clipboard_ok:
            logger.info("Copied %s to clipboard", result.bz_url)
        else:
            logger.error(
                "Clipboard copy FAILED for %s — see clipboard log",
                result.bz_url,
            )
        result.clipboard_ok = clipboard_ok

    def upload_multiple(
        self,
        file_paths: list[str | Path],
        progress_cb: Optional[ProgressCallback] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        chunk_cb: Optional[ChunkCallback] = None,
        status_cb: Optional[StatusCallback] = None,
    ) -> list[UploadResult]:
        """
        Upload multiple files sequentially.

        *progress_cb* receives the **total** bytes across all uploads.
        Chunk and status callbacks are forwarded to each individual upload.
        """
        results: list[UploadResult] = []

        # First, compute total size for an overall progress bar
        total_size = 0
        sizes: list[int] = []
        for fp in file_paths:
            p = Path(fp)
            if p.is_file():
                sizes.append(p.stat().st_size)
            elif p.is_dir():
                # rough estimate: size of folder contents
                size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                sizes.append(size)
            else:
                sizes.append(0)
        total_size = sum(sizes)

        overall_sent = 0

        for fp, size in zip(file_paths, sizes):
            if cancelled and cancelled():
                raise UploadCancelled()

            file_sent = [0]

            def _rel_cb(done, total):
                file_sent[0] = done
                overall = overall_sent + done
                if progress_cb is not None:
                    return progress_cb(overall, total_size)
                return None

            try:
                result = self.upload(
                    fp, _rel_cb, cancelled,
                    chunk_cb=chunk_cb, status_cb=status_cb,
                )
                overall_sent += size
                results.append(result)
            except UploadCancelled:
                raise
            except NetworkError:
                raise
            except UploadError:
                raise

        return results

    # -- Helpers -------------------------------------------------------------
    def _zip_directory(self, dir_path: Path) -> Path:
        """Create a temporary ZIP of *dir_path* and return its path."""
        import tempfile, zipfile
        from utils.file_utils import sanitize_filename

        safe_name = sanitize_filename(dir_path.name) or "archive"
        tmp_dir = Path(tempfile.mkdtemp(prefix="bz_upload_"))
        zip_path = tmp_dir / f"{safe_name}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in dir_path.rglob("*"):
                if file_path.is_file():
                    arc = file_path.relative_to(dir_path)
                    zf.write(file_path, arc)

        logger.info("Created temp zip: %s", zip_path)
        return zip_path
