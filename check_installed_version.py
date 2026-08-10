"""Check installed BuzzInstaller files and their versions."""
import subprocess
import sys
import os

results = []
exe_paths = []

# Dist BuzzInstaller
for candidate in [
    r"src\dist\BuzzInstaller\BuzzInstaller.exe",
    r"dist\BuzzInstaller\BuzzInstaller.exe",
]:
    if os.path.isfile(candidate):
        exe_paths.append(("BuzzInstaller.exe (dist)", candidate))

# Dist BuzzInstallerInstaller
for candidate in [
    r"dist\BuzzInstallerInstaller\BuzzInstallerInstaller.exe",
    r"src\dist\BuzzInstallerInstaller\BuzzInstallerInstaller.exe",
]:
    if os.path.isfile(candidate):
        exe_paths.append(("BuzzInstallerInstaller.exe (dist)", candidate))

# Installed BuzzInstaller.exe
localappdata = os.environ.get("LOCALAPPDATA", "")
if localappdata:
    installed_exe = os.path.join(localappdata, "BuzzInstaller", "BuzzInstaller.exe")
    if os.path.isfile(installed_exe):
        exe_paths.append(("BuzzInstaller.exe (installed)", installed_exe))

if not exe_paths:
    results.append("No BuzzInstaller EXEs found in dist/ or installed location.")
else:
    for label, path in exe_paths:
        try:
            r = subprocess.run(
                [path, "--version"],
                capture_output=True, text=True, timeout=15,
            )
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            results.append(f"{label} ({path}):")
            results.append(f"  stdout: {repr(out)}")
            results.append(f"  stderr: {repr(err)}")
            results.append(f"  returncode: {r.returncode}")
        except Exception as e:
            results.append(f"{label} ({path}): ERROR: {e}")

# Show version sources
results.append("")
results.append("=== Version sources ===")
sys.path.insert(0, "src")
sys.path.insert(0, "src\\BuzzInstallerInstaller")
try:
    from VERSIONS import BUZZINSTALLER_VERSION, BUZZINSTALLER_INSTALLER_VERSION
    results.append(f"VERSIONS.BUZZINSTALLER_VERSION = {BUZZINSTALLER_VERSION}")
    results.append(f"VERSIONS.BUZZINSTALLER_INSTALLER_VERSION = {BUZZINSTALLER_INSTALLER_VERSION}")
    from version import APP_VERSION as INSTALLER_VERSION
    results.append(f"installer version.py APP_VERSION = {INSTALLER_VERSION}")
except Exception as e:
    results.append(f"ERROR importing versions: {e}")
    import traceback
    results.append(traceback.format_exc())

with open("installed_version_results.txt", "w") as f:
    f.write("\n".join(results))
print("Done - check installed_version_results.txt")
