import sys, re, urllib.request, urllib.error
import http.cookiejar

code = "s7av2loeg1wd"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0")

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(cj),
    urllib.request.HTTPSHandler(),
)
def hdrs(extra=None):
    h = {"User-Agent": UA,
         "sec-ch-ua": '"Not;A=Brand";v="24", "Chromium";v="127"',
         "sec-ch-ua-mobile": "?0",
         "sec-ch-ua-platform": '"Windows"',
         "Accept": "text/html,application/xhtml+xml",
         "Accept-Language": "en-US,en;q=0.9"}
    if extra: h.update(extra)
    return h

page_url = f"https://buzzheavier.com/{code}"
req = urllib.request.Request(page_url, headers=hdrs())
try:
    resp = opener.open(req, timeout=30)
    html = resp.read().decode("utf-8", "replace")
    print("PAGE status OK, len:", len(html), flush=True)
    m = re.search(r'/'+re.escape(code)+r'/download\?t=([A-Za-z0-9._%-]+)', html)
    if m:
        token = m.group(1)
        print("TOKEN:", token, flush=True)
        dl_url = f"https://buzzheavier.com/{code}/download?t={token}"
        req2 = urllib.request.Request(dl_url, headers=hdrs({"Accept": "*/*"}))
        resp2 = opener.open(req2, timeout=60)
        data = resp2.read()
        print("DL bytes:", len(data), flush=True)
        print("DL repr:", repr(data[:120]), flush=True)
    else:
        print("TOKEN NOT FOUND. snippet:", html[:500], flush=True)
except urllib.error.HTTPError as e:
    print("HTTPError", e.code, e.read()[:300], flush=True)
except Exception as e:
    print("ERROR:", type(e).__name__, e, flush=True)
