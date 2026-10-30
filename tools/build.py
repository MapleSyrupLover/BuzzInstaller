"""
Build helpers for BuzzInstaller.

Used by both the command-line developer tool (``tools/dev.py``) and the
Developer Studio GUI (``tools/dev_gui.py``).  Produces:

* ``dist/BuzzInstaller/BuzzInstaller/`` — the onedir application
* ``dist/BuzzInstaller/BuzzInstaller.zip`` — release asset (contents at root)
* ``dist/BuzzInstallerInstaller/BuzzInstallerInstaller.exe`` — installer
* ``dist/checksums.txt`` — SHA-256 checksums for the release assets
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DIST = ROOT / "dist"
BUILD_ROOT = ROOT / "build" / "devtools"

APP_SRC = SRC / "BuzzInstaller"
INSTALLER_SRC = SRC / "BuzzInstallerInstaller"
APP_DIST = DIST / "BuzzInstaller"
APP_OUT = APP_DIST / "BuzzInstaller"
APP_ZIP = APP_DIST / "BuzzInstaller.zip"
INSTALLER_DIST = DIST / "BuzzInstallerInstaller"
INSTALLER_EXE = INSTALLER_DIST / "BuzzInstallerInstaller.exe"
CHECKSUMS = DIST / "checksums.txt"

APP_ICON = APP_SRC / "assets" / "buzzinstaller.ico"
APP_ASSETS = APP_SRC / "assets"

LogFn = Callable[[str], None]


class BuildError(RuntimeError):
    """Raised when a build step fails."""


def _noop(_: str) -> None:
    pass


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return 0


def run_command(
    command: Iterable[str],
    log: LogFn = _noop,
    cwd: Path = ROOT,
    env: Optional[dict] = None,
) -> str:
    """Run a command, streaming output to *log*. Returns stdout."""
    command = [str(part) for part in command]
    log("$ " + " ".join(command))
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=_creation_flags(),
    )
    lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip("\r\n")
        lines.append(line)
        log(line)
    code = process.wait()
    output = "\n".join(lines)
    if code != 0:
        tail = "\n".join(lines[-25:])
        raise BuildError(
            f"Command failed with exit code {code}:\n{' '.join(command)}\n{tail}"
        )
    return output


def _module_names(package_dir: Path) -> list[str]:
    """Return importable top-level module names for a package directory."""
    names: list[str] = []
    for path in package_dir.rglob("*.py"):
        relative = path.relative_to(package_dir).with_suffix("")
        parts = list(relative.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        names.append(".".join(parts))
    return sorted(set(names))


def stop_running_app(log: LogFn = _noop) -> None:
    """Terminate a running BuzzInstaller so the build can replace it."""
    if sys.platform != "win32":
        return
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", "BuzzInstaller.exe"],
            capture_output=True,
            text=True,
            creationflags=_creation_flags(),
        )
        if result.returncode == 0:
            log("Stopped the running BuzzInstaller process.")
    except OSError:
        pass


def clean(log: LogFn = _noop, remove_dist: bool = True) -> None:
    """Remove build and (optionally) dist output."""
    targets = [BUILD_ROOT]
    if remove_dist:
        targets.append(DIST)
    for target in targets:
        if target.exists():
            log(f"Removing {target.relative_to(ROOT)}")
            shutil.rmtree(target, ignore_errors=True)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)


def _pyinstaller_base(
    name: str,
    work_dir: Path,
    dist_dir: Path,
    spec_dir: Path,
    log: LogFn,
) -> list[str]:
    return [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", name,
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
        "--paths", str(SRC),
        "--paths", str(APP_SRC if name == "BuzzInstaller" else INSTALLER_SRC),
    ]


def build_app(log: LogFn = _noop) -> Path:
    """Build the onedir BuzzInstaller distribution."""
    if APP_DIST.exists():
        shutil.rmtree(APP_DIST, ignore_errors=True)
    (BUILD_ROOT / "app").mkdir(parents=True, exist_ok=True)
    (BUILD_ROOT / "spec-app").mkdir(parents=True, exist_ok=True)

    command = _pyinstaller_base(
        "BuzzInstaller",
        BUILD_ROOT / "work-app",
        APP_DIST,
        BUILD_ROOT / "spec-app",
        log,
    )
    command += [
        "--onedir",
        "--windowed",
        "--icon", str(APP_ICON),
        "--add-data", f"{APP_ASSETS}{os.pathsep}assets",
        "--exclude-module", "tkinter",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtMultimediaWidgets",
    ]
    for module in _module_names(APP_SRC):
        command.append(f"--hidden-import={module}")
    command.append(str(APP_SRC / "main.py"))

    log("Building BuzzInstaller (onedir)…")
    run_command(command, log=log)
    return APP_OUT / "BuzzInstaller.exe"


def build_installer(log: LogFn = _noop) -> Path:
    """Build the single-file installer/updater."""
    if INSTALLER_DIST.exists():
        shutil.rmtree(INSTALLER_DIST, ignore_errors=True)
    (BUILD_ROOT / "installer").mkdir(parents=True, exist_ok=True)
    (BUILD_ROOT / "spec-installer").mkdir(parents=True, exist_ok=True)

    command = _pyinstaller_base(
        "BuzzInstallerInstaller",
        BUILD_ROOT / "work-installer",
        INSTALLER_DIST,
        BUILD_ROOT / "spec-installer",
        log,
    )
    command += [
        "--onefile",
        "--windowed",
    ]
    icon = APP_ICON
    if icon.is_file():
        command += ["--icon", str(icon)]
    for module in _module_names(INSTALLER_SRC):
        command.append(f"--hidden-import={module}")
    command.append(str(INSTALLER_SRC / "main.py"))

    log("Building BuzzInstallerInstaller (onefile)…")
    run_command(command, log=log)
    return INSTALLER_EXE


def make_zip(log: LogFn = _noop) -> Path:
    """Zip the onedir distribution with its contents at the archive root."""
    if not (APP_OUT / "BuzzInstaller.exe").is_file():
        raise BuildError("Cannot zip: BuzzInstaller.exe was not built.")

    if APP_ZIP.exists():
        APP_ZIP.unlink()
    log(f"Creating {APP_ZIP.relative_to(ROOT)}…")
    with zipfile.ZipFile(APP_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(APP_OUT.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(APP_OUT).as_posix())
    return APP_ZIP


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(assets: Optional[Iterable[Path]] = None, log: LogFn = _noop) -> Path:
    """Write SHA-256 checksums for the given release assets."""
    if assets is None:
        assets = [APP_ZIP, INSTALLER_EXE]
    lines = []
    for asset in assets:
        asset = Path(asset)
        if asset.is_file():
            lines.append(f"{sha256_file(asset)}  {asset.name}")
    CHECKSUMS.parent.mkdir(parents=True, exist_ok=True)
    CHECKSUMS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"Wrote {CHECKSUMS.relative_to(ROOT)}")
    return CHECKSUMS


def build_all(
    log: LogFn = _noop,
    build_application: bool = True,
    build_updater: bool = True,
    make_archive: bool = True,
    kill_running: bool = False,
) -> dict:
    """Run the requested build steps and return the artifact paths."""
    if kill_running:
        stop_running_app(log)

    result: dict = {}
    built: list[Path] = []
    if build_application:
        exe = build_app(log)
        result["app_exe"] = exe
        if make_archive:
            archive = make_zip(log)
            result["app_zip"] = archive
            built.append(archive)
    if build_updater:
        installer = build_installer(log)
        result["installer_exe"] = installer
        built.append(installer)
    if built:
        result["checksums"] = write_checksums(built, log)
        built.append(result["checksums"])
    result["artifacts"] = built
    return result


def artifact_paths() -> list[Path]:
    """Existing release artifacts, newest expectation first."""
    return [path for path in (APP_ZIP, INSTALLER_EXE, CHECKSUMS) if path.is_file()]
