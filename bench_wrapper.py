"""
Benchmark: plain ``requests.put(data=open(...))`` vs the redesigned
``_ProgressFile`` pass-through wrapper.

Measures (best-of-3 to reduce network variance):
  * Upload speed (MiB/s)
  * Total upload time
  * Wrapper overhead percentage vs plain
  * CPU time (``time.process_time``)
  * Progress-callback invocations (= throttled Qt signal emissions)
"""
import os
import sys
import time

# Make ``src/BuzzInstaller`` importable
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "src", "BuzzInstaller"))

import requests

from core.buzzheavier_api import BuzzHeavierAPI, USER_AGENT, UPLOAD_HOST

SIZE = 10 * 1024 * 1024  # 10 MiB
TEMP = os.environ.get("TEMP", "/tmp")
TEST_PATH = os.path.join(TEMP, "bz_bench.bin")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/octet-stream",
    "Content-Length": str(SIZE),
}
RUNS = 3  # best-of-N to smooth network noise


def make_test_file() -> None:
    with open(TEST_PATH, "wb") as f:
        f.write(b"0" * SIZE)


def bench_plain() -> tuple[float, float, int]:
    """True baseline: requests.put(data=open(...)) — no wrapper at all."""
    dt_min, cpu_min = float("inf"), float("inf")
    status = 0
    for _ in range(RUNS):
        t0 = time.monotonic()
        c0 = time.process_time()
        with open(TEST_PATH, "rb") as f:
            r = requests.put(f"{UPLOAD_HOST}/bench_plain.bin",
                             data=f, headers=HEADERS, timeout=(10, 120))
        dt = time.monotonic() - t0
        cpu = time.process_time() - c0
        status = r.status_code
        if dt < dt_min:
            dt_min, cpu_min = dt, cpu
    return dt_min, cpu_min, status


def bench_no_cb() -> tuple[float, float, int]:
    """api.upload_file with no progress callback -> pure wrapper overhead."""
    api = BuzzHeavierAPI()
    dt_min, cpu_min = float("inf"), float("inf")
    code = ""
    for _ in range(RUNS):
        t0 = time.monotonic()
        c0 = time.process_time()
        result = api.upload_file(TEST_PATH)
        dt = time.monotonic() - t0
        cpu = time.process_time() - c0
        code = result["code"]
        if dt < dt_min:
            dt_min, cpu_min = dt, cpu
    return dt_min, cpu_min, code


def bench_with_cb() -> tuple[float, float, int, int]:
    """api.upload_file with a throttled progress callback."""
    api = BuzzHeavierAPI()
    dt_min, cpu_min = float("inf"), float("inf")
    code, calls = "", 0
    for _ in range(RUNS):
        n_calls = 0

        def cb(done, total):
            nonlocal n_calls
            n_calls += 1
            return True

        t0 = time.monotonic()
        c0 = time.process_time()
        result = api.upload_file(TEST_PATH, progress_cb=cb)
        dt = time.monotonic() - t0
        cpu = time.process_time() - c0
        code = result["code"]
        if dt < dt_min:
            dt_min, cpu_min, calls = dt, cpu, n_calls
    return dt_min, cpu_min, code, calls


def main() -> None:
    make_test_file()
    print("=" * 72)
    print(f"Benchmark: {SIZE // (1024 * 1024)} MiB -> {UPLOAD_HOST}  (best of {RUNS})")
    print("=" * 72)

    plain_dt, plain_cpu, plain_status = bench_plain()
    nocb_dt, nocb_cpu, nocb_code = bench_no_cb()
    cb_dt, cb_cpu, cb_code, cb_calls = bench_with_cb()

    def fmt(name, dt, cpu, status, calls=None, speed=None):
        speed = speed if speed is not None else SIZE / max(dt, 1e-9) / (1024 * 1024)
        print(f"\n{name}:")
        print(f"   status/code:     {status}")
        print(f"   total time:      {dt:.2f} s")
        print(f"   upload speed:    {speed:.2f} MiB/s")
        print(f"   CPU time:        {cpu:.2f} s  ({cpu / max(dt, 1e-9) * 100:.1f}% of wall)")
        if calls is not None:
            print(f"   progress calls:  {calls}  (= throttled Qt signal emissions)")

    fmt("1. plain requests.put(data=open())", plain_dt, plain_cpu, plain_status)
    fmt("2. api.upload_file (no callback)", nocb_dt, nocb_cpu, nocb_code)
    fmt("3. api.upload_file (throttled callback)", cb_dt, cb_cpu, cb_code, cb_calls)

    def overhead(dt, label):
        pct = (dt / plain_dt - 1) * 100
        keep = plain_dt / dt * 100 if dt > 0 else 0
        ok = ">= 90%" if keep >= 90 else "< 90%"
        print(f"   {label}: time {'+' if pct >= 0 else ''}{pct:.1f}%  "
              f"throughput {keep:.1f}% of plain  [{ok}]")

    print("\n" + "=" * 72)
    print("Overhead vs plain requests.put():")
    overhead(nocb_dt, "2. wrapper, no  callback ")
    overhead(cb_dt, "3. wrapper, + throttled cb")
    print("=" * 72)

    os.remove(TEST_PATH)


if __name__ == "__main__":
    main()