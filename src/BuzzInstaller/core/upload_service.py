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


@dataclass
class UploadResult:
    code: str
    url: str
    bz_url: str
    file_name: str
    size: int
    sha256: str = ""
    clipboard_ok: bool = True

    @property
    def size_str(self) -> str:
        from utils.file_utils import format_size
        return format_size(self.size)


class UploadService:
    """Upload files to BuzzHeavier and manage the clipboard side-effect."""

    def __init__(self, api: Optional[BuzzHeavierAPI] = None) -> None:
        self.api = api or BuzzHeavierAPI()

    def upload(
        self,
        file_path: str | Path,
        progress_cb: Optional[ProgressCallback] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> UploadResult:
        """
        Upload a single file.

        Parameters
        ----------
        file_path
            File or folder to upload.  (Folders are zipped before upload.)
        progress_cb
            ``(bytes_sent, total_bytes)``.  Returning ``False`` aborts.
        cancelled
            Callable returning ``True`` when the user has requested cancel.
        """
        path = Path(file_path)

        # If it's a folder, zip it first
        upload_target = path
        if path.is_dir():
            upload_target = self._zip_directory(path)
            logger.info("Zipped folder %s → %s", path, upload_target)

        try:
            data = self.api.upload_file(upload_target, progress_cb, cancelled)
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

        # Copy the BZ:// link to the clipboard via the single
        # ClipboardService (GUI-thread safe; caller is the worker thread).
        clipboard_ok = clipboard_service.copy(result.bz_url)
        if clipboard_ok:
            logger.info("Copied %s to clipboard", result.bz_url)
        else:
            logger.error("Clipboard copy FAILED for %s — see clipboard log", result.bz_url)
        result.clipboard_ok = clipboard_ok

        return result

    def upload_multiple(
        self,
        file_paths: list[str | Path],
        progress_cb: Optional[ProgressCallback] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[UploadResult]:
        """
        Upload multiple files sequentially.

        *progress_cb* receives the **total** bytes across all uploads.
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
                result = self.upload(fp, _rel_cb, cancelled)
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
