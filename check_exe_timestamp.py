"""Check the modification time of the installer EXE."""
import os
import time

exe = r"dist\BuzzInstallerInstaller\BuzzInstallerInstaller.exe"
if os.path.isfile(exe):
    mtime = os.path.getmtime(exe)
    size = os.path.getsize(exe)
    print(f"File: {exe}")
    print(f"Size: {size:,} bytes")
    print(f"Modified: {time.ctime(mtime)}")
    print(f"Modified (UTC): {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(mtime))}")
    print(f"Current time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print(f"Age (seconds): {time.time() - mtime:.1f}")
else:
    print(f"File NOT FOUND: {exe}")

# Also check if there's a build log
for log in ["build/_pyi_installer/BuzzInstallerInstaller.log", "build/_pyi_installer/"]:
    full = os.path.join(os.getcwd(), log)
    if os.path.isfile(full):
        print(f"\nLog file exists: {full}")
    elif os.path.isdir(full):
        contents = os.listdir(full)
        print(f"\nBuild directory contents: {contents}")
