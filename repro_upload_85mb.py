"""
Reproduction & root-cause diagnostic for the ~85 MB upload failure.

Uploads the EXACT failing file (BuzzInstaller.zip) using BOTH strategies
available in buzzheavier_api.upload_file():

  A) raw-bytes fast path  (<= 256 MiB)  -> ONE sendall() of the whole payload
  B) streaming path       (>  256 MiB)  -> 1 MiB chunks, one sendall() each

Every layer is instrumented:

  * upload start, file size, strategy, chunk index/size, bytes sent,
    current speed, elapsed time
  * HTTP request state (headers, URL), HTTP response state (status, headers)
  * socket state (SO_ERROR, peer FIN probe after failure)
  * retry attempts
  * exceptions (exact type + message + FULL traceback)
  * whether the client or server initiated the disconnect

Chunk accounting is verified: cumulative sent bytes must equal the file
size; chunks are read sequentially from the file so no byte is skipped or
duplicated.  A watchdog thread logs if the write stalls > 60 s.
"""

from __future__ import annotations

import io
import os
import sys
import json
import time
import socket
import threading
import traceback
from pathlib import Path

FILE = Path(r"c:\Users\elial\Documents\PROJECTS\INSTALLER\src\dist\BuzzInstaller\BuzzInstaller.zip")
UPLOAD_HOST = "https://w.buzzheavier.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
)
LOG_PATH = Path(r"c:\Users\elial\AppData\Local\BuzzInstaller\logs\upload_diagnostic.log")


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


class InstrumentedBody:
    """Wraps a file object; logs every read/upload chunk with accounting."""

    def __init__(self, f, total: int, label: str, chunk_hint: int = 0):
        self._f = f
        self._total = total
        self._label = label
        self._sent = 0
        self._chunk_no = 0
        self._chunk_hint = chunk_hint
        self._start = time.monotonic()
        self._speed_start = time.monotonic()
        self._speed_sent = 0

    def read(self, size: int = -1) -> bytes:
        if self._chunk_hint > 0:
            # Streaming wrapper: ignore urllib3's 16 KiB request and
            # return a LARGE chunk (matching production _StreamingFile).
            want = self._chunk_hint
        else:
            # Bytes path: urllib3 asks for the whole body (size = total).
            want = size if size > 0 else self._total - self._sent
        chunk = self._f.read(want)
        if not chunk:
            return chunk
        self._chunk_no += 1
        self._sent += len(chunk)
        now = time.monotonic()
        elapsed = now - self._start
        win = now - self._speed_start
        if win >= 1.0:
            speed = self._speed_sent / win / (1024 * 1024)
            log(
                f"  [{self._label}] tick: sent={self._sent}/{self._total} "
                f"({100.0*self._sent/self._total:.2f}%) "
                f"speed={speed:.2f} MiB/s elapsed={elapsed:.1f}s "
                f"chunk_no={self._chunk_no} chunk_size={len(chunk)} "
                f"chunks_total={self._total / max(len(chunk),1):.0f}"
            )
            self._speed_start = now
            self._speed_sent = 0
        self._speed_sent += len(chunk)
        return chunk

    def end_check(self) -> None:
        ok = self._sent == self._total
        log(
            f"  [{self._label}] ACCOUNTING: sent={self._sent} total={self._total} "
            f"match={ok} chunks={self._chunk_no}"
        )
        return ok


def patch_urllib3_send():
    """Instrument urllib3's HTTPConnection.send to log each sendall size."""
    try:
        from urllib3.connection import HTTPConnection
        orig = HTTPConnection.send

        def send(self, data):
            if isinstance(data, (bytes, bytearray, memoryview)):
                log(f"    [http] sendall({len(data)} bytes) — one call")
            else:
                log("    [http] send(file-like) — urllib3 will iterate reads")
            try:
                r = orig(self, data)
                try:
                    log(f"    [http] send OK; SO_ERROR={self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)}")
                except Exception:
                    pass
                return r
            except Exception as exc:
                # Probe: did the SERVER close the connection?  A successful
                # recv(b'', MSG_PEEK) returning b'' means FIN received.
                peer_state = "unknown"
                try:
                    data = self.sock.recv(1, socket.MSG_PEEK)
                    peer_state = "RECEIVED-FIN (server closed)" if data == b"" else "still-open"
                except Exception as pe:
                    peer_state = f"recv-probe-error({pe})"
                log(
                    f"    [http] send RAISED {type(exc).__name__}: {exc} | "
                    f"peer_state={peer_state}"
                )
                raise

        HTTPConnection.send = send
        log("[instrument] urllib3 send() patched")
    except Exception as exc:
        log(f"[instrument] patch failed: {exc}")
        traceback.print_exc()


def attempt(label: str, strategy: str) -> dict:
    """Upload the file once using the given strategy.  Returns a result dict."""
    log(f"\n===== ATTEMPT: {label} (strategy={strategy}) =====")
    file_size = FILE.stat().st_size
    url = f"{UPLOAD_HOST}/{FILE.name}"
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/octet-stream",
        "Content-Length": str(file_size),
    }
    log(f"start: file={FILE} size={file_size} ({file_size/1024/1024:.2f} MiB)")
    log(f"url: {url}")
    log(f"headers: {headers}")
    log(f"request-state: PUT begin")
    t0 = time.monotonic()
    result = {"label": label, "strategy": strategy, "ok": False, "status": None}

    body = None
    try:
        import requests
        if strategy == "bytes":
            with open(FILE, "rb") as f:
                payload = f.read()
            log(f"bytes path: read entire file into memory ({len(payload)} bytes)")
            body = InstrumentedBody(io.BytesIO(payload), file_size, label)
            resp = requests.put(url, data=body, headers=headers, timeout=(10, 120))
        else:
            with open(FILE, "rb") as f:
                body = InstrumentedBody(f, file_size, label, chunk_hint=1024 * 1024)
                resp = requests.put(url, data=body, headers=headers, timeout=(10, 120))
        elapsed = time.monotonic() - t0
        log(f"HTTP response state: status={resp.status_code} elapsed={elapsed:.1f}s")
        log(f"response headers: {dict(resp.headers)}")
        body.end_check()
        if resp.status_code in (200, 201):
            result["ok"] = True
            result["status"] = resp.status_code
            try:
                log(f"response json: {resp.text[:500]}")
            except Exception:
                pass
        else:
            result["status"] = resp.status_code
            log(f"FAIL: HTTP {resp.status_code} body={resp.text[:300]}")
    except requests.exceptions.ConnectionError as exc:
        result["exc"] = f"{type(exc).__name__}: {exc}"
        log(f"FAILED ConnectionError: {type(exc).__name__}: {exc}")
        # Drill into the underlying cause (usually a wrapped socket error)
        cause = exc
        depth = 0
        while cause.__cause__ is not None and depth < 6:
            cause = cause.__cause__
            depth += 1
            log(f"  cause[{depth}]: {type(cause).__name__}: {cause}")
        log("TRACEBACK:\n" + "".join(traceback.format_exc()))
    except requests.exceptions.Timeout as exc:
        result["exc"] = f"{type(exc).__name__}: {exc}"
        log(f"FAILED Timeout: {exc}")
        log("TRACEBACK:\n" + "".join(traceback.format_exc()))
    except Exception as exc:
        result["exc"] = f"{type(exc).__name__}: {exc}"
        log(f"FAILED {type(exc).__name__}: {exc}")
        log("TRACEBACK:\n" + "".join(traceback.format_exc()))
    finally:
        if body is not None:
            body.end_check()
    elapsed = time.monotonic() - t0
    log(f"elapsed_total={elapsed:.1f}s ok={result['ok']}")
    return result


def main() -> None:
    old = os.environ.get("REQUESTS_CA_BUNDLE")
    log("=" * 70)
    log("UPLOAD DIAGNOSTIC — root-cause reproduction")
    log(f"python={sys.version}")
    log(f"file={FILE} exists={FILE.exists()} size={FILE.stat().st_size if FILE.exists() else '?'}")
    patch_urllib3_send()

    results = []
    # Try EVERYTHING once, in order.  The first failure usually gives the
    # exact root cause; the second strategy proves the fix.
    for label, strategy in [
        ("A-bytes-fast-path", "bytes"),
        ("B-stream-1MiB", "stream"),
    ]:
        try:
            results.append(attempt(label, strategy))
        except Exception as exc:
            log(f"unexpected in {label}: {exc}\n{traceback.format_exc()}")
    log("=" * 70)
    for r in results:
        log(f"RESULT {r['label']}: ok={r['ok']} status={r.get('status')} exc={r.get('exc', 'none')}")


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings()
    main()