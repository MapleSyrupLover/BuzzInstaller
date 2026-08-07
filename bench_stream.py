"""Benchmark streaming uploaders vs raw-bytes reference (320 MiB, best of 2)."""
import os, sys, time
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "src", "BuzzInstaller"))
import requests
from core.buzzheavier_api import USER_AGENT, UPLOAD_HOST

SIZE = 320 * 1024 * 1024
TEMP = os.environ.get("TEMP", "/tmp")
TEST = os.path.join(TEMP, "bz_stream.bin")
HDR = {"User-Agent": USER_AGENT, "Content-Type": "application/octet-stream"}
RUNS = 1
_ctr = 0

def make_file():
    if os.path.exists(TEST): return
    print("Creating 320 MiB file...", flush=True)
    with open(TEST, "wb") as f:
        c = b"0" * (8 * 1024 * 1024)
        for _ in range(SIZE // (8 * 1024 * 1024)): f.write(c)
    print("Ready.", flush=True)

def url(tag):
    global _ctr; _ctr += 1
    return f"{UPLOAD_HOST}/{tag}{_ctr}.bin"

def best(fn):
    bd = float("inf")
    for _ in range(RUNS):
        t0 = time.monotonic(); fn(); dt = time.monotonic() - t0
        if dt < bd: bd = dt
    return bd

class StreamFile:
    __slots__ = ("_f", "_chunk")
    def __init__(self, f, chunk): self._f, self._chunk = f, chunk
    def read(self, size=-1):
        return self._f.read(self._chunk)  # ignore requested size

def bench_bytes():
    p = open(TEST, "rb").read()
    def run():
        h = dict(HDR); h["Content-Length"] = str(SIZE)
        requests.put(url("bytes"), data=p, headers=h, timeout=(30, 600))
    return best(run)

def bench_stream(chunk):
    def run():
        h = dict(HDR); h["Content-Length"] = str(SIZE)
        with open(TEST, "rb") as f:
            requests.put(url(f"s{chunk//1024}k"), data=StreamFile(f, chunk),
                         headers=h, timeout=(30, 600))
    return best(run)

def main():
    make_file()
    print("=" * 70, flush=True)
    print(f"Streaming benchmark: {SIZE//(1024*1024)} MiB (best of {RUNS})", flush=True)
    print("=" * 70, flush=True)
    db = bench_bytes(); sb = SIZE / max(db, 1e-9) / (1024*1024)
    res = [("bytes (reference)", db, sb)]
    for chunk in (256*1024, 1024*1024, 4*1024*1024):
        print(f"  testing stream {chunk//1024} KiB...", flush=True)
        dt = bench_stream(chunk); sp = SIZE / max(dt, 1e-9) / (1024*1024)
        res.append((f"stream {chunk//1024} KiB", dt, sp))
    print("\n" + "=" * 70, flush=True)
    print("RESULTS", flush=True)
    print("=" * 70, flush=True)
    for name, dt, sp in res:
        pct = sb / sp * 100 if sp > 0 else 0
        print(f"  {name:<22} {dt:7.1f}s  {sp:6.2f} MiB/s  ({pct:.0f}% of bytes)", flush=True)
    os.remove(TEST)

if __name__ == "__main__":
    main()