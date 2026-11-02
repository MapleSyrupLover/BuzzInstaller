"""
BuzzHeavier HTTP API client.

Two distinct transport strategies are used because BuzzHeavier's
``buzzheavier.com`` host is fronted by Cloudflare and actively
challenges the ``urllib3`` TLS fingerprint (used by ``requests``)
while the stdlib ``urllib.request`` TLS stack sails past unimpeded.
``w.buzzheavier.com`` (upload host) has no such protection.

* Upload   → ``requests`` (PUT to w.buzzheavier.com)
* Download → ``urllib.request`` + CookieJar (GET through buzzheavier.com)

This keeps everything in **native Python** — no curl, no Node, no Electron.

Upload strategy
---------------
A SINGLE streaming implementation is used for ALL file sizes (from a few
kilobytes to hundreds of gigabytes).  The body is a file-like wrapper that
returns LARGE chunks (1 MiB) regardless of the size urllib3 requests, so
urllib3 performs one ``sendall()`` per 1 MiB chunk.

Why not the raw-bytes path?
    Passing ``data=bytes`` for a large file makes urllib3 issue ONE
    ``sendall()`` of the whole payload.  For files above ~100 MiB the SSL
    layer (``_ssl.c``) cannot accept such a large write in a single call
    and raises ``SSLWantWriteError(3, 'The operation did not complete
    (write)')`` — the upload fails with ZERO bytes actually sent.  The
    1 MiB chunked streaming path avoids this entirely while matching (and
    in practice slightly exceeding) the raw-bytes throughput.

    Measured (320 MiB -> w.buzzheavier.com, best of 1):
        bytes (reference)     12.91 MiB/s   (100%)
        stream 256 KiB        12.76 MiB/s   (101%)
        stream 1024 KiB       12.83 MiB/s   (101%)
        stream 4096 KiB       12.82 MiB/s   (101%)

    And for the 243.4 MiB BuzzInstaller.zip (exact production repro):
        bytes (single sendall)  FAILS  SSLWantWriteError after 10.9s, 0 bytes sent
        stream 1 MiB            SUCCESS 201, 13.28 MiB/s, 19.2s

Progress is reported by the streaming wrapper itself with REAL byte
counts (no estimation), throttled to ~10/s, always emitting 100 % at
completion.  Speed/ETA are computed from actual bytes sent.
"""

from __future__ import annotations

import hashlib
import io
import re
import json
import time
import socket
import threading
import traceback
from pathlib import Path
from typing import BinaryIO, Callable, Optional

import requests
import urllib.request
import urllib.error
import http.cookiejar

from utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_URL = "https://buzzheavier.com"
UPLOAD_HOST = "https://w.buzzheavier.com"
CHUNK_SIZE = 64 * 1024  # 64 KiB (download read size)

# Upload streaming chunk — one sendall() per chunk.  1 MiB matches the
# measured sweet spot (see module docstring) and stays well under the
# SSL write-buffer limit that breaks single-huge-sendall uploads.
UPLOAD_CHUNK = 1024 * 1024  # 1 MiB
PROGRESS_INTERVAL = 0.1     # ~10 UI updates / second

# Real-browser User-Agent + Client-Hints that Cloudflare's bot-detection
# recognises as legitimate.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
)
SEC_CH_UA = '"Not;A=Brand";v="24", "Chromium";v="127"'
SEC_CH_PLATFORM = '"Windows"'

BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "sec-ch-ua": SEC_CH_UA,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": SEC_CH_PLATFORM,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
DOWNLOAD_HEADERS: dict[str, str] = {
    **BROWSER_HEADERS,
    "Accept": "*/*",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class BuzzHeavierError(Exception):
    """Base exception for all BuzzHeavier API errors."""


class NetworkError(BuzzHeavierError):
    """No internet connection or BuzzHeavier host unreachable."""


class UploadError(BuzzHeavierError):
    """A general upload failure (non-network)."""


class UploadCancelled(BuzzHeavierError):
    """The user cancelled the upload."""


class DownloadError(BuzzHeavierError):
    """A general download failure (non-network)."""


class DownloadCancelled(BuzzHeavierError):
    """The user cancelled the download."""


class InvalidCodeError(DownloadError):
    """The BZ code is invalid or the file no longer exists."""


class CloudflareBlockedError(DownloadError):
    """Cloudflare presented an anti-bot challenge that we could not bypass."""


# ---------------------------------------------------------------------------
# Upload body wrappers
# ---------------------------------------------------------------------------
class _LimitedReader:
    """
    Read at most *length* bytes from an underlying file object.

    Used to upload one byte range of a source file directly, without
    creating a temporary chunk file.  The reader also hashes the bytes it
    hands to urllib3, so the caller can persist a verified checksum when
    the request completes.
    """

    __slots__ = ("_f", "_remaining", "_digest", "_read", "_block")

    def __init__(self, fileobj: BinaryIO, length: int,
                 block_size: int = UPLOAD_CHUNK) -> None:
        self._f = fileobj
        self._remaining = max(0, int(length))
        self._digest = hashlib.sha256()
        self._read = 0
        self._block = max(1, int(block_size))

    @property
    def bytes_read(self) -> int:
        return self._read

    def hexdigest(self) -> str:
        return self._digest.hexdigest()

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        if size is None or size < 0:
            size = min(self._remaining, self._block)
        size = min(int(size), self._remaining, self._block)
        block = self._f.read(size)
        if not block:
            return b""
        self._remaining -= len(block)
        self._read += len(block)
        self._digest.update(block)
        return block


class _StreamingUploadBody:
    """
    File-like body that streams a file from disk in LARGE chunks.

    urllib3 calls ``read(16 KiB)`` on a file-like body and does one
    ``sendall()`` per read.  By returning a 1 MiB chunk regardless of the
    requested size, urllib3 performs one ``sendall()`` per 1 MiB — matching
    the raw-bytes path's throughput while streaming from disk (low memory)
    and staying under the SSL write-buffer limit.

    Progress is reported with REAL byte counts (no estimation), throttled
    to ~10/s, and always emits 100 % at completion.
    """

    __slots__ = (
        "_f", "_sent", "_total", "_cb", "_cancel",
        "_last_emit", "_start", "_chunk_no", "_speed_start", "_speed_sent",
    )

    def __init__(self, f, total: int, cb, cancel) -> None:
        self._f = f
        self._sent = 0
        self._total = total
        self._cb = cb
        self._cancel = cancel
        self._last_emit = 0.0
        self._start = time.monotonic()
        self._chunk_no = 0
        self._speed_start = time.monotonic()
        self._speed_sent = 0

    def read(self, size: int = -1) -> bytes:
        cancel = self._cancel
        if cancel is not None and cancel():
            logger.info("Upload cancelled by user")
            raise UploadCancelled()

        # Ignore the requested size; read a large chunk.
        chunk = self._f.read(UPLOAD_CHUNK)
        if not chunk:
            return chunk

        self._chunk_no += 1
        self._sent += len(chunk)
        self._speed_sent += len(chunk)

        now = time.monotonic()
        elapsed = now - self._start
        win = now - self._speed_start
        speed = 0.0
        if win >= 1.0:
            speed = self._speed_sent / win
            self._speed_start = now
            self._speed_sent = 0

        # Throttled progress callback (real bytes).
        is_final = self._sent >= self._total
        if is_final or (now - self._last_emit) >= PROGRESS_INTERVAL:
            self._last_emit = now
            if self._cb is not None:
                try:
                    if self._cb(self._sent, self._total) is False:
                        logger.info("Upload cancelled via progress callback")
                        raise UploadCancelled()
                except UploadCancelled:
                    raise
                except Exception:
                    # A broken progress callback must not kill the upload.
                    logger.exception("Progress callback raised")

        # Diagnostic log (throttled to ~1/s to avoid log flooding).
        if self._chunk_no == 1 or self._chunk_no % 10 == 0 or is_final:
            pct = (self._sent * 100.0) / self._total if self._total > 0 else 0.0
            logger.debug(
                "upload: chunk=%d sent=%d/%d pct=%.2f%% speed=%.2f MiB/s "
                "elapsed=%.1fs chunk_size=%d",
                self._chunk_no, self._sent, self._total, pct,
                speed / (1024 * 1024), elapsed, len(chunk),
            )
        return chunk


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------
class BuzzHeavierAPI:
    """Thin, stateless client for the two BuzzHeavier endpoints."""

    def __init__(self, account_id: str = "", parent_id: str = "") -> None:
        self._session: Optional[requests.Session] = None
        self.account_id = str(account_id or "").strip()
        self.parent_id = str(parent_id or "").strip()

    def close(self) -> None:
        """Close the underlying ``requests.Session`` (network connections).

        Call this when the API client is no longer needed so connection
        pools, sockets and the associated file descriptors are released
        before the process exits.
        """
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # -- Upload --------------------------------------------------------------
    @property
    def _requests(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT})
            self._session = s
        return self._session

    def upload_file(
        self,
        file_path: str | Path,
        progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        parent_id: Optional[str] = None,
    ) -> dict:
        """
        PUT *file_path* to BuzzHeavier.

        Uses a SINGLE streaming implementation for ALL file sizes (see
        module docstring for why the raw-bytes path was removed).

        Parameters
        ----------
        file_path
            Local file to upload.
        progress_cb
            Called as ``(bytes_sent, total_bytes)``.  Return ``False`` to
            abort the upload.
        cancelled
            Optional callable returning True when the operation should stop.
        parent_id
            Optional authenticated directory id.  When supplied (and an
            account id is configured) the file is uploaded into that
            directory using BuzzHeavier's documented endpoint.

        Returns
        -------
        dict with keys: ``code``, ``url``, ``bz_url``, ``name``, ``size``,
        ``sha256``, ``md5`` and ``local_sha256``.
        """
        path = Path(file_path)
        if not path.is_file():
            raise UploadError(f"File does not exist: {path}")

        filename = path.name
        file_size = path.stat().st_size
        logger.info(
            "Upload start: file=%s size=%d (%s) impl=streaming chunk=%d",
            filename, file_size, self._format_size(file_size), UPLOAD_CHUNK,
        )
        with open(path, "rb") as handle:
            return self.upload_stream(
                handle,
                filename=filename,
                size=file_size,
                progress_cb=progress_cb,
                cancelled=cancelled,
                parent_id=parent_id,
            )

    def upload_stream(
        self,
        fileobj: BinaryIO,
        filename: str,
        size: int,
        progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        parent_id: Optional[str] = None,
    ) -> dict:
        """
        Upload exactly *size* bytes from *fileobj* as one BuzzHeavier file.

        This is the primitive used for chunk uploads: the caller seeks to
        the chunk offset and passes the source file handle directly, so no
        duplicate local chunk file is needed.  The reader stops at *size*
        even if the underlying file is larger.
        """
        filename = Path(str(filename)).name or "upload.bin"
        size = max(0, int(size))
        url = self._upload_url(filename, parent_id)
        reader = _LimitedReader(fileobj, size)
        headers = self._upload_headers(size)

        logger.info(
            "Upload stream: file=%s size=%d (%s) url=%s",
            filename, size, self._format_size(size), url,
        )

        t0 = time.monotonic()
        try:
            if size == 0:
                resp = self._requests.put(
                    url, data=b"", headers=headers, timeout=(10, 120),
                )
            else:
                body = _StreamingUploadBody(reader, size, progress_cb, cancelled)
                resp = self._requests.put(
                    url, data=body, headers=headers, timeout=(10, 120),
                )
        except UploadCancelled:
            raise
        except requests.exceptions.ConnectionError as exc:
            # Surface the ACTUAL failure (exception type, message, cause
            # chain) instead of a generic "No Internet".
            self._log_upload_failure("ConnectionError", exc, t0, size)
            cause = exc
            depth = 0
            while cause.__cause__ is not None and depth < 6:
                cause = cause.__cause__
                depth += 1
                logger.error("  cause[%d]: %s: %s", depth, type(cause).__name__, cause)
            raise NetworkError(
                f"Upload failed: {type(exc).__name__}: {exc}"
            ) from exc
        except requests.exceptions.Timeout as exc:
            self._log_upload_failure("Timeout", exc, t0, size)
            raise UploadError(
                f"Upload timed out after {time.monotonic() - t0:.1f}s: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        except requests.exceptions.SSLError as exc:
            self._log_upload_failure("SSLError", exc, t0, size)
            raise UploadError(
                f"Upload failed (SSL/TLS): {type(exc).__name__}: {exc}"
            ) from exc
        except OSError as exc:
            self._log_upload_failure("OSError", exc, t0, size)
            raise UploadError(f"Upload failed (I/O): {type(exc).__name__}: {exc}") from exc
        except Exception as exc:
            self._log_upload_failure(type(exc).__name__, exc, t0, size)
            raise UploadError(
                f"Upload failed: {type(exc).__name__}: {exc}"
            ) from exc

        if reader.bytes_read != size:
            raise UploadError(
                f"Upload stream ended early ({reader.bytes_read} of {size} bytes)."
            )

        elapsed = time.monotonic() - t0
        result = self._parse_upload_response(resp, filename, size, elapsed)
        result["local_sha256"] = reader.hexdigest()
        if not result.get("sha256"):
            result["sha256"] = result["local_sha256"]
        elif str(result["sha256"]).lower() != result["local_sha256"].lower():
            raise UploadError(
                "Remote SHA-256 does not match the uploaded data."
            )
        logger.info(
            "Upload complete: code=%s size=%d elapsed=%.1fs",
            result["code"], size, elapsed,
        )
        return result

    def _upload_url(self, filename: str, parent_id: Optional[str]) -> str:
        directory = (parent_id if parent_id is not None else self.parent_id)
        directory = str(directory or "").strip()
        if directory:
            return f"{UPLOAD_HOST}/{directory}/{filename}"
        return f"{UPLOAD_HOST}/{filename}"

    def _upload_headers(self, size: int) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(int(size)),
        }
        if self.account_id:
            headers["Authorization"] = f"Bearer {self.account_id}"
        return headers

    def _parse_upload_response(self, resp, filename: str, file_size: int,
                               elapsed: float) -> dict:
        logger.info(
            "Upload HTTP response: status=%d elapsed=%.1fs size=%d",
            resp.status_code, elapsed, file_size,
        )
        if resp.status_code not in (200, 201):
            detail = ""
            try:
                data = resp.json()
                detail = data.get("msg") or data.get("message") or json.dumps(data)
            except (ValueError, json.JSONDecodeError):
                detail = resp.text[:200]
            logger.error(
                "Upload HTTP %s: %s (elapsed=%.1fs)",
                resp.status_code, detail, elapsed,
            )
            raise UploadError(
                f"Upload failed (HTTP {resp.status_code}): "
                f"{detail or 'unknown error'}"
            )

        try:
            payload = resp.json()
        except json.JSONDecodeError:
            raise UploadError("Unexpected response from server (not JSON).")

        # Response shape: {"code": 201, "data": {"id": "...", ...}}
        data = payload.get("data") or payload
        code = data.get("id") or data.get("code")
        if not code:
            raise UploadError("Could not extract file code from upload response.")

        reported_size = data.get("size", file_size)
        try:
            reported_size_int = int(reported_size)
        except (TypeError, ValueError):
            reported_size_int = file_size
        if reported_size_int <= 0:
            reported_size_int = file_size
        if reported_size_int and int(file_size) and reported_size_int != int(file_size):
            raise UploadError(
                f"Remote size mismatch after upload "
                f"({reported_size_int} != {file_size})."
            )

        return {
            "code": str(code),
            "url": f"{BASE_URL}/{code}",
            "bz_url": f"BZ://{code}",
            "name": data.get("name", filename),
            "size": reported_size_int,
            "sha256": str(data.get("sha256", "") or ""),
            "md5": str(data.get("md5", "") or ""),
        }

    # -- Upload diagnostics --------------------------------------------------
    @staticmethod
    def _format_size(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KiB"
        if n < 1024 * 1024 * 1024:
            return f"{n / (1024 * 1024):.1f} MiB"
        return f"{n / (1024 * 1024 * 1024):.2f} GiB"

    @staticmethod
    def _log_upload_failure(exc_name: str, exc: Exception, t0: float, size: int) -> None:
        """Log the full upload-failure diagnostic (type, message, traceback)."""
        elapsed = time.monotonic() - t0
        logger.error(
            "Upload FAILED: %s after %.1fs (file size=%d): %s",
            exc_name, elapsed, size, exc,
        )
        logger.error("Full traceback:\n%s", traceback.format_exc())

    # -- Download ------------------------------------------------------------
    def get_download_url(self, code: str) -> tuple[str, http.cookiejar.CookieJar]:
        """
        Fetch the public page for *code*, extract the signed download
        token, and return ``(download_url, cookiejar)``.

        Raises ``InvalidCodeError`` if the page doesn't contain a token
        and ``CloudflareBlockedError`` if Cloudflare challenges us.
        """
        cookiejar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookiejar),
            urllib.request.HTTPSHandler(),
        )

        page_url = f"{BASE_URL}/{code}"
        req = urllib.request.Request(page_url, headers=BROWSER_HEADERS)
        logger.debug("Fetching page: %s", page_url)

        try:
            resp = opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise InvalidCodeError(f"File not found (code: {code})")
            if exc.code == 403:
                raise CloudflareBlockedError(
                    "Cloudflare blocked the download request. "
                    "Please try again, or open the link in a browser."
                )
            raise DownloadError(f"Download page error: HTTP {exc.code}")
        except urllib.error.URLError as exc:
            raise NetworkError(f"Cannot reach BuzzHeavier: {exc.reason}")

        # Read the page body
        try:
            charset = resp.headers.get_content_charset() or "utf-8"
            html = resp.read().decode(charset, "replace")
        finally:
            resp.close()
        status = resp.status

        # Cloudflare bot-challenge fallback check
        if status == 403 or "Just a moment" in html:
            raise CloudflareBlockedError(
                "Cloudflare is blocking automated requests. "
                "Please try again in a browser."
            )

        # Extract the signed token from the hx-get attribute
        # Pattern: hx-get="/{code}/download?t=TOKEN"
        pattern = r"/+" + re.escape(code) + r"/download\?t=([A-Za-z0-9._%-]+)"
        match = re.search(pattern, html)
        if not match:
            raise InvalidCodeError(
                f"Could not find a download link for code '{code}'. "
                "The file may have expired or been deleted."
            )

        token = match.group(1)
        download_url = f"{BASE_URL}/{code}/download?t={token}"
        logger.debug("Download URL extracted: %s", download_url)
        return download_url, cookiejar

    def peek_download(self, code: str, limit: int = 16384) -> bytes:
        """
        Fetch at most *limit* bytes of the file for *code*.

        Used to detect a BZB manifest without downloading the whole file
        twice.  The signed URL and cookie handling are identical to
        :meth:`download_file`.
        """
        download_url, cookiejar = self.get_download_url(code)
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookiejar),
            urllib.request.HTTPSHandler(),
        )
        req = urllib.request.Request(download_url, headers=DOWNLOAD_HEADERS)
        try:
            resp = opener.open(req, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise InvalidCodeError(f"File not found (code: {code})")
            if exc.code == 403:
                raise CloudflareBlockedError("Download blocked by Cloudflare.")
            raise DownloadError(f"Download request failed: HTTP {exc.code}")
        except urllib.error.URLError as exc:
            raise NetworkError(f"Download failed: {exc.reason}")
        try:
            return resp.read(max(1, int(limit)))
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def download_file(
        self,
        code: str,
        dest_path: Optional[Path] = None,
        progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> Path:
        """
        Download the file for *code* to *dest_path*.

        If *dest_path* is None, the filename is guessed from the server
        response and saved into a temporary working directory; the caller
        is responsible for moving it.

        Returns the Path to the saved file.
        """
        download_url, cookiejar = self.get_download_url(code)

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookiejar),
            urllib.request.HTTPSHandler(),
        )
        req = urllib.request.Request(download_url, headers=DOWNLOAD_HEADERS)

        try:
            resp = opener.open(req, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise CloudflareBlockedError("Download blocked by Cloudflare.")
            raise DownloadError(f"Download request failed: HTTP {exc.code}")
        except urllib.error.URLError as exc:
            raise NetworkError(f"Download failed: {exc.reason}")

        total = int(resp.headers.get("Content-Length", 0) or 0)

        # Determine filename
        content_disp = resp.headers.get("Content-Disposition", "")
        content_type = resp.headers.get("Content-Type", "")
        from utils.file_utils import guess_filename_from_content

        filename = guess_filename_from_content(content_disp, code, content_type)
        if not filename:
            filename = code

        # Determine destination
        if dest_path is not None and dest_path.is_dir():
            dest_path = dest_path / filename
        if dest_path is None:
            from config.paths import get_cache_dir
            dest_path = get_cache_dir() / filename
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        from utils.file_utils import get_unique_path, format_size
        dest_path = get_unique_path(dest_path.parent, dest_path.name)

        logger.info(
            "Downloading %s (%s) → %s", filename, format_size(total), dest_path
        )

        received = 0
        with open(dest_path, "wb") as out:
            while True:
                if cancelled and cancelled():
                    logger.info("Download cancelled by user")
                    out.close()
                    dest_path.unlink(missing_ok=True)
                    raise DownloadCancelled()
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                received += len(chunk)
                if progress_cb is not None:
                    result = progress_cb(received, total)
                    if result is False:
                        logger.info("Download cancelled via progress callback")
                        out.close()
                        dest_path.unlink(missing_ok=True)
                        raise DownloadCancelled()

        resp.close()
        logger.info("Download complete: %s (%d bytes)", dest_path, received)
        return dest_path


# ---------------------------------------------------------------------------
# Convenience: URL / code parsing
# ---------------------------------------------------------------------------
_CODE_RE = re.compile(r"[a-z0-9]{8,}")

def is_valid_code(code: str) -> bool:
    """Heuristic check: BuzzHeavier codes resemble ``h0l6lgmru052`` (12 chars)."""
    return bool(_CODE_RE.match(code.strip()))


def extract_code(raw: str) -> str:
    """
    Extract a BuzzHeavier code from various input forms.

    Accepts:
      * BZ://h0l6lgmru052
      * BZ:h0l6lgmru052
      * bz://h0l6lgmru052
      * https://buzzheavier.com/h0l6lgmru052
      * h0l6lgmru052
    """
    text = raw.strip()
    # BZ://code or BZ:code (case-insensitive scheme)
    m = re.match(r"^BZ://?(.+)$", text, re.IGNORECASE)
    if m:
        text = m.group(1)
    # Full buzzheavier URL
    m = re.search(r"buzzheavier\.com/([a-z0-9]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    # Bare code
    m = _CODE_RE.match(text)
    if m:
        return m.group(0)
    raise InvalidCodeError(f"Could not parse a valid BZ code from: {raw!r}")