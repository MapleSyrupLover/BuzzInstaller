"""
Download service — orchestrates a file download from BuzzHeavier.

Responsibilities
----------------
* Call :class:`BuzzHeavierAPI` to download the file for a given BZ code.
* Optionally auto-extract ZIP archives into ``Downloads\\Extracted\\``.
* Optionally delete the ZIP after extraction.
* Optionally launch Windows Explorer on the destination folder.
* Report byte-level progress and support cancellation.

The service is **synchronous** — UI code runs it in a background thread.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.buzzheavier_api import (
    BuzzHeavierAPI,
    NetworkError,
    DownloadError,
    DownloadCancelled,
    InvalidCodeError,
    CloudflareBlockedError,
)
from config.config_manager import load_config
from config.paths import get_default_download_dir, get_cache_dir
from utils.file_utils import format_size, get_unique_path, is_zip, extract_zip, sanitize_filename
from utils.logging import get_logger

logger = get_logger(__name__)

ProgressCallback = Callable[[int, int], Optional[bool]]


@dataclass
class DownloadResult:
    code: str
    file_path: Path
    file_name: str
    size: int
    was_extracted: bool = False
    extracted_to: Optional[Path] = None
    download_folder: Optional[Path] = None

    @property
    def size_str(self) -> str:
        return format_size(self.size)


class DownloadService:
    """Download files from BuzzHeavier and perform post-download actions."""

    def __init__(self, api: Optional[BuzzHeavierAPI] = None) -> None:
        self.api = api or BuzzHeavierAPI()

    def download(
        self,
        code: str,
        progress_cb: Optional[ProgressCallback] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        config: Optional[dict] = None,
    ) -> DownloadResult:
        """
        Download the file for *code* and apply post-download settings.

        Parameters
        ----------
        code
            The BuzzHeavier file code (e.g. ``h0l6lgmru052``).
        progress_cb
            ``(bytes_received, total_bytes)``.  Return ``False`` to abort.
        cancelled
            Callable returning ``True`` when the user cancels.
        config
            Optional config dict.  Loaded from disk if not provided.
        """
        if config is None:
            config = load_config()

        # Determine download folder
        dl_dir_str = config.get("download_folder", "") or ""
        if dl_dir_str:
            dl_dir = Path(dl_dir_str)
        else:
            dl_dir = get_default_download_dir()
        dl_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Downloading code %s → %s", code, dl_dir)

        file_path = self.api.download_file(
            code,
            dest_path=dl_dir,
            progress_cb=progress_cb,
            cancelled=cancelled,
        )

        result = DownloadResult(
            code=code,
            file_path=file_path,
            file_name=file_path.name,
            size=file_path.stat().st_size,
            download_folder=dl_dir,
        )

        # --- Auto-extract ZIP ----------------------------------------------
        auto_extract = config.get("auto_extract_zip", True)
        if auto_extract and is_zip(file_path):
            logger.info("Auto-extracting ZIP: %s", file_path)
            extract_dir = dl_dir / "Extracted" / sanitize_filename(file_path.stem)
            extract_dir = get_unique_path(extract_dir, extract_dir.name + ".d")
            # Use unique path that doesn't exist
            extract_dir = self._ensure_unique_dir(extract_dir)

            try:
                extracted_files = extract_zip(file_path, extract_dir)
                result.was_extracted = True
                result.extracted_to = extract_dir
                logger.info("Extracted %d files to %s", len(extracted_files), extract_dir)

                # Delete ZIP after extraction?
                if config.get("delete_zip_after_extract", False):
                    file_path.unlink(missing_ok=True)
                    logger.info("Deleted source ZIP: %s", file_path)
            except Exception as exc:
                logger.error("Extraction failed: %s", exc)
                # Don't fail the whole download; report the partial result
                result.was_extracted = False

        # --- Launch folder? ------------------------------------------------
        if config.get("launch_folder_after_download", True):
            target = result.extracted_to if result.was_extracted else file_path
            if target and target.exists():
                self._launch_explorer(target)

        return result

    # -- Helpers -------------------------------------------------------------
    def _ensure_unique_dir(self, path: Path) -> Path:
        """Return a path for a directory that doesn't exist yet."""
        path.mkdir(parents=True, exist_ok=True)
        # If somehow it existed with content, add a suffix
        if path.exists() and any(path.iterdir()):
            idx = 1
            while True:
                candidate = Path(str(path) + f" ({idx})")
                if not candidate.exists():
                    candidate.mkdir(parents=True, exist_ok=True)
                    return candidate
                if not any(candidate.iterdir()):
                    return candidate
                idx += 1
        return path

    def _launch_explorer(self, target: Path) -> None:
        """Open a folder in Windows Explorer."""
        import subprocess
        try:
            if target.is_dir():
                subprocess.Popen(["explorer.exe", str(target)])
            else:
                subprocess.Popen(["explorer.exe", "/select,", str(target)])
            logger.info("Launched Explorer for %s", target)
        except Exception as exc:
            logger.warning("Failed to launch Explorer: %s", exc)
