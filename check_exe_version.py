"""Check the installed BuzzInstaller.exe --version output and version management."""
import subprocess
import re
import sys
import os

os.environ.setdefault("PYTHONPATH", "src")
sys.path.insert(0, "src")

exe = r"src\dist\BuzzInstaller\BuzzInstaller.exe"
results = []

if not os.path.isfile(exe):
    results.append("EXE NOT FOUND at " + exe)
else:
    try:
        r = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        results.append("stdout: " + repr(out))
        results.append("stderr: " + repr(err))
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", out)
        results.append("regex match: " + (m.group(1) if m else "NONE"))
    except Exception as e:
        results.append("ERROR running exe: " + str(e))
        import traceback
        results.append(traceback.format_exc())

try:
    from VERSIONS import BUZZINSTALLER_VERSION, BUZZINSTALLER_INSTALLER_VERSION
    results.append("Local BUZZINSTALLER_VERSION: " + str(BUZZINSTALLER_VERSION))
    results.append("Local BUZZINSTALLER_INSTALLER_VERSION: " + str(BUZZINSTALLER_INSTALLER_VERSION))
    from version import compare_versions
    cmp_installer = compare_versions(BUZZINSTALLER_INSTALLER_VERSION, "1.2.1")
    results.append("Installer version vs GitHub 1.2.1: " + str(cmp_installer) + " (0=equal, -1=older, 1=newer)")
    cmp_app = compare_versions(BUZZINSTALLER_VERSION, "1.2.1")
    results.append("App version vs GitHub 1.2.1: " + str(cmp_app) + " (0=equal, -1=older, 1=newer)")
except Exception as e:
    results.append("ERROR in version logic: " + str(e))
    import traceback
    results.append(traceback.format_exc())

with open("exe_version_results.txt", "w") as f:
    f.write("\n".join(results))
print("Done - check exe_version_results.txt")
