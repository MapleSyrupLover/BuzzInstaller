"""Check which GitHub repo the update check actually queries."""
import json
import urllib.error
import urllib.request

results = {}

for repo in ["MapleSyrupLover/BuzzInstaller", "MapleSyrupLover/INSTALLER"]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "test"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read())
            results[repo] = {
                "status": resp.status,
                "tag": d.get("tag_name"),
                "name": d.get("name"),
                "assets": [a["name"] for a in d.get("assets", [])],
            }
    except urllib.error.HTTPError as e:
        results[repo] = {
            "status": e.code,
            "error": e.read(200).decode("utf-8", "replace"),
        }
    except Exception as e:
        results[repo] = {"status": "ERR", "error": str(e)}

with open("github_repo_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print("Results written to github_repo_results.json")