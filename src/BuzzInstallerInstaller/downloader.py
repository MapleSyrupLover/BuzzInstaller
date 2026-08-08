"""
Downloader for BuzzInstaller release assets.

Downloads the BuzzInstaller.exe asset from GitHub with progress reporting
and validation.  Uses only the standard library (``urllib.request``).
"""

from __future__ import annotations

import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from github_api import ReleaseAsset, USER_AGENT

# Download chunk size (64 KiB)
CHUNK_SIZE = 64 * 1024

# Download timeout (seconds)
DOWNLOAD_TIMEOUT = 120


class DownloadError(Exception):
    """Base error for download failures."""


class DownloadFailedError(DownloadError):
    """Raised when the download fails."""


class DownloadCancelledError(DownloadError):
    """Raised when the user cancels the download."""


class CorruptedDownloadError(DownloadError):
    """Raised when the downloaded file is invalid (empty, wrong size)."""


ProgressCallback = Callable[[int, int], Optional[bool]]


def download_asset(
    asset: ReleaseAsset,
    dest_dir: Optional[Path] = None,
    progress_cb: Optional[ProgressCallback] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Path:
    """
    Download *asset* to *dest_dir* (or a temp dir if None).

    Returns the path to the downloaded file.

    Raises:
        DownloadFailedError — network / HTTP failure
        DownloadCancelledError — user cancelled
        CorruptedDownloadError — file is empty or size mismatch
    """
    if dest_dir is None:
        dest_dir = Path(tempfile.gettempdir()) / "BuzzInstallerInstaller"
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / asset.name

    req = urllib.request.Request(
        asset.url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )

    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            status = resp.status
            if status != 200:
                raise DownloadFailedError(
                    f"Download failed: HTTP {status}"
                )

            total = int(resp.headers.get("Content-Length", 0) or 0)
            received = 0

            with open(dest_path, "wb") as out:
                while True:
                    if cancelled and cancelled():
                        out.close()
                        dest_path.unlink(missing_ok=True)
                        raise DownloadCancelledError("Download cancelled by user")

                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    received += len(chunk)

                    if progress_cb is not None:
                        result = progress_cb(received, total)
                        if result is False:
                            out.close()
                            dest_path.unlink(missing_ok=True)
                            raise DownloadCancelledError(
                                "Download cancelled via progress callback"
                            )

    except urllib.error.HTTPError as exc:
        dest_path.unlink(missing_ok=True)
        raise DownloadFailedError(f"Download failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        dest_path.unlink(missing_ok=True)
        raise DownloadFailedError(f"Download failed: {exc.reason}") from exc
    except OSError as exc:
        dest_path.unlink(missing_ok=True)
        raise DownloadFailedError(f"Download failed (I/O): {exc}") from exc

    # --- Validation ------------------------------------------------------
    if not dest_path.exists():
        raise CorruptedDownloadError("Downloaded file does not exist")

    file_size = dest_path.stat().st_size
    if file_size == 0:
        dest_path.unlink(missing_ok=True)
        raise CorruptedDownloadError("Downloaded file is empty")

    # If the server told us the expected size, verify it matches.
    if total > 0 and file_size != total:
        dest_path.unlink(missing_ok=True)
        raise CorruptedDownloadError(
            f"Download incomplete: expected {total} bytes, got {file_size}"
        )

    return dest_path