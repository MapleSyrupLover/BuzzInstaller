"""Final verification of the version-check fix."""
import sys
import os
import json
import subprocess
import urllib.request

sys.path.insert(0, "src")
sys.path.insert(0, os.path.join("src", "BuzzInstallerInstaller"))

results = []
results.append("FINAL DIAGNOSIS - BuzzInstallerInstaller false 'out of date' detection")
results.append("=" * 70)

# 1. Git state
results.append("\n### 1. Git state")
r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
results.append(f"HEAD commit: {r.stdout.strip()}")
r = subprocess.run(["git", "log", "--oneline", "-1"], capture_output=True, text=True)
results.append(f"HEAD message: {r.stdout.strip()}")
with open("src/VERSIONS.py") as f:
    vt = f.read()
results.append(f"Working tree VERSIONS.py version: {1.21 if '1.2.1' in vt else 'ERROR'}")

# 2. Build scripts
results.append("\n### 2. Build scripts (BZBackground check)")
for path in ["src/BuzzInstaller/build/build_exe.py",
             "src/BuzzInstallerInstaller/build/build_installer.py"]:
    with open(path) as f:
        content = f.read()
    has_bg = "BZBackground" in content
    results.append(f"  {path}: {'FAIL - has BZBackground refs' if has_bg else 'OK - no BZBackground references'}")

# 3. GitHub API
results.append("\n### 3. GitHub API")
from github_api import RELEASES_API_URL, INSTALLER_ASSET_NAME, GitHubReleasesClient
results.append(f"  API URL: {RELEASES_API_URL}")
results.append(f"  Expected installer asset: {INSTALLER_ASSET_NAME}")

api_url = RELEASES_API_URL
req = urllib.request.Request(api_url, headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/vnd.github+json",
})
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        results.append(f"  Latest release tag: {tag}")
        results.append(f"  Release name: {data.get('name', '')}")
        asset_names = [a["name"] for a in data.get("assets", [])]
        results.append(f"  Assets: {asset_names}")

        installer_asset = None
        for a in data.get("assets", []):
            if a["name"] == INSTALLER_ASSET_NAME:
                installer_asset = a
                break
        if installer_asset:
            results.append(f"  Installer asset found: {installer_asset['name']}")
            results.append(f"  Download URL: {installer_asset['browser_download_url'][:100]}...")
        else:
            results.append(f"  ERROR: Installer asset NOT found")

        # Normalize and compare
        from version import parse_version, compare_versions, APP_VERSION
        results.append(f"  Local APP_VERSION: {APP_VERSION}")
        gh_version = parse_version(tag)
        results.append(f"  GitHub version (parsed): {gh_version}")
        cmp_result = compare_versions(APP_VERSION, str(gh_version))
        results.append(f"  compare_versions('{APP_VERSION}', '{gh_version}') = {cmp_result}")
        if cmp_result == 0:
            results.append(f"  -> MATCH: No update needed. CORRECT.")
        elif cmp_result < 0:
            results.append(f"  -> MISMATCH: Would show 'out of date'. WRONG!")
        else:
            results.append(f"  -> Local is newer. OK.")
except Exception as e:
    results.append(f"  ERROR: {e}")

# 4. Compiled EXE version
results.append("\n### 4. Compiled EXE version")
exe_path = "dist/BuzzInstallerInstaller/BuzzInstallerInstaller.exe"
if os.path.isfile(exe_path):
    try:
        r = subprocess.run([exe_path, "--version"], capture_output=True, text=True, timeout=15)
        out = (r.stdout or "").strip()
        results.append(f"  {exe_path}:")
        results.append(f"    stdout: {repr(out)}")
        results.append(f"    returncode: {r.returncode}")
        if "1.2.1" in out:
            results.append(f"    -> CORRECT: Reports 1.2.1")
        elif "1.2.0" in out:
            results.append(f"    -> STALE: Reports 1.2.0 (needs rebuild)")
        else:
            results.append(f"    -> UNKNOWN version")
    except Exception as e:
        results.append(f"  ERROR: {e}")
else:
    results.append(f"  EXE not found at {exe_path}")

# 5. check_for_installer_update (source version)
results.append("\n### 5. check_for_installer_update() (source)")
from update_check import check_for_installer_update
try:
    url = check_for_installer_update()
    if url is None:
        results.append(f"  Result: None -> No update detected. CORRECT (source is at 1.2.1, GitHub is 1.2.1)")
    else:
        results.append(f"  Result: {url} -> FALSE POSITIVE: would show update dialog!")
except Exception as e:
    results.append(f"  ERROR: {e}")
    import traceback
    results.append(traceback.format_exc())

# 6. Version comparison edge cases
results.append("\n### 6. Version comparison edge cases")
from version import compare_versions
for a, b, expected in [("1.2.0", "1.2.1", -1), ("1.2.1", "1.2.1", 0),
                       ("1.9.0", "1.10.0", -1), ("1.10.0", "1.9.0", 1),
                       ("v1.2.1", "1.2.1", 0), ("v1.10.0", "1.9.0", 1)]:
    result = compare_versions(a, b)
    ok = result == expected
    results.append(f"  {'OK' if ok else 'FAIL'}: compare_versions('{a}', '{b}') = {result} (expected {expected})")

# 7. Tests
results.append("\n### 7. Tests")
for test_file in ["src/BuzzInstallerInstaller/tests/test_version.py",
                  "src/BuzzInstallerInstaller/tests/test_update_check.py"]:
    r = subprocess.run([sys.executable, test_file], capture_output=True, text=True, timeout=30)
    last_line = (r.stdout or "").strip().split("\n")[-1] if r.stdout else ""
    results.append(f"  {os.path.basename(test_file)}: exit={r.returncode}, last={last_line}")

print("\n".join(results))
with open("final_verify_results.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(results))
print("\nDone - check final_verify_results.txt")
