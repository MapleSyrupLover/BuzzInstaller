"""Run all version-related tests after fixes."""
import subprocess
import sys

lines = []

for test_file in [
    "src/BuzzInstallerInstaller/tests/test_version.py",
    "src/BuzzInstallerInstaller/tests/test_update_check.py",
]:
    lines.append(f"=== {test_file} ===")
    r = subprocess.run(
        [sys.executable, test_file],
        capture_output=True, text=True, timeout=30,
    )
    lines.append(r.stdout)
    if r.stderr:
        lines.append("STDERR: " + r.stderr)
    lines.append(f"Exit code: {r.returncode}")
    lines.append("")

with open("all_test_results.txt", "w") as f:
    f.write("\n".join(lines))
print("Done - check all_test_results.txt")
