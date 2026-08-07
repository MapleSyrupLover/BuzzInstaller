import sys, json, re, io, requests, urllib.request, http.cookiejar, time

code = "s7av2loeg1wd"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0")
SEC = {
    "sec-ch-ua": '"Not;A=Brand";v="24", "Chromium";v="127"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
HDRS = {"User-Agent": UA, **SEC}

def try_requests_download():
    s = requests.Session()
    s.headers.update(HDRS)
    try:
        resp = s.get(f"https://buzzheavier.com/{code}", timeout=30, allow_redirects=True)
        if "Just a moment" in resp.text or resp.status_code == 403:
            return "BLOCKED", resp.status_code
        m = re.search(r'/'+re.escape(code)+r'/download\?t=([A-Za-z0-9._%-]+)', resp.text)
        if not m: return "NO_TOKEN", resp.status_code
        token = m.group(1)
        dl = s.get(f"https://buzzheavier.com/{code}/download?t={token}", timeout=60, stream=True, headers={**HDRS, "Accept":"*/*"})
        data = b"".join(dl.iter_content(8192))
        return "OK", len(data)
    except Exception as e:
        return "ERR", f"{type(e).__name__}: {e}"

def try_urllib_download():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj), urllib.request.HTTPSHandler())
    def hdrs(extra=None):
        h = dict(HDRS)
        if extra: h.update(extra)
        return h
    try:
        resp = opener.open(urllib.request.Request(f"https://buzzheavier.com/{code}", headers=hdrs()), timeout=30)
        html = resp.read().decode("utf-8","replace")
        m = re.search(r'/'+re.escape(code)+r'/download\?t=([A-Za-z0-9._%-]+)', html)
        if not m: return "NO_TOKEN", resp.status
        token = m.group(1)
        resp2 = opener.open(urllib.request.Request(f"https://buzzheavier.com/{code}/download?t={token}", headers=hdrs({"Accept":"*/*"})), timeout=60)
        data = resp2.read()
        return "OK", len(data)
    except Exception as e:
        return "ERR", f"{type(e).__name__}: {e}"

# Run each 3 times
for label, fn in [("requests", try_requests_download), ("urllib", try_urllib_download)]:
    print(f"--- {label} ---", flush=True)
    for i in range(3):
        result = fn()
        print(f"  attempt {i}: {result}", flush=True)
        time.sleep(2)
