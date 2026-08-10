"""Run version and update_check tests, capture output to file."""
import subprocess
import sys

lines = []

# Run version tests
lines.append("=== test_version.py ===")
r = subprocess.run(
    [sys.executable, "src/BuzzInstallerInstaller/tests/test_version.py"],
    capture_output=True, text=True, timeout=30,
)
lines.append(r.stdout)
if r.stderr:
    lines.append("STDERR: " + r.stderr)
lines.append(f"Exit code: {r.returncode}")

# Run update_check tests
lines.append("\n=== test_update_check.py ===")
r = subprocess.run(
    [sys.executable, "src/BuzzInstallerInstaller/tests/test_update_check.py"],
    capture_output=True, text=True, timeout=30,
)
lines.append(r.stdout)
if r.stderr:
    lines.append("STDERR: " + r.stderr)
lines.append(f"Exit code: {r.returncode}")

with open("version_test_results.txt", "w") as f:
    f.write("\n".join(lines))

print("Done - check version_test_results.txt")
