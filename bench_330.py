"""
Benchmark every available HTTP implementation against the browser's
12-13 MB/s upload, using a 330 MB file.

Available on this machine:
  * curl 8.21.0 (Schannel = native Windows TLS)  <-- likely matches browser
  * requests (OpenSSL via urllib3)
  * urllib3 direct (OpenSSL)

Reports per implementation:
  * Upload speed (MiB/s)
  * Total time
  * CPU time (where measurable)
  * HTTP version
  * TLS version
  * Request headers (from curl -v / prepared request)
"""
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "src", "BuzzInstaller"))

import requests

from core.buzzheavier_api import USER_AGENT, UPLOAD_HOST

SIZE = 330 * 1024 * 1024  # 330 MiB
TEMP = os.environ.get("TEMP", "/tmp")
TEST_PATH = os.path.join(TEMP, "bz_330.bin")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/octet-stream",
}
RUNS = 2  # 330 MiB is big; best-of-2 to limit time
_COUNTER = 0


def make_test_file() -> None:
    if os.path.exists(TEST_PATH):
        return
    print(f"Creating {SIZE // (1024 * 1024)} MiB test file...", flush=True)
    with open(TEST_PATH, "wb") as f:
        # Write in 8 MiB chunks to avoid huge single allocation
        chunk = b"0" * (8 * 1024 * 1024)
        for _ in range(SIZE // (8 * 1024 * 1024)):
            f.write(chunk)
    print("Test file ready.", flush=True)


def _next_url(tag: str) -> str:
    global _COUNTER
    _COUNTER += 1
    return f"{UPLOAD_HOST}/{tag}{_COUNTER}.bin"


def best_of(fn, runs=RUNS):
    best_dt, best_cpu, extra = float("inf"), float("inf"), None
    for _ in range(runs):
        t0 = time.monotonic()
        c0 = time.process_time()
        status, info = fn()
        dt = time.monotonic() - t0
        cpu = time.process_time() - c0
        if dt < best_dt:
            best_dt, best_cpu, extra = dt, cpu, (status, info)
    return best_dt, best_cpu, extra


def bench_curl() -> tuple[float, float, tuple]:
    """curl with Schannel TLS. Use -w to capture HTTP version + speed."""
    def run():
        url = _next_url("curl")
        # -w writes timing/version info to stderr; --http1.1 vs default
        # (curl may negotiate HTTP/2). Capture both.
        cmd = [
            "curl", "-sS", "-o", "NUL", "-w",
            "http=%{http_version} tls=%{ssl_verify_result} "
            "speed=%{speed_upload} time=%{time_total}",
            "-X", "PUT", "-H", f"User-Agent: {USER_AGENT}",
            "-H", "Content-Type: application/octet-stream",
            "--data-binary", "@" + TEST_PATH, url,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return r.returncode, r.stderr.strip()
    return best_of(run)


def bench_requests_bytes() -> tuple[float, float, tuple]:
    """requests with in-memory bytes body (our current fast path)."""
    payload = open(TEST_PATH, "rb").read()

    def run():
        url = _next_url("reqbytes")
        h = dict(HEADERS)
        r = requests.put(url, data=payload, headers=h, timeout=(30, 600))
        return r.status_code, f"http={r.raw.version}"
    return best_of(run)


def bench_urllib3_bytes() -> tuple[float, float, tuple]:
    """urllib3 direct with bytes body."""
    import urllib3
    pool = urllib3.PoolManager()
    payload = open(TEST_PATH, "rb").read()

    def run():
        url = _next_url("u3bytes")
        h = dict(HEADERS)
        r = pool.request("PUT", url, body=payload, headers=h,
                         timeout=600.0, preload_content=False)
        status = r.status
        r.release_conn()
        return status, "urllib3"
    return best_of(run)


def main():
    make_test_file()
    print("=" * 78, flush=True)
    print(f"Benchmark: {SIZE // (1024 * 1024)} MiB -> {UPLOAD_HOST} "
          f"(best of {RUNS})", flush=True)
    print("=" * 78, flush=True)

    # curl first (most likely to match browser)
    print("\n[1/3] curl (Schannel TLS)...", flush=True)
    c_dt, c_cpu, (c_st, c_info) = bench_curl()
    c_sp = SIZE / max(c_dt, 1e-9) / (1024 * 1024)

    print("[2/3] requests (bytes body)...", flush=True)
    r_dt, r_cpu, (r_st, r_info) = bench_requests_bytes()
    r_sp = SIZE / max(r_dt, 1e-9) / (1024 * 1024)

    print("[3/3] urllib3 (bytes body)...", flush=True)
    u_dt, u_cpu, (u_st, u_info) = bench_urllib3_bytes()
    u_sp = SIZE / max(u_dt, 1e-9) / (1024 * 1024)

    print("\n" + "=" * 78, flush=True)
    print("RESULTS (330 MiB, best of %d)" % RUNS, flush=True)
    print("=" * 78, flush=True)

    def row(name, dt, sp, cpu, status, info):
        print(f"  {name:<28} {dt:7.1f}s  {sp:6.2f} MiB/s  "
              f"cpu {cpu:5.1f}s  {status}  {info}", flush=True)

    row("curl (Schannel)", c_dt, c_sp, c_cpu, c_st, c_info)
    row("requests (bytes)", r_dt, r_sp, r_cpu, r_st, r_info)
    row("urllib3 (bytes)", u_dt, u_sp, u_cpu, u_st, u_info)

    print("\n" + "-" * 78, flush=True)
    print("curl -v header/TLS detail (first run):", flush=True)
    url = _next_url("curlv")
    cmd = [
        "curl", "-v", "-X", "PUT", "-H", f"User-Agent: {USER_AGENT}",
        "-H", "Content-Type: application/octet-stream",
        "--data-binary", "@" + TEST_PATH, url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    # Print the TLS/HTTP negotiation lines
    for line in r.stderr.splitlines():
        if any(k in line for k in ("SSL connection", "TLS", "HTTP/", "ALPN",
                                   "Connected to", "PUT /", "> ")):
            print("   " + line, flush=True)
    print("-" * 78, flush=True)

    os.remove(TEST_PATH)


if __name__ == "__main__":
    main()