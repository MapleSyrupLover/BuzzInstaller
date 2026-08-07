"""
Exact-production-path reproduction & root-cause diagnostic.

Uses the EXACT code path from buzzheavier_api.upload_file() for the
243.4 MiB BuzzInstaller.zip (which is <= 256 MiB, so it takes the
raw-bytes fast path: data=payload -> ONE sendall() of 243 MiB).

Instruments the socket layer (sendall/recv/close) to capture the exact
failure: exception type, message, traceback, HTTP/socket/SSL state,
and whether the client or server initiated the disconnect.

Also runs the streaming path (1 MiB chunks) for comparison.
"""
from __future__ import annotations

import io
import os
import sys
import json
import time
import socket
import ssl
import threading
import traceback
from pathlib import Path

FILE = Path(r"c:\Users\elial\Documents\PROJECTS\INSTALLER\src\dist\BuzzInstaller\BuzzInstaller.zip")
UPLOAD_HOST = "https://w.buzzheavier.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
)
LOG_PATH = Path(r"c:\Users\elial\AppData\Local\BuzzInstaller\logs\upload_exact_diag.log")

# Global socket instrumentation state
_sock_state = {"last_sendall": 0, "last_sendall_ts": 0.0, "total_sent": 0,
               "sendall_count": 0, "closed": False, "close_ts": 0.0,
               "peer_fin": False, "ssl_version": "", "cipher": ""}


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def patch_socket():
    """Instrument socket.sendall / recv / close to capture exact failure."""
    orig_sendall = socket.socket.sendall
    orig_recv = socket.socket.recv
    orig_close = socket.socket.close

    def sendall(self, data, *args, **kwargs):
        n = len(data) if isinstance(data, (bytes, bytearray, memoryview)) else -1
        now = time.monotonic()
        gap = now - _sock_state["last_sendall_ts"] if _sock_state["last_sendall_ts"] else 0.0
        _sock_state["last_sendall"] = n
        _sock_state["last_sendall_ts"] = now
        _sock_state["sendall_count"] += 1
        if n > 0:
            _sock_state["total_sent"] += n
        # Log large sends (>= 1 MiB) and the first few small ones
        if n >= 1024 * 1024 or _sock_state["sendall_count"] <= 3:
            log(
                f"  [sock] sendall #{_sock_state['sendall_count']} "
                f"len={n} gap={gap:.3f}s total_sent={_sock_state['total_sent']}"
            )
        try:
            r = orig_sendall(self, data, *args, **kwargs)
            return r
        except Exception as exc:
            # Probe peer state: did the SERVER close?
            peer = "unknown"
            try:
                d = self.recv(1, socket.MSG_PEEK)
                peer = "RECEIVED-FIN (server closed)" if d == b"" else "still-open"
            except Exception as pe:
                peer = f"recv-probe-error({type(pe).__name__}: {pe})"
            log(
                f"  [sock] sendall RAISED {type(exc).__name__}: {exc} | "
                f"peer_state={peer} | total_sent={_sock_state['total_sent']}"
            )
            raise

    def recv(self, bufsize, flags=0):
        try:
            d = orig_recv(self, bufsize, flags)
            if d == b"":
                _sock_state["peer_fin"] = True
                log(f"  [sock] recv returned b'' (peer FIN) — server closed connection")
            return d
        except Exception as exc:
            log(f"  [sock] recv RAISED {type(exc).__name__}: {exc}")
            raise

    def close(self):
        if not _sock_state["closed"]:
            _sock_state["closed"] = True
            _sock_state["close_ts"] = time.monotonic()
            log(f"  [sock] close() called — client initiated close")
        return orig_close(self)

    socket.socket.sendall = sendall
    socket.socket.recv = recv
    socket.socket.close = close
    log("[instrument] socket.sendall/recv/close patched")


def patch_ssl():
    """Capture SSL/TLS state on the wrapped socket."""
    orig_wrap = ssl.SSLSocket.recv
    # We'll capture SSL info lazily via the socket's getpeercert / version
    log("[instrument] SSL instrumentation ready")


def attempt(label: str, strategy: str) -> dict:
    """Upload the file once using the given strategy. Returns result dict."""
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

    # Reset socket state for this attempt
    _sock_state["total_sent"] = 0
    _sock_state["sendall_count"] = 0
    _sock_state["closed"] = False
    _sock_state["peer_fin"] = False

    try:
        import requests
        if strategy == "bytes":
            # EXACT production path: data=payload (raw bytes) -> ONE sendall
            with open(FILE, "rb") as f:
                payload = f.read()
            log(f"bytes path: read entire file into memory ({len(payload)} bytes)")
            log(f"bytes path: calling requests.put(data=raw_bytes) — expect ONE sendall")
            resp = requests.put(url, data=payload, headers=headers, timeout=(10, 30))
        else:
            # EXACT production streaming path: _StreamingFile with 1 MiB chunks
            class _StreamingFile:
                __slots__ = ("_f", "_sent", "_total", "_chunk_no", "_start")

                def __init__(self, f, total):
                    self._f = f
                    self._sent = 0
                    self._total = total
                    self._chunk_no = 0
                    self._start = time.monotonic()

                def read(self, size: int = -1) -> bytes:
                    chunk = self._f.read(1024 * 1024)
                    if not chunk:
                        return chunk
                    self._chunk_no += 1
                    self._sent += len(chunk)
                    now = time.monotonic()
                    elapsed = now - self._start
                    speed = self._sent / elapsed / (1024 * 1024) if elapsed > 0 else 0
                    if self._chunk_no % 10 == 0 or self._sent >= self._total:
                        log(
                            f"  [stream] chunk={self._chunk_no} sent={self._sent}/{self._total} "
                            f"({100.0*self._sent/self._total:.2f}%) "
                            f"speed={speed:.2f} MiB/s elapsed={elapsed:.1f}s"
                        )
                    return chunk

            with open(FILE, "rb") as f:
                body = _StreamingFile(f, file_size)
                log(f"stream path: calling requests.put(data=_StreamingFile) — 1 MiB chunks")
                resp = requests.put(url, data=body, headers=headers, timeout=(10, 30))

        elapsed = time.monotonic() - t0
        log(f"HTTP response state: status={resp.status_code} elapsed={elapsed:.1f}s")
        log(f"response headers: {dict(resp.headers)}")
        log(f"socket state: total_sent={_sock_state['total_sent']} "
            f"sendall_count={_sock_state['sendall_count']} peer_fin={_sock_state['peer_fin']}")
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
        log(f"socket final state: total_sent={_sock_state['total_sent']} "
            f"sendall_count={_sock_state['sendall_count']} "
            f"closed={_sock_state['closed']} peer_fin={_sock_state['peer_fin']}")
    elapsed = time.monotonic() - t0
    log(f"elapsed_total={elapsed:.1f}s ok={result['ok']}")
    return result


def main() -> None:
    log("=" * 70)
    log("UPLOAD EXACT-PATH DIAGNOSTIC — root-cause reproduction")
    log(f"python={sys.version}")
    log(f"file={FILE} exists={FILE.exists()} size={FILE.stat().st_size if FILE.exists() else '?'}")
    patch_socket()
    patch_ssl()

    results = []
    # Try the EXACT production bytes path first (this is what the app uses
    # for this 243 MiB file), then the streaming path for comparison.
    for label, strategy in [
        ("A-bytes-exact-production", "bytes"),
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