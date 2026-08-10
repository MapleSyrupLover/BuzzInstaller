"""Final verification: run the actual update check against GitHub."""
import sys
import os

sys.path.insert(0, "src")
sys.path.insert(0, os.path.join("src", "BuzzInstallerInstaller"))

results = []

# Test 1: check_for_installer_update (installer self-update check)
results.append("=== check_for_installer_update() ===")
results.append(f"APP_VERSION (from VERSIONS.py): {__import__('VERSIONS').BUZZINSTALLER_INSTALLER_VERSION}")
from version import APP_VERSION
results.append(f"APP_VERSION (from version.py): {APP_VERSION}")
results.append(f"RELEASES_API_URL: {__import__('github_api').RELEASES_API_URL}")

from update_check import check_for_installer_update
try:
    result = check_for_installer_update()
    results.append(f"check_for_installer_update() returned: {repr(result)}")
    if result is None:
        results.append("INTERPRETATION: No update needed (installer version matches GitHub release). CORRECT.")
    else:
        results.append(f"INTERPRETATION: Update available - URL: {result}")
        results.append("INTERPRETATION: This means APP_VERSION != GitHub release tag. INCORRECT if they should match.")
except Exception as e:
    results.append(f"ERROR: {e}")
    import traceback
    results.append(traceback.format_exc())

# Test 2: Verify installed exe version vs latest
results.append("")
results.append("=== Installed BuzzInstaller.exe --version ===")
exe = r"src\dist\BuzzInstaller\BuzzInstaller.exe"
from installer import get_installed_version
ver = get_installed_version()
results.append(f"get_installed_version(): {ver} ({str(ver) if ver else 'None'})")

from github_api import GitHubReleasesClient, RELEASES_API_URL
results.append("")
results.append("=== GitHub release check ===")
try:
    client = GitHubReleasesClient()
    release = client.get_latest_release()
    results.append(f"GitHub release tag: {release.tag_name}")
    results.append(f"GitHub release version: {release.version}")
    results.append(f"GitHub release assets: {[a.name for a in release.assets]}")
    from version import compare_versions
    cmp = compare_versions(APP_VERSION, str(release.version))
    results.append(f"compare_versions(APP_VERSION={APP_VERSION}, GitHub={release.version}) = {cmp}")
    if cmp == 0:
        results.append("MATCH: Installer version equals GitHub release. No update dialog will show.")
    elif cmp < 0:
        results.append("MISMATCH: Installer version is OLDER than GitHub release. Update dialog WILL show.")
    elif cmp > 0:
        results.append("MISMATCH: Installer version is NEWER than GitHub release. No update dialog will show.")
except Exception as e:
    results.append(f"ERROR checking GitHub: {e}")
    import traceback
    results.append(traceback.format_exc())

with open("verify_update_check_results.txt", "w") as f:
    f.write("\n".join(results))
print("Done - check verify_update_check_results.txt")
