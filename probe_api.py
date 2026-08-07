import sys, io, json, requests

print("start", flush=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0")

# ---- Upload probe ----
fname = "bz_probe_test.txt"
content = b"hello probe"
headers = {"User-Agent": UA}
url = f"https://w.buzzheavier.com/{fname}"
try:
    r = requests.put(url, data=io.BytesIO(content), headers=headers, timeout=30)
    print("UPLOAD STATUS:", r.status_code, flush=True)
    print("UPLOAD HEADERS:", dict(r.headers), flush=True)
    print("UPLOAD BODY:", repr(r.text[:800]), flush=True)
    try:
        print("UPLOAD JSON:", json.loads(r.text), flush=True)
    except Exception:
        print("UPLOAD: NOT JSON", flush=True)
except Exception as e:
    print("UPLOAD ERROR:", type(e).__name__, e, flush=True)
