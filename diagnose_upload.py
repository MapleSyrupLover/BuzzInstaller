"""
Diagnose why ``requests.put(data=bytes)`` reaches ~3.8 MiB/s while
``requests.put(data=open(file,"rb"))`` reaches only ~0.43 MiB/s.

Produces measurements, not theories:
  1. requests: data=bytes                     (in-memory baseline)
  2. requests: data=file.read()               (disk read up-front, bytes body)
  3. requests: data=open(file,"rb")           (streamed file body)
  4. requests: data=instrumented file object  (streamed, records read() sizes)
  5. urllib3:   body=bytes
  6. urllib3:   body=file object
  7. httpx:     content=bytes                 (guarded)
  8. httpx:     content=file object           (guarded)

Also prints the prepared request headers for bytes vs file to compare
Content-Length / Transfer-Encoding / Expect and the measured read() sizes.
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "src", "BuzzInstaller"))

import requests

from core.buzzheavier_api import USER_AGENT, UPLOAD_HOST

SIZE = 10 * 1024 * 1024  # 10 MiB
TEMP = os.environ.get("TEMP", "/tmp")
TEST_PATH = os.path.join(TEMP, "bz_diag.bin")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/octet-stream",
}
RUNS = 3  # best-of-N per path to smooth network noise
_COUNTER = 0


def make_test_file() -> None:
    with open(TEST_PATH, "wb") as f:
        f.write(b"0" * SIZE)


def _next_url(tag: str) -> str:
    """Unique filename per upload so the server treats them independently."""
    global _COUNTER
    _COUNTER += 1
    return f"{UPLOAD_HOST}/{tag}{_COUNTER}.bin"


def best_of(fn):
    """Run fn RUNS times, return (best_dt, best_cpu, extra)."""
    best_dt, best_cpu, extra = float("inf"), float("inf"), None
    for _ in range(RUNS):
        t0 = time.monotonic()
        c0 = time.process_time()
        status, count, result = fn()
        dt = time.monotonic() - t0
        cpu = time.process_time() - c0
        if dt < best_dt:
            best_dt, best_cpu, extra = dt, cpu, (status, count)
    return best_dt, best_cpu, extra


class InstrumentedFile:
    """File wrapper that records every read() request size."""

    def __init__(self, f):
        self._f = f
        self.sizes = []
        self.calls = 0

    def read(self, n: int = -1):
        chunk = self._f.read(n)
        self.calls += 1
        self.sizes.append(len(chunk))
        return chunk

    def __getattr__(self, name):
        return getattr(self._f, name)


def main():
    make_test_file()

    print("=" * 78)
    print(f"Diagnosing bytes-vs-file upload speed: {SIZE // (1024 * 1024)} MiB, "
          f"{RUNS} runs per path (best)")
    conn_t0 = time.monotonic()

    # -- 1. bytes --------------------------------------------------------
    data = b"0" * SIZE

    def fn_bytes():
        url = _next_url("bytes")
        h = dict(HEADERS)
        r = requests.put(url, data=data, headers=h, timeout=(10, 120))
        return r.status_code, len(data), ""

    dt1, cpu1, (st1, _) = best_of(fn_bytes)
    sp1 = SIZE / max(dt1, 1e-9) / (1024 * 1024)

    # -- 2. file.read() ---------------------------------------------------
    payload = open(TEST_PATH, "rb").read()

    def fn_fileread():
        url = _next_url("fileread")
        h = dict(HEADERS)
        r = requests.put(url, data=payload, headers=h, timeout=(10, 120))
        return r.status_code, len(payload), ""

    dt2, cpu2, (st2, _) = best_of(fn_fileread)
    sp2 = SIZE / max(dt2, 1e-9) / (1024 * 1024)

    # -- 3. open(file) ----------------------------------------------------
    def fn_open():
        url = _next_url("openfile")
        h = dict(HEADERS)
        with open(TEST_PATH, "rb") as f:
            r = requests.put(url, data=f, headers=h, timeout=(10, 120))
        return r.status_code, SIZE, ""

    dt3, cpu3, (st3, _) = best_of(fn_open)
    sp3 = SIZE / max(dt3, 1e-9) / (1024 * 1024)

    # -- 4. instrumented file (read sizes) --------------------------------
    read_stats = {}

    def fn_inst():
        url = _next_url("inst")
        h = dict(HEADERS)
        with open(TEST_PATH, "rb") as raw:
            inst = InstrumentedFile(raw)
            r = requests.put(url, data=inst, headers=h, timeout=(10, 120))
            read_stats["calls"] = inst.calls
            read_stats["min"] = min(inst.sizes)
            read_stats["max"] = max(inst.sizes)
            read_stats["count_lt_8k"] = sum(1 for s in inst.sizes if s < 8192)
            read_stats["last"] = inst.sizes[-1] if inst.sizes else None
        return r.status_code, SIZE, ""

    dt4, cpu4, (st4, _) = best_of(fn_inst)
    sp4 = SIZE / max(dt4, 1e-9) / (1024 * 1024)

    # -- Prepared-request header comparison -------------------------------
    print("\n" + "-" * 78)
    print("Prepared request headers (before sending):")
    for label, body in (("data=bytes", data), ("data=open(file)", open(TEST_PATH, "rb"))):
        req = requests.Request("PUT", f"{UPLOAD_HOST}/hdr.bin",
                               data=body, headers=HEADERS)
        prep = req.prepare()
        print(f"\n  {label}:")
        for k, v in prep.headers.items():
            print(f"    {k}: {v}")
        print(f"    (body type: {type(body).__name__})")

    # -- 5/6. urllib3 direct ---------------------------------------------
    dt5 = dt6 = sp5 = sp6 = float("nan")
    try:
        import urllib3
        pool = urllib3.PoolManager()
        h = dict(HEADERS)

        def fn_u3_bytes():
            url = _next_url("u3bytes")
            r = pool.request("PUT", url, body=data, headers=h, timeout=120.0,
                             preload_content=False)
            status = r.status
            r.release_conn()
            return status, len(data), ""

        def fn_u3_file():
            url = _next_url("u3file")
            with open(TEST_PATH, "rb") as f:
                r = pool.request("PUT", url, body=f, headers=h, timeout=120.0,
                                 preload_content=False)
            status = r.status
            r.release_conn()
            return status, SIZE, ""

        dt5, cpu5, (st5, _) = best_of(fn_u3_bytes)
        sp5 = SIZE / max(dt5, 1e-9) / (1024 * 1024)
        dt6, cpu6, (st6, _) = best_of(fn_u3_file)
        sp6 = SIZE / max(dt6, 1e-9) / (1024 * 1024)
    except Exception as exc:
        print(f"\n  urllib3 direct: skipped ({exc})")

    # -- 7/8. httpx (guarded) ---------------------------------------------
    dt7 = dt8 = sp7 = sp8 = float("nan")
    try:
        import httpx

        def fn_httpx_bytes():
            url = _next_url("hxbytes")
            with httpx.Client(timeout=120.0, headers=HEADERS) as c:
                r = c.put(url, content=data)
            return r.status_code, len(data), ""

        def fn_httpx_file():
            url = _next_url("hxfile")
            with open(TEST_PATH, "rb") as f:
                with httpx.Client(timeout=120.0, headers=HEADERS) as c:
                    r = c.put(url, content=f)
            return r.status_code, SIZE, ""

        dt7, cpu7, (st7, _) = best_of(fn_httpx_bytes)
        sp7 = SIZE / max(dt7, 1e-9) / (1024 * 1024)
        dt8, cpu8, (st8, _) = best_of(fn_httpx_file)
        sp8 = SIZE / max(dt8, 1e-9) / (1024 * 1024)
    except Exception as exc:
        print(f"\n  httpx: skipped ({exc})")

    # -- Report ------------------------------------------------------------
    conn_total = time.monotonic() - conn_t0
    print("\n" + "=" * 78)
    print(f"RESULTS  ({SIZE // (1024 * 1024)} MiB -> {UPLOAD_HOST}, best of {RUNS})")
    print("=" * 78)

    def row(name, dt, sp, cpu, status, note=""):
        if dt == dt == float("inf"):
            print(f"  {name:<34} skipped")
            return
        print(f"  {name:<34} {dt:6.2f}s  {sp:5.2f} MiB/s  cpu {cpu:4.2f}s  "
              f"http {status} {note}")

    row("requests  data=bytes", dt1, sp1, cpu1, st1, "<-- baseline")
    row("requests  data=file.read()", dt2, sp2, cpu2, st2)
    row("requests  data=open(file)", dt3, sp3, cpu3, st3)
    row("requests  data=instr.file", dt4, sp4, cpu4, st4)
    if read_stats:
        print(f"    instrumented read(): calls={read_stats['calls']}, "
              f"min={read_stats['min']}, max={read_stats['max']}, "
              f"<8KiB={read_stats['count_lt_8k']}, last={read_stats['last']}")
    if sp2 is not float("nan"):
        print(f"\n  bytes-vs-file.read(): file.read() = {sp1/sp2 if sp2 else float('nan'):.2f}x of bytes")
        print(f"  read-vs-stream:  file.read() {sp2:.2f} vs open(file) {sp3:.2f} "
              f"-> {'disk-only' if abs(sp2 - sp3) < 0.1 else 'streaming overhead'}")
    if not (sp5 is float("nan")):
        row("urllib3  body=bytes", dt5, sp5, 0, st5)
        row("urllib3  body=file", dt6, sp6, 0, st6)
    if not (sp7 is float("nan")):
        row("httpx    content=bytes", dt7, sp7, 0, st7)
        row("httpx    content=file", dt8, sp8, 0, st8)

    print(f"\nTotal diagnostic wall time: {conn_total:.1f}s")

    os.remove(TEST_PATH)


if __name__ == "__main__":
    main()