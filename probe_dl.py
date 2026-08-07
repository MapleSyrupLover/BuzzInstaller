import sys, io, json, re, os, requests

print("start download probe", flush=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0")

SEC = {
    "sec-ch-ua": '"Not;A=Brand";v="24", "Chromium";v="127"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

code = "s7av2loeg1wd"
page_url = f"https://buzzheavier.com/{code}"

# Step 1: fetch the public page
s = requests.Session()
s.headers.update({"User-Agent": UA, **SEC})
try:
    resp = s.get(page_url, timeout=30, allow_redirects=True)
    print("PAGE STATUS:", resp.status_code, flush=True)
    print("PAGE URL:", resp.url, flush=True)
    print("PAGE HEADERS:", dict(resp.headers), flush=True)
    print("PAGE LEN:", len(resp.text), flush=True)
    print("PAGE CONTENT-TYPE:", resp.headers.get("Content-Type"), flush=True)
except Exception as e:
    print("PAGE ERROR:", type(e).__name__, e, flush=True)
    sys.exit(1)

# Step 2: extract token
m = re.search(r'/'+re.escape(code)+r'/download\?t=([A-Za-z0-9._%-]+)', resp.text)
if not m:
    print("TOKEN NOT FOUND, snippet:", resp.text[:600], flush=True)
    sys.exit(1)
token = m.group(1)
print("TOKEN:", token, flush=True)

# Step 3: download
dl_url = f"https://buzzheavier.com/{code}/download?t={token}"
try:
    resp2 = s.get(dl_url, timeout=30, stream=True)
    print("DL STATUS:", resp2.status_code, flush=True)
    print("DL HEADERS:", dict(resp2.headers), flush=True)
    print("DL CONTENT-TYPE:", resp2.headers.get("Content-Type"), flush=True)
    print("DL DISPOSITION:", resp2.headers.get("Content-Disposition"), flush=True)
    total = 0
    body = b""
    for chunk in resp2.iter_content(8192):
        body += chunk
        total += len(chunk)
    print("DL bytes:", total, flush=True)
    print("DL repr:", repr(body[:200]), flush=True)
except Exception as e:
    print("DL ERROR:", type(e).__name__, e, flush=True)
