"""Release asset download helpers."""

from __future__ import annotations

import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from buzzcommon.release import ReleaseAsset, USER_AGENT

CHUNK_SIZE = 256 * 1024
DOWNLOAD_TIMEOUT = 120

ProgressCallback = Callable[[int, int], Optional[bool]]
CancelCallback = Callable[[], bool]


class DownloadError(Exception):
    """Base error for download failures."""


class DownloadCancelledError(DownloadError):
    """The user cancelled the download."""


class CorruptedDownloadError(DownloadError):
    """The downloaded file is empty or incomplete."""


def default_download_dir() -> Path:
    return Path(tempfile.gettempdir()) / "BuzzInstallerUpdate"


def download_asset(
    asset: ReleaseAsset,
    dest_dir: Optional[Path] = None,
    progress_cb: Optional[ProgressCallback] = None,
    cancelled: Optional[CancelCallback] = None,
) -> Path:
    """Download *asset* and return the local path."""
    if not asset.url:
        raise DownloadError(f"The release asset {asset.name!r} has no download URL.")

    dest_dir = Path(dest_dir or default_download_dir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / asset.name
    partial = dest.with_suffix(dest.suffix + ".part")

    request = urllib.request.Request(
        asset.url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )

    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            total = int(response.headers.get("Content-Length", 0) or asset.size or 0)
            received = 0
            with open(partial, "wb") as handle:
                while True:
                    if cancelled and cancelled():
                        partial.unlink(missing_ok=True)
                        raise DownloadCancelledError("Download cancelled.")
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    if progress_cb is not None:
                        if progress_cb(received, total) is False:
                            partial.unlink(missing_ok=True)
                            raise DownloadCancelledError("Download cancelled.")
    except DownloadCancelledError:
        raise
    except urllib.error.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"Download failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"Download failed: {exc.reason}") from exc
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"Download failed: {exc}") from exc

    if not partial.is_file() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise CorruptedDownloadError("The downloaded file is empty.")

    if asset.size and partial.stat().st_size < asset.size:
        partial.unlink(missing_ok=True)
        raise CorruptedDownloadError("The downloaded file is incomplete.")

    os.replace(partial, dest)
    return dest
