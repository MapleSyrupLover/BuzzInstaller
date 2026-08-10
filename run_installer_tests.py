"""Run installer-related tests after fixes."""
import subprocess
import sys
import os

lines = []

# Add the installer package to path for test imports
env = os.environ.copy()
env["PYTHONPATH"] = "src/BuzzInstallerInstaller"

for test_file in [
    "src/BuzzInstallerInstaller/tests/test_installer.py",
    "src/BuzzInstallerInstaller/tests/test_improvements.py",
    "src/BuzzInstaller/tests/test_background_handoff.py",
]:
    if not os.path.isfile(test_file):
        lines.append(f"SKIP (not found): {test_file}")
        lines.append("")
        continue
    lines.append(f"=== {test_file} ===")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", test_file],
        capture_output=True, text=True, timeout=60,
        env=env,
    )
    lines.append(r.stdout)
    lines.append(r.stderr)
    lines.append(f"Exit code: {r.returncode}")
    lines.append("")

with open("installer_test_results.txt", "w") as f:
    f.write("\n".join(lines))
print("Done - check installer_test_results.txt")
