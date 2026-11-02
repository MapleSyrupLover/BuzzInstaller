"""
Resumable bundle uploads and downloads.

A bundle is a normal set of BuzzHeavier files: one file per chunk plus a
final ``.bzb`` JSON manifest.  The manifest is the only file referenced by
the public ``BZ://`` link.  Nothing about this requires server-side
multipart continuation — an interrupted chunk is restarted, while every
verified chunk is persisted in SQLite and skipped on the next attempt.

Public entry points
-------------------
* :class:`BundleUploadService` — upload/resume a file or folder bundle.
* :class:`BundleDownloadService` — download a bundle, or transparently
  fall back to the existing single-request downloader for legacy links.

Both services are synchronous and run their network work in worker
threads; the UI wraps them in ``QThread`` workers.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import uuid as uuidlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from config.config_manager import load_config
from config.paths import get_default_download_dir, get_transfers_dir
from core import bundle, chunking
from core.archive import archive_name, create_zip64_store
from core.buzzheavier_api import (
    BuzzHeavierAPI,
    BuzzHeavierError,
    DownloadCancelled,
    DownloadError,
    InvalidCodeError,
    NetworkError,
    UploadCancelled,
    UploadError,
)
from core.download_service import DownloadResult, DownloadService
from core.transfer_db import (
    CHUNK_COMPLETED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_FINALIZING,
    STATUS_INTERRUPTED,
    STATUS_PAUSED,
    STATUS_PENDING,
    STATUS_RUNNING,
    TransferStore,
    default_store,
)
from core.transfer_lock import TransferGuard, TransferInProgressError
from utils.file_utils import format_size, get_unique_path, is_zip, sanitize_filename
from utils.logging import get_logger

logger = get_logger(__name__)

ProgressCallback = Callable[[int, int], Optional[bool]]
ChunkCallback = Callable[[int, str, str], None]
StatusCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]


# ---------------------------------------------------------------------------
# Exceptions / results
# ---------------------------------------------------------------------------
class BundleError(BuzzHeavierError):
    """A bundle transfer failed."""


class BundleVerificationError(BundleError):
    """A downloaded/uploaded chunk or reconstructed file failed verification."""


class BundleManifestError(BundleError):
    """A manifest was invalid, unsupported or unsafe."""


class TransferPaused(BuzzHeavierError):
    """The user paused the transfer; completed chunks are preserved."""


class _StopRequested(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class BundleUploadResult:
    code: str
    url: str
    bz_url: str
    file_name: str
    size: int
    sha256: str = ""
    transfer_uuid: str = ""
    total_chunks: int = 0
    chunk_size: int = 0
    chunked: bool = True
    clipboard_ok: bool = True

    @property
    def size_str(self) -> str:
        return format_size(self.size)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_cb(callback, *args) -> Optional[bool]:
    if callback is None:
        return None
    try:
        return callback(*args)
    except Exception:
        logger.exception("Transfer callback raised")
        return None


def _int_config(config: dict, key: str, default: int,
                low: int = 1, high: int = 16) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _float_config(config: dict, key: str, default: float) -> float:
    try:
        return max(0.0, float(config.get(key, default)))
    except (TypeError, ValueError):
        return default


class _TransferBase:
    """Shared control, progress and API-pool plumbing."""

    def __init__(
        self,
        *,
        api_factory: Optional[Callable[[], BuzzHeavierAPI]] = None,
        store: Optional[TransferStore] = None,
        config: Optional[dict] = None,
        staging_dir: Optional[str | Path] = None,
        lock_dir: Optional[str | Path] = None,
    ) -> None:
        self.config = dict(config) if config is not None else load_config()
        self.store = store or default_store()
        self.chunk_size = chunking.chunk_size_from_config(self.config)
        self.retry_limit = _int_config(self.config, "chunk_retry_limit", 5, 1, 20)
        self.retry_base = _float_config(self.config, "chunk_retry_backoff", 1.0)
        self.account_id = str(self.config.get("buzzheavier_account_id", "") or "")
        self.parent_id = str(
            self.config.get("buzzheavier_bundle_parent_id", "") or ""
        )
        self._staging_root = (
            Path(staging_dir) if staging_dir is not None
            else get_transfers_dir()
        )
        self._lock_dir = Path(lock_dir) if lock_dir is not None else None
        self._api_factory = api_factory or (
            lambda: BuzzHeavierAPI(
                account_id=self.account_id, parent_id=self.parent_id,
            )
        )
        self._local = threading.local()
        self._apis: list[BuzzHeavierAPI] = []
        self._apis_lock = threading.Lock()
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._cancelled_cb: Optional[CancelCallback] = None
        self._progress_cb: Optional[ProgressCallback] = None
        self._chunk_cb: Optional[ChunkCallback] = None
        self._status_cb: Optional[StatusCallback] = None
        self._progress_lock = threading.Lock()
        self._callback_lock = threading.RLock()
        self._chunk_progress: dict[int, int] = {}
        self._completed_bytes = 0
        self._total_bytes = 0
        # UUID of the transfer currently being executed.  The UI reads this
        # to offer "Resume" when the worker stops for any reason.
        self.current_uuid = ""

    # -- public controls -------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()

    def pause(self) -> None:
        self._pause.set()

    def _reset_control(self) -> None:
        self._cancel.clear()
        self._pause.clear()
        self._chunk_progress.clear()
        self._completed_bytes = 0
        self._total_bytes = 0

    def _should_stop(self) -> bool:
        if self._cancel.is_set() or self._pause.is_set():
            return True
        cb = self._cancelled_cb
        if cb is not None:
            try:
                return bool(cb())
            except Exception:
                logger.exception("Cancellation callback raised")
        return False

    def _check_stop(self) -> None:
        if self._pause.is_set():
            raise _StopRequested("paused")
        if self._cancel.is_set():
            raise _StopRequested("cancelled")
        cb = self._cancelled_cb
        if cb is not None:
            try:
                if cb():
                    raise _StopRequested("cancelled")
            except _StopRequested:
                raise
            except Exception:
                logger.exception("Cancellation callback raised")

    def _status(self, text: str) -> None:
        with self._callback_lock:
            _safe_cb(self._status_cb, text)

    def _chunk_status(self, index: int, state: str, detail: str = "") -> None:
        with self._callback_lock:
            _safe_cb(self._chunk_cb, index, state, detail)

    # -- API pool --------------------------------------------------------
    def _api(self) -> BuzzHeavierAPI:
        api = getattr(self._local, "api", None)
        if api is None:
            api = self._api_factory()
            self._local.api = api
            with self._apis_lock:
                self._apis.append(api)
        return api

    def _drop_api(self) -> None:
        api = getattr(self._local, "api", None)
        self._local.api = None
        if api is None:
            return
        try:
            api.close()
        except Exception:
            pass
        with self._apis_lock:
            try:
                self._apis.remove(api)
            except ValueError:
                pass

    def close(self) -> None:
        with self._apis_lock:
            apis = list(self._apis)
            self._apis.clear()
        for api in apis:
            try:
                api.close()
            except Exception:
                pass
        self._local.api = None

    # -- progress --------------------------------------------------------
    def _reset_progress_from_store(self, uuid: str) -> None:
        chunks = self.store.get_chunks(uuid)
        with self._progress_lock:
            self._chunk_progress.clear()
            self._completed_bytes = sum(
                int(c["size"]) for c in chunks
                if c["status"] == CHUNK_COMPLETED
            )
            self._total_bytes = sum(int(c["size"]) for c in chunks)

    def _chunk_progress_cb(self, index: int, done: int, total: int) -> Optional[bool]:
        with self._progress_lock:
            self._chunk_progress[int(index)] = max(0, int(done))
        return self._emit_progress()

    def _emit_progress(self, force: bool = False) -> Optional[bool]:
        with self._progress_lock:
            done = self._completed_bytes + sum(self._chunk_progress.values())
            total = self._total_bytes
            done = min(done, total) if total > 0 else done
        with self._callback_lock:
            result = _safe_cb(self._progress_cb, int(done), int(total))
        if result is False:
            self._cancel.set()
            return False
        return None

    def _mark_completed_progress(self, index: int, size: int) -> None:
        with self._progress_lock:
            self._chunk_progress.pop(int(index), None)
            self._completed_bytes += int(size)

    def _reset_chunk_progress(self, index: int) -> None:
        with self._progress_lock:
            self._chunk_progress[int(index)] = 0

    # -- retry -----------------------------------------------------------
    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(30.0, self.retry_base * (2 ** max(0, attempt - 1)))
        if delay <= 0:
            return
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            self._check_stop()
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def _staging_dir(self, transfer_uuid: str) -> Path:
        safe = "".join(
            c for c in str(transfer_uuid) if c.isalnum() or c in "-_"
        )[:96] or "unknown"
        path = self._staging_root / safe
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _guard(self, transfer_uuid: str) -> TransferGuard:
        return TransferGuard(transfer_uuid, self._lock_dir)

    def _raise_for_stop(self) -> None:
        if self._pause.is_set():
            raise TransferPaused("Transfer paused.")
        if self._cancel.is_set():
            raise UploadCancelled()


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
class BundleUploadService(_TransferBase):
    """Upload a file/folder as independently resumable BuzzHeavier chunks."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.upload_concurrency = _int_config(
            self.config, "chunk_upload_concurrency", 3, 1, 8,
        )
        self.keep_archives = bool(self.config.get("keep_bundle_archives", False))

    # -- public API ------------------------------------------------------
    def upload(
        self,
        path: str | Path,
        progress_cb: Optional[ProgressCallback] = None,
        cancelled: Optional[CancelCallback] = None,
        chunk_cb: Optional[ChunkCallback] = None,
        status_cb: Optional[StatusCallback] = None,
        resume_uuid: Optional[str] = None,
    ) -> BundleUploadResult:
        """Upload a file or folder, or resume an interrupted transfer."""
        self._reset_control()
        self._progress_cb = progress_cb
        self._chunk_cb = chunk_cb
        self._status_cb = status_cb
        self._cancelled_cb = cancelled
        try:
            if resume_uuid:
                return self.resume_upload(resume_uuid)
            return self._start_upload(Path(path))
        finally:
            self.close()

    def resume_upload(self, transfer_uuid: str) -> BundleUploadResult:
        """Resume an upload after pause/cancel/restart."""
        self.current_uuid = str(transfer_uuid)
        transfer = self.store.get_transfer(transfer_uuid)
        if transfer is None:
            raise BundleError(f"Unknown transfer: {transfer_uuid}")
        if transfer["direction"] != "upload":
            raise BundleError("That transfer is not an upload.")
        if transfer["status"] == STATUS_COMPLETED:
            return self._result_from_transfer(transfer)

        source = Path(transfer["source_path"])
        if not source.exists():
            self.store.set_status(
                transfer_uuid, STATUS_FAILED,
                "The staged source/archive is no longer available. "
                "Please send the item again.",
            )
            raise chunking.SourceChangedError(
                f"Source is no longer available: {source}"
            )
        # _run_upload performs the full size + SHA-256 identity check once.
        return self._run_upload(transfer)

    # -- start / prepare -------------------------------------------------
    def _start_upload(self, source: Path) -> BundleUploadResult:
        if not source.exists():
            raise BundleError(f"File or folder does not exist: {source}")
        transfer_uuid = uuidlib.uuid4().hex
        staging = self._staging_dir(transfer_uuid)
        kind = "file"
        folder_name = ""

        if source.is_dir():
            kind = "folder"
            folder_name = sanitize_filename(source.name, max_len=160) or "folder"
            self._status("Creating ZIP64 archive (STORE mode)…")
            archive_path = staging / archive_name(folder_name)
            try:
                create_zip64_store(
                    source,
                    archive_path,
                    progress_cb=lambda done, total: _safe_cb(
                        self._progress_cb, done, total
                    ),
                    cancelled=self._should_stop,
                )
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            upload_source = archive_path
            original_name = folder_name
        else:
            upload_source = source
            original_name = sanitize_filename(source.name, max_len=200) or "file"

        self._status("Verifying source file…")
        size, sha256 = chunking.source_identity(
            upload_source, cancelled=self._should_stop,
        )

        existing = self.store.find_resumable(
            "upload", str(upload_source), size, sha256,
        )
        if existing is None and kind == "folder":
            # A folder archive is regenerated on every attempt, so its
            # path/hash cannot identify the previous transfer.  Reuse the
            # interrupted one when its staged archive is still intact.
            candidate = self.store.find_resumable_folder(
                str(source), folder_name,
            )
            if candidate is not None and Path(
                candidate["source_path"]
            ).is_file():
                existing = candidate
        if existing is not None:
            logger.info(
                "Resuming existing upload %s for %s",
                existing["uuid"], original_name,
            )
            if kind == "folder":
                # The staged archive is part of the existing transfer; use
                # its recorded path rather than the newly-created one.
                try:
                    staging_keep = Path(existing["source_path"]).parent
                    if staging_keep != staging and staging.exists():
                        shutil.rmtree(staging, ignore_errors=True)
                except OSError:
                    pass
            return self._run_upload(existing)

        specs = chunking.plan_chunks(
            upload_source, self.chunk_size, transfer_uuid,
        )
        extra = {
            "original_path": str(source),
            "folder_name": folder_name,
            "archive_format": "zip64-store" if kind == "folder" else "",
        }
        transfer = self.store.create_transfer(
            uuid=transfer_uuid,
            direction="upload",
            name=original_name,
            source_path=str(upload_source),
            size=size,
            sha256=sha256,
            chunk_size=self.chunk_size,
            chunks=specs,
            kind=kind,
            status=STATUS_PENDING,
            extra=extra,
        )
        return self._run_upload(transfer)

    # -- execution -------------------------------------------------------
    def _run_upload(self, transfer: dict) -> BundleUploadResult:
        uuid = str(transfer["uuid"])
        self.current_uuid = uuid
        guard = self._guard(uuid)
        if not guard.acquire():
            raise TransferInProgressError(
                f"Upload {uuid} is already running."
            )
        try:
            transfer = self.store.get_transfer(uuid) or transfer
            if transfer["status"] == STATUS_COMPLETED:
                return self._result_from_transfer(transfer)

            source = Path(transfer["source_path"])
            chunking.verify_source_identity(
                source, int(transfer["size"]), str(transfer["sha256"]),
                cancelled=self._should_stop,
            )
            self.store.requeue_incomplete_chunks(uuid)
            self.store.set_status(uuid, STATUS_RUNNING)
            self._reset_progress_from_store(uuid)
            self._total_bytes = int(transfer["size"])
            self._emit_progress(force=True)

            chunks = self.store.get_chunks(uuid)
            completed = {
                int(c["chunk_index"]) for c in chunks
                if c["status"] == CHUNK_COMPLETED
            }
            for chunk in chunks:
                if int(chunk["chunk_index"]) in completed:
                    self._chunk_status(
                        int(chunk["chunk_index"]), "completed",
                        str(chunk.get("remote_code") or ""),
                    )

            missing = [c for c in chunks if int(c["chunk_index"]) not in completed]
            if missing:
                self._status(
                    f"Uploading {len(missing)} of {len(chunks)} chunk(s)…"
                )
                workers = max(1, min(self.upload_concurrency, len(missing)))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(
                            self._upload_chunk, uuid, chunk, source,
                        ): chunk
                        for chunk in missing
                    }
                    try:
                        for future in as_completed(futures):
                            future.result()
                    except _StopRequested:
                        for future in futures:
                            future.cancel()
                        raise
                    except Exception as exc:
                        for future in futures:
                            future.cancel()
                        if isinstance(exc, _StopRequested):
                            raise
                        self.store.set_status(uuid, STATUS_FAILED, str(exc))
                        raise

            if self._pause.is_set():
                self.store.set_status(uuid, STATUS_PAUSED, "Paused by user.")
                raise TransferPaused("Transfer paused.")
            if self._cancel.is_set():
                self.store.set_status(
                    uuid, STATUS_INTERRUPTED, "Cancelled by user; "
                    "completed chunks were kept.",
                )
                raise UploadCancelled()

            # All chunks verified — upload the manifest (or finish a run
            # that crashed after the manifest upload).
            return self._finalize_upload(uuid)

        except TransferPaused:
            raise
        except UploadCancelled:
            raise
        except _StopRequested as exc:
            if exc.reason == "paused":
                self.store.set_status(uuid, STATUS_PAUSED, "Paused by user.")
                raise TransferPaused("Transfer paused.") from exc
            self.store.set_status(
                uuid, STATUS_INTERRUPTED, "Cancelled by user; "
                "completed chunks were kept.",
            )
            raise UploadCancelled() from exc
        except Exception as exc:
            if not isinstance(exc, TransferInProgressError):
                current = self.store.get_transfer(uuid)
                if current and current["status"] not in (
                    STATUS_COMPLETED, STATUS_PAUSED, STATUS_INTERRUPTED,
                ):
                    self.store.set_status(uuid, STATUS_FAILED, str(exc))
            raise
        finally:
            guard.release()

    def _upload_chunk(self, uuid: str, chunk: dict, source: Path) -> None:
        index = int(chunk["chunk_index"])
        expected_size = int(chunk["size"])
        offset = int(chunk["offset"])
        name = str(chunk.get("name") or f"chunk-{index:05d}")
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retry_limit + 1):
            self._check_stop()
            self._reset_chunk_progress(index)
            self.store.mark_chunk_started(uuid, index)
            self._chunk_status(index, "uploading", f"Chunk {index + 1}")
            try:
                with open(source, "rb") as handle:
                    handle.seek(offset)
                    result = self._api().upload_stream(
                        handle,
                        filename=name,
                        size=expected_size,
                        progress_cb=lambda done, total: self._chunk_progress_cb(
                            index, done, total
                        ),
                        cancelled=self._should_stop,
                        parent_id=self.parent_id or None,
                    )
                digest = str(result.get("local_sha256", "") or "")
                if not digest:
                    digest = chunking.hash_range(source, offset, expected_size)
                if int(result.get("size", expected_size)) != expected_size:
                    raise BundleVerificationError(
                        f"Chunk {index + 1} remote size mismatch."
                    )
                changed = self.store.mark_chunk_completed(
                    uuid, index,
                    sha256=digest,
                    size=expected_size,
                    remote_code=str(result.get("code", "")),
                    remote_url=str(result.get("url", "")),
                )
                if changed:
                    self._mark_completed_progress(index, expected_size)
                self._chunk_status(
                    index, "completed", str(result.get("code", "")),
                )
                self._emit_progress(force=True)
                return
            except _StopRequested:
                raise
            except (UploadCancelled, DownloadCancelled) as exc:
                if self._should_stop():
                    raise _StopRequested(
                        "paused" if self._pause.is_set() else "cancelled"
                    ) from exc
                last_error = exc
            except Exception as exc:
                if self._should_stop():
                    raise _StopRequested(
                        "paused" if self._pause.is_set() else "cancelled"
                    ) from exc
                last_error = exc

            self._drop_api()
            if attempt >= self.retry_limit:
                message = f"Chunk {index + 1} failed: {last_error}"
                self.store.mark_chunk_failed(uuid, index, str(last_error))
                self._chunk_status(index, "failed", str(last_error))
                raise BundleError(message) from last_error

            self.store.set_chunk_status(
                uuid, index, "retrying", str(last_error),
            )
            self._chunk_status(
                index, "retrying",
                f"Attempt {attempt + 1}/{self.retry_limit}: {last_error}",
            )
            self._status(
                f"Retrying chunk {index + 1} "
                f"(attempt {attempt + 1}/{self.retry_limit})…"
            )
            self._sleep_backoff(attempt)

        raise BundleError(f"Chunk {index + 1} failed: {last_error}")

    # -- manifest --------------------------------------------------------
    def _finalize_upload(self, uuid: str) -> BundleUploadResult:
        transfer = self.store.get_transfer(uuid)
        if transfer is None:
            raise BundleError(f"Unknown transfer: {uuid}")
        if transfer["status"] == STATUS_COMPLETED:
            return self._result_from_transfer(transfer)

        chunks = self.store.get_chunks(uuid)
        if any(c["status"] != CHUNK_COMPLETED for c in chunks):
            raise BundleError("Cannot finalize: not all chunks are verified.")

        # A previous run may have uploaded the manifest and crashed before
        # marking the transfer complete.
        if transfer.get("manifest_code") and transfer.get("manifest_url"):
            self.store.set_status(uuid, STATUS_COMPLETED)
            return self._result_from_transfer(
                self.store.get_transfer(uuid) or transfer
            )

        extra = transfer.get("extra_data") or {}
        kind = str(transfer.get("kind") or "file")
        manifest = bundle.build_manifest(
            transfer_uuid=uuid,
            name=str(transfer["name"]),
            size=int(transfer["size"]),
            sha256=str(transfer["sha256"]),
            chunk_size=int(transfer["chunk_size"]),
            kind=kind,
            folder_name=str(extra.get("folder_name", "") or ""),
            archive_format=str(extra.get("archive_format", "") or ""),
            chunks=[
                {
                    "index": int(c["chunk_index"]),
                    "offset": int(c["offset"]),
                    "size": int(c["size"]),
                    "sha256": str(c["sha256"]),
                    "name": str(c["name"]),
                    "remote_code": str(c["remote_code"]),
                    "remote_url": str(c["remote_url"]),
                    "bz_url": f"BZ://{c['remote_code']}" if c["remote_code"] else "",
                }
                for c in chunks
            ],
        )
        staging = self._staging_dir(uuid)
        manifest_path = staging / bundle.manifest_filename(
            str(transfer["name"]), uuid,
        )
        bundle.write_manifest(manifest_path, manifest)
        manifest_sha = chunking.sha256_file(manifest_path)

        self.store.set_status(uuid, STATUS_FINALIZING)
        self._status("Uploading bundle manifest…")
        try:
            result = self._api().upload_file(
                manifest_path,
                progress_cb=None,
                cancelled=self._should_stop,
                parent_id=self.parent_id or None,
            )
        except (UploadCancelled, DownloadCancelled) as exc:
            if self._should_stop():
                self.store.set_status(
                    uuid, STATUS_INTERRUPTED,
                    "Manifest upload interrupted; all chunks are safe.",
                )
                if self._pause.is_set():
                    self.store.set_status(uuid, STATUS_PAUSED, "Paused by user.")
                    raise TransferPaused("Transfer paused.") from exc
                raise UploadCancelled() from exc
            self.store.set_status(uuid, STATUS_FAILED, str(exc))
            raise BundleError(f"Manifest upload failed: {exc}") from exc
        except Exception as exc:
            self.store.set_status(uuid, STATUS_FAILED, str(exc))
            raise BundleError(f"Manifest upload failed: {exc}") from exc

        code = str(result.get("code", ""))
        url = str(result.get("url", ""))
        if not code:
            self.store.set_status(uuid, STATUS_FAILED, "Manifest upload returned no code.")
            raise BundleError("Manifest upload returned no file code.")

        manifest_json = bundle.serialize_manifest(manifest).decode("utf-8")
        self.store.set_manifest(
            uuid, code=code, url=url, sha256=manifest_sha,
            manifest_json=manifest_json,
        )
        self.store.set_status(uuid, STATUS_COMPLETED)
        self._emit_progress(force=True)

        # Cleanup is safe only after the manifest is confirmed uploaded.
        if kind == "folder" and not self.keep_archives:
            source = Path(transfer["source_path"])
            try:
                if source.exists() and source.parent == staging:
                    source.unlink(missing_ok=True)
            except OSError:
                pass
        if not self.keep_archives:
            shutil.rmtree(staging, ignore_errors=True)

        return self._result_from_transfer(
            self.store.get_transfer(uuid) or transfer
        )

    def _result_from_transfer(self, transfer: dict) -> BundleUploadResult:
        code = str(transfer.get("manifest_code", "") or "")
        url = str(transfer.get("manifest_url", "") or "")
        return BundleUploadResult(
            code=code,
            url=url,
            bz_url=f"BZ://{code}" if code else "",
            file_name=str(transfer["name"]),
            size=int(transfer["size"]),
            sha256=str(transfer.get("sha256", "") or ""),
            transfer_uuid=str(transfer["uuid"]),
            total_chunks=int(transfer.get("total_chunks", 0) or 0),
            chunk_size=int(transfer.get("chunk_size", 0) or 0),
        )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
class BundleDownloadService(_TransferBase):
    """Download bundles with resumable chunks and legacy fallback."""

    def __init__(self, **kwargs) -> None:
        legacy_service = kwargs.pop("legacy_service", None)
        super().__init__(**kwargs)
        self.download_concurrency = _int_config(
            self.config, "chunk_download_concurrency", 3, 1, 8,
        )
        self.legacy_service = legacy_service or DownloadService(api=self._api())

    # -- public API ------------------------------------------------------
    def download(
        self,
        code: str,
        progress_cb: Optional[ProgressCallback] = None,
        cancelled: Optional[CancelCallback] = None,
        chunk_cb: Optional[ChunkCallback] = None,
        status_cb: Optional[StatusCallback] = None,
        resume_uuid: Optional[str] = None,
    ) -> DownloadResult:
        """Download *code*, detecting a BZB manifest or falling back."""
        self._reset_control()
        self._progress_cb = progress_cb
        self._chunk_cb = chunk_cb
        self._status_cb = status_cb
        self._cancelled_cb = cancelled
        try:
            if resume_uuid:
                return self.resume_download(resume_uuid)
            self._status("Resolving download link…")
            prefix = self._api().peek_download(code, 16384)
            if not bundle.is_manifest_prefix(prefix):
                self._status("Downloading…")
                return self.legacy_service.download(
                    code, progress_cb, cancelled, self.config,
                )
            return self._download_manifest(code)
        finally:
            self.close()

    def resume_download(self, transfer_uuid: str) -> DownloadResult:
        """Resume a bundle download after pause/cancel/restart."""
        self.current_uuid = str(transfer_uuid)
        transfer = self.store.get_transfer(transfer_uuid)
        if transfer is None:
            raise BundleError(f"Unknown transfer: {transfer_uuid}")
        if transfer["direction"] != "download":
            raise BundleError("That transfer is not a download.")
        if transfer["status"] == STATUS_COMPLETED:
            result = self._result_from_transfer(transfer)
            if self._result_available(transfer):
                return result
            self.store.set_status(transfer_uuid, STATUS_INTERRUPTED)
            transfer = self.store.get_transfer(transfer_uuid) or transfer

        manifest = self._manifest_for_transfer(transfer)
        return self._run_download(transfer, manifest)

    # -- manifest --------------------------------------------------------
    def _download_manifest(self, code: str) -> DownloadResult:
        probe_dir = self._staging_root / (
            f"manifest-{sanitize_filename(code, max_len=32)}-{uuidlib.uuid4().hex}"
        )
        probe_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._status("Downloading bundle manifest…")
            path = self._api().download_file(
                code,
                dest_path=probe_dir / "manifest.bzb",
                progress_cb=None,
                cancelled=self._should_stop,
            )
            try:
                raw = Path(path).read_bytes()
            finally:
                shutil.rmtree(probe_dir, ignore_errors=True)
        except (DownloadCancelled, UploadCancelled) as exc:
            shutil.rmtree(probe_dir, ignore_errors=True)
            if self._pause.is_set():
                raise TransferPaused("Transfer paused.") from exc
            raise DownloadCancelled() from exc
        except (InvalidCodeError, DownloadError, NetworkError):
            shutil.rmtree(probe_dir, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(probe_dir, ignore_errors=True)
            raise BundleManifestError(f"Could not download manifest: {exc}") from exc

        try:
            manifest = bundle.parse_manifest(raw)
        except bundle.ManifestError as exc:
            raise BundleManifestError(str(exc)) from exc

        manifest_uuid = str(manifest["transfer_uuid"])
        manifest_json = bundle.serialize_manifest(manifest).decode("utf-8")

        existing = self.store.find_by_manifest_code(code)
        if existing is not None and existing["direction"] != "download":
            existing = None
        if existing is None:
            candidate = self.store.get_transfer(manifest_uuid)
            if candidate is not None and candidate["direction"] == "download":
                existing = candidate

        if existing is not None:
            previous_json = str(existing.get("manifest_json", "") or "")
            if previous_json and previous_json != manifest_json:
                raise BundleManifestError(
                    "The manifest changed for an existing transfer; refusing "
                    "to mix chunks from different bundles."
                )
            local_uuid = str(existing["uuid"])
            if existing["status"] == STATUS_COMPLETED:
                result = self._result_from_transfer(existing)
                if self._result_available(existing):
                    return result
                self.store.set_status(local_uuid, STATUS_INTERRUPTED)
            return self._run_download(
                self.store.get_transfer(local_uuid) or existing, manifest,
            )

        # A sender and receiver can be the same installation.  The upload
        # already owns the manifest UUID, so give the download its own
        # local UUID while remembering the manifest UUID for validation.
        if self.store.get_transfer(manifest_uuid) is None:
            local_uuid = manifest_uuid
        else:
            local_uuid = uuidlib.uuid4().hex

        staging = self._staging_dir(local_uuid)
        manifest_path = staging / "manifest.json"
        tmp = manifest_path.with_suffix(".json.tmp")
        tmp.write_bytes(manifest_json.encode("utf-8"))
        os.replace(tmp, manifest_path)

        dest_dir = self._download_dir()
        chunks = [
            chunking.ChunkSpec(
                index=int(c["index"]),
                offset=int(c["offset"]),
                size=int(c["size"]),
                name=str(c["name"]),
                sha256=str(c["sha256"]),
                remote_code=str(c["remote_code"]),
                remote_url=str(c.get("remote_url", "")),
            )
            for c in manifest["chunks"]
        ]
        transfer = self.store.create_transfer(
            uuid=local_uuid,
            direction="download",
            name=str(manifest["name"]),
            source_path=str(code),
            size=int(manifest["size"]),
            sha256=str(manifest["sha256"]),
            chunk_size=int(manifest["chunk_size"]),
            chunks=chunks,
            kind=str(manifest["kind"]),
            status=STATUS_PENDING,
            destination=str(dest_dir),
            manifest_code=str(code),
            manifest_url=f"https://buzzheavier.com/{code}",
            manifest_json=manifest_json,
            extra={
                "manifest_uuid": manifest_uuid,
                "folder_name": manifest.get("folder_name", ""),
                "archive_format": manifest.get("archive_format", ""),
            },
        )
        return self._run_download(transfer, manifest)

    def _manifest_for_transfer(self, transfer: dict) -> dict:
        raw = str(transfer.get("manifest_json", "") or "")
        if raw:
            try:
                return bundle.parse_manifest(raw)
            except bundle.ManifestError as exc:
                raise BundleManifestError(str(exc)) from exc

        code = str(transfer.get("manifest_code", "") or "")
        if not code:
            raise BundleManifestError("No manifest is recorded for this transfer.")
        probe_dir = self._staging_root / f"manifest-{uuidlib.uuid4().hex}"
        probe_dir.mkdir(parents=True, exist_ok=True)
        try:
            path = self._api().download_file(
                code, dest_path=probe_dir / "manifest.bzb",
                cancelled=self._should_stop,
            )
            manifest = bundle.parse_manifest(Path(path).read_bytes())
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)
        self.store.set_manifest(
            str(transfer["uuid"]),
            code=code,
            url=str(transfer.get("manifest_url", "") or ""),
            manifest_json=bundle.serialize_manifest(manifest).decode("utf-8"),
        )
        return manifest

    # -- execution -------------------------------------------------------
    def _run_download(self, transfer: dict, manifest: dict) -> DownloadResult:
        uuid = str(transfer["uuid"])
        self.current_uuid = uuid
        guard = self._guard(uuid)
        if not guard.acquire():
            raise TransferInProgressError(
                f"Download {uuid} is already running."
            )
        try:
            transfer = self.store.get_transfer(uuid) or transfer
            if transfer["status"] == STATUS_COMPLETED:
                return self._result_from_transfer(transfer)

            self._validate_transfer_manifest(transfer, manifest)
            self.store.requeue_incomplete_chunks(uuid)
            self.store.set_status(uuid, STATUS_RUNNING)
            self._reset_progress_from_store(uuid)
            self._total_bytes = int(transfer["size"])
            self._emit_progress(force=True)

            chunks = self.store.get_chunks(uuid)
            # A completed chunk is only reusable while its local part is
            # still present.  Staging may have been cleaned by the OS, or
            # the user may have deleted the reconstructed file.
            for chunk in chunks:
                if chunk["status"] != CHUNK_COMPLETED:
                    continue
                local = Path(str(chunk.get("local_path", "") or ""))
                if not local.is_file():
                    self.store.reset_chunk(
                        uuid, int(chunk["chunk_index"]),
                        include_completed=True,
                    )
            chunks = self.store.get_chunks(uuid)
            completed = {
                int(c["chunk_index"]) for c in chunks
                if c["status"] == CHUNK_COMPLETED
            }
            self._reset_progress_from_store(uuid)
            for chunk in chunks:
                if int(chunk["chunk_index"]) in completed:
                    self._chunk_status(
                        int(chunk["chunk_index"]), "completed",
                        "verified",
                    )
            missing = [c for c in chunks if int(c["chunk_index"]) not in completed]
            if missing:
                self._status(
                    f"Downloading {len(missing)} of {len(chunks)} chunk(s)…"
                )
                workers = max(1, min(self.download_concurrency, len(missing)))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(
                            self._download_chunk, uuid, chunk, manifest,
                        ): chunk
                        for chunk in missing
                    }
                    try:
                        for future in as_completed(futures):
                            future.result()
                    except _StopRequested:
                        for future in futures:
                            future.cancel()
                        raise
                    except Exception as exc:
                        for future in futures:
                            future.cancel()
                        if isinstance(exc, _StopRequested):
                            raise
                        self.store.set_status(uuid, STATUS_FAILED, str(exc))
                        raise

            if self._pause.is_set():
                self.store.set_status(uuid, STATUS_PAUSED, "Paused by user.")
                raise TransferPaused("Transfer paused.")
            if self._cancel.is_set():
                self.store.set_status(
                    uuid, STATUS_INTERRUPTED,
                    "Cancelled by user; completed chunks were kept.",
                )
                raise DownloadCancelled()

            return self._reassemble(transfer, manifest)

        except TransferPaused:
            raise
        except DownloadCancelled:
            raise
        except _StopRequested as exc:
            if exc.reason == "paused":
                self.store.set_status(uuid, STATUS_PAUSED, "Paused by user.")
                raise TransferPaused("Transfer paused.") from exc
            self.store.set_status(
                uuid, STATUS_INTERRUPTED,
                "Cancelled by user; completed chunks were kept.",
            )
            raise DownloadCancelled() from exc
        except Exception as exc:
            if not isinstance(exc, TransferInProgressError):
                current = self.store.get_transfer(uuid)
                if current and current["status"] not in (
                    STATUS_COMPLETED, STATUS_PAUSED, STATUS_INTERRUPTED,
                ):
                    self.store.set_status(uuid, STATUS_FAILED, str(exc))
            raise
        finally:
            guard.release()

    def _download_chunk(self, uuid: str, chunk: dict,
                        manifest: dict) -> None:
        index = int(chunk["chunk_index"])
        expected_size = int(chunk["size"])
        expected_sha = str(chunk["sha256"])
        code = str(chunk["remote_code"])
        staging = self._staging_dir(uuid) / "chunks"
        staging.mkdir(parents=True, exist_ok=True)
        chunk_path = staging / f"{index:05d}.part"
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retry_limit + 1):
            self._check_stop()
            self._reset_chunk_progress(index)
            self.store.mark_chunk_started(uuid, index)
            self._chunk_status(index, "downloading", f"Chunk {index + 1}")
            try:
                chunk_path.unlink(missing_ok=True)
                actual = Path(self._api().download_file(
                    code,
                    dest_path=chunk_path,
                    progress_cb=lambda done, total: self._chunk_progress_cb(
                        index, done, total
                    ),
                    cancelled=self._should_stop,
                ))
                size = actual.stat().st_size
                if size != expected_size:
                    actual.unlink(missing_ok=True)
                    raise BundleVerificationError(
                        f"Chunk {index + 1} size mismatch "
                        f"({size} != {expected_size})."
                    )
                digest = chunking.sha256_file(
                    actual, cancelled=self._should_stop,
                )
                if digest.lower() != expected_sha.lower():
                    actual.unlink(missing_ok=True)
                    raise BundleVerificationError(
                        f"Chunk {index + 1} checksum mismatch."
                    )
                changed = self.store.mark_chunk_completed(
                    uuid, index,
                    sha256=digest,
                    size=size,
                    local_path=str(actual),
                    remote_code=code,
                    remote_url=f"https://buzzheavier.com/{code}",
                )
                if changed:
                    self._mark_completed_progress(index, size)
                self._chunk_status(index, "completed", "verified")
                self._emit_progress(force=True)
                return
            except _StopRequested:
                chunk_path.unlink(missing_ok=True)
                raise
            except (DownloadCancelled, UploadCancelled) as exc:
                chunk_path.unlink(missing_ok=True)
                if self._should_stop():
                    raise _StopRequested(
                        "paused" if self._pause.is_set() else "cancelled"
                    ) from exc
                last_error = exc
            except Exception as exc:
                chunk_path.unlink(missing_ok=True)
                if self._should_stop():
                    raise _StopRequested(
                        "paused" if self._pause.is_set() else "cancelled"
                    ) from exc
                last_error = exc

            self._drop_api()
            if attempt >= self.retry_limit:
                message = f"Chunk {index + 1} failed: {last_error}"
                self.store.mark_chunk_failed(uuid, index, str(last_error))
                self._chunk_status(index, "failed", str(last_error))
                raise BundleError(message) from last_error

            self.store.set_chunk_status(
                uuid, index, "retrying", str(last_error),
            )
            self._chunk_status(
                index, "retrying",
                f"Attempt {attempt + 1}/{self.retry_limit}: {last_error}",
            )
            self._status(
                f"Retrying chunk {index + 1} "
                f"(attempt {attempt + 1}/{self.retry_limit})…"
            )
            self._sleep_backoff(attempt)

        raise BundleError(f"Chunk {index + 1} failed: {last_error}")

    # -- reassembly ------------------------------------------------------
    def _reassemble(self, transfer: dict, manifest: dict) -> DownloadResult:
        uuid = str(transfer["uuid"])
        dest_dir = Path(transfer.get("destination") or self._download_dir())
        dest_dir.mkdir(parents=True, exist_ok=True)
        kind = str(manifest.get("kind") or transfer.get("kind") or "file")

        if kind == "folder":
            final_name = archive_name(str(manifest["name"]))
        else:
            final_name = bundle.safe_original_name(str(manifest["name"])) or "download"

        final_path = get_unique_path(dest_dir, final_name)
        temp_path = final_path.parent / f".{final_path.name}.{uuid}.part"
        temp_path.unlink(missing_ok=True)

        chunks = self.store.get_chunks(uuid)
        total = int(manifest["size"])
        whole = hashlib.sha256()
        written = 0
        try:
            with open(temp_path, "wb") as out:
                for chunk in chunks:
                    index = int(chunk["chunk_index"])
                    expected_size = int(chunk["size"])
                    expected_sha = str(chunk["sha256"])
                    local = Path(str(chunk.get("local_path", "") or ""))
                    if not local.is_file():
                        local = self._staging_dir(uuid) / "chunks" / f"{index:05d}.part"
                    if not local.is_file():
                        self.store.reset_chunk(
                            uuid, index, include_completed=True,
                        )
                        raise BundleVerificationError(
                            f"Chunk {index + 1} is missing locally; it will be "
                            "downloaded again on resume."
                        )
                    chunk_digest = hashlib.sha256()
                    chunk_size = 0
                    with open(local, "rb") as src:
                        while True:
                            if self._should_stop():
                                raise _StopRequested(
                                    "paused" if self._pause.is_set() else "cancelled"
                                )
                            block = src.read(chunking.IO_BLOCK)
                            if not block:
                                break
                            out.write(block)
                            chunk_digest.update(block)
                            whole.update(block)
                            chunk_size += len(block)
                            written += len(block)
                    if chunk_size != expected_size:
                        self.store.reset_chunk(
                            uuid, index, include_completed=True,
                        )
                        raise BundleVerificationError(
                            f"Chunk {index + 1} local size mismatch during "
                            "reassembly."
                        )
                    if chunk_digest.hexdigest().lower() != expected_sha.lower():
                        self.store.reset_chunk(
                            uuid, index, include_completed=True,
                        )
                        raise BundleVerificationError(
                            f"Chunk {index + 1} checksum mismatch during "
                            "reassembly."
                        )
            out_size = temp_path.stat().st_size
            if out_size != total:
                raise BundleVerificationError(
                    f"Reconstructed file size mismatch ({out_size} != {total})."
                )
            if whole.hexdigest().lower() != str(manifest["sha256"]).lower():
                raise BundleVerificationError(
                    "Reconstructed file SHA-256 does not match the manifest."
                )
            os.replace(temp_path, final_path)
        except _StopRequested as exc:
            temp_path.unlink(missing_ok=True)
            if exc.reason == "paused":
                self.store.set_status(uuid, STATUS_PAUSED, "Paused by user.")
                raise TransferPaused("Transfer paused.") from exc
            self.store.set_status(
                uuid, STATUS_INTERRUPTED, "Cancelled by user.",
            )
            raise DownloadCancelled() from exc
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

        result = DownloadResult(
            code=str(transfer.get("manifest_code", "") or transfer.get("source_path", "")),
            file_path=final_path,
            file_name=str(manifest["name"]),
            size=total,
            download_folder=dest_dir,
        )
        try:
            self.legacy_service.apply_post_download(
                result, self.config, force_extract=(kind == "folder"),
            )
        except Exception as exc:
            logger.warning("Post-download processing failed: %s", exc)

        extra = dict(transfer.get("extra_data") or {})
        extra.update({
            "file_path": str(result.file_path),
            "extracted_to": str(result.extracted_to or ""),
            "was_extracted": bool(result.was_extracted),
        })
        self.store.set_extra(uuid, extra)
        destination = (
            str(result.extracted_to) if result.was_extracted and result.extracted_to
            else str(result.file_path)
        )
        self.store.set_status(uuid, STATUS_COMPLETED, destination=destination)
        self._emit_progress(force=True)

        # Verified chunks and the staging manifest are no longer needed.
        shutil.rmtree(self._staging_dir(uuid), ignore_errors=True)
        return result

    def _validate_transfer_manifest(self, transfer: dict, manifest: dict) -> None:
        extra = transfer.get("extra_data") or {}
        expected_uuid = str(extra.get("manifest_uuid") or transfer["uuid"])
        if str(manifest["transfer_uuid"]) != expected_uuid:
            raise BundleManifestError("Manifest UUID does not match the transfer.")
        if int(manifest["size"]) != int(transfer["size"]):
            raise BundleManifestError("Manifest size changed since the transfer started.")
        if str(manifest["sha256"]).lower() != str(transfer["sha256"]).lower():
            raise BundleManifestError("Manifest SHA-256 changed since the transfer started.")
        chunks = self.store.get_chunks(str(transfer["uuid"]))
        if len(chunks) != int(manifest["total_chunks"]):
            raise BundleManifestError("Manifest chunk count changed.")
        for chunk, manifest_chunk in zip(chunks, manifest["chunks"]):
            if int(chunk["chunk_index"]) != int(manifest_chunk["index"]):
                raise BundleManifestError("Manifest chunk order changed.")
            if int(chunk["size"]) != int(manifest_chunk["size"]):
                raise BundleManifestError("Manifest chunk size changed.")
            if str(chunk["sha256"]).lower() != str(manifest_chunk["sha256"]).lower():
                raise BundleManifestError("Manifest chunk checksum changed.")

    # -- helpers ---------------------------------------------------------
    def _download_dir(self) -> Path:
        configured = str(self.config.get("download_folder", "") or "")
        path = Path(configured) if configured else get_default_download_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _result_available(transfer: dict) -> bool:
        extra = transfer.get("extra_data") or {}
        for key in ("file_path", "extracted_to"):
            raw = str(extra.get(key, "") or "")
            if raw and Path(raw).exists():
                return True
        return False

    def _result_from_transfer(self, transfer: dict) -> DownloadResult:
        extra = transfer.get("extra_data") or {}
        raw_path = str(extra.get("file_path", "") or "")
        file_path = Path(raw_path) if raw_path else Path("")
        extracted_raw = str(extra.get("extracted_to", "") or "")
        extracted_to = Path(extracted_raw) if extracted_raw else None
        return DownloadResult(
            code=str(transfer.get("manifest_code", "") or ""),
            file_path=file_path,
            file_name=str(transfer["name"]),
            size=int(transfer["size"]),
            was_extracted=bool(extra.get("was_extracted", False)),
            extracted_to=extracted_to,
            download_folder=(
                file_path.parent if raw_path else
                (Path(str(transfer["destination"])) if transfer.get("destination") else None)
            ),
        )


# ---------------------------------------------------------------------------
# Public helpers used by the UI / services
# ---------------------------------------------------------------------------
def should_use_bundle(path: str | Path, config: Optional[dict] = None) -> bool:
    """True when *path* should use the resumable bundle path."""
    cfg = config if config is not None else load_config()
    if not bool(cfg.get("chunked_transfers_enabled", True)):
        return False
    return chunking.is_bundle_candidate(path, chunking.chunk_size_from_config(cfg))


def resume_upload(transfer_uuid: str, **kwargs) -> BundleUploadResult:
    return BundleUploadService(**kwargs).upload("", resume_uuid=transfer_uuid)


def resume_download(transfer_uuid: str, **kwargs) -> DownloadResult:
    return BundleDownloadService(**kwargs).download(
        "", resume_uuid=transfer_uuid,
    )
