"""
In-app updater for BuzzInstaller.

The updater talks to the public GitHub Releases API, compares the latest
release against the running build, downloads the onedir bundle
(``BuzzInstaller.zip``) and hands off the actual file replacement to a
detached PowerShell helper.  The helper waits for the app to exit,
unpacks the new build over the installation directory and relaunches the
app — no separate installer executable required.

This means users are updated automatically as soon as a new GitHub
release is published (which the Developer Studio in ``tools/`` does in
one click).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from config.paths import get_app_dir
from utils.file_utils import format_size
from utils.logging import get_logger
from version import APP_VERSION

logger = get_logger(__name__)

REPO_OWNER = "MapleSyrupLover"
REPO_NAME = "BuzzInstaller"
LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
)
RELEASES_PAGE = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases"
USER_AGENT = "BuzzInstaller-Updater/1.0 (+https://github.com/MapleSyrupLover/BuzzInstaller)"

BUNDLE_ASSET_NAME = "BuzzInstaller.zip"
INSTALLER_ASSET_NAME = "BuzzInstallerInstaller.exe"
EXE_NAME = "BuzzInstaller.exe"

ProgressCallback = Callable[[int, int], Optional[bool]]
CancelCallback = Callable[[], bool]


class UpdaterError(Exception):
    """Base class for updater failures."""


class UpdateCheckError(UpdaterError):
    """Raised when the release check cannot be completed."""


class UpdateDownloadError(UpdaterError):
    """Raised when downloading the update fails."""


class UpdateApplyError(UpdaterError):
    """Raised when the downloaded update cannot be applied."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag: str
    html_url: str
    notes: str
    asset_name: str
    asset_url: str
    asset_size: int
    published_at: str = ""

    @property
    def size_str(self) -> str:
        return format_size(self.asset_size)


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------
def _version_tuple(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", text or "")
    if not numbers:
        return (0,)
    return tuple(int(n) for n in numbers[:4])


def is_newer(latest: str, current: Optional[str] = None) -> bool:
    """True when *latest* is semantically newer than *current*.

    *current* defaults to the running version; passing it explicitly keeps
    the helpers easy to test against simulated installations.
    """
    if current is None:
        current = current_version()
    latest_t = _version_tuple(latest)
    current_t = _version_tuple(current)
    length = max(len(latest_t), len(current_t))
    latest_t = latest_t + (0,) * (length - len(latest_t))
    current_t = current_t + (0,) * (length - len(current_t))
    return latest_t > current_t


def current_version() -> str:
    return APP_VERSION


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_install_dir() -> Path:
    """Directory containing the running BuzzInstaller executable."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # Running from source — the repository root (used for diagnostics only;
    # applying an update is disabled in this mode).
    return Path(__file__).resolve().parents[3]


def get_update_cache_dir() -> Path:
    return get_app_dir() / "updates"


def can_self_update() -> bool:
    """Only an installed (frozen) build can replace itself."""
    return is_frozen() and (get_install_dir() / EXE_NAME).is_file()


# ---------------------------------------------------------------------------
# Release checking
# ---------------------------------------------------------------------------
def _request_json(url: str, timeout: int = 15) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateCheckError("No releases have been published yet.") from exc
        raise UpdateCheckError(f"GitHub returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise UpdateCheckError(f"Cannot reach GitHub: {exc.reason}") from exc
    except OSError as exc:
        raise UpdateCheckError(f"Network error: {exc}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError("GitHub returned an invalid response.") from exc
    if not isinstance(data, dict):
        raise UpdateCheckError("GitHub returned an unexpected response.")
    return data


def check_for_update(timeout: int = 15) -> Optional[UpdateInfo]:
    """
    Return update information when a newer release exists, otherwise None.

    Raises :class:`UpdateCheckError` on network / API failures so callers
    can distinguish "up to date" from "could not check".
    """
    data = _request_json(LATEST_RELEASE_URL, timeout=timeout)

    if data.get("draft") or data.get("prerelease"):
        return None

    tag = str(data.get("tag_name", "")).strip()
    if not tag:
        raise UpdateCheckError("Latest release has no version tag.")
    if not is_newer(tag):
        return None

    assets = data.get("assets") or []
    bundle = None
    legacy_exe = None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        if name.lower() == BUNDLE_ASSET_NAME.lower():
            bundle = asset
            break
        if name.lower() == EXE_NAME.lower():
            legacy_exe = asset
    asset = bundle or legacy_exe
    if asset is None:
        raise UpdateCheckError(
            f"Release {tag} does not contain {BUNDLE_ASSET_NAME}."
        )

    return UpdateInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        html_url=str(data.get("html_url", RELEASES_PAGE)),
        notes=str(data.get("body", "") or ""),
        asset_name=str(asset.get("name", BUNDLE_ASSET_NAME)),
        asset_url=str(asset.get("browser_download_url", "")),
        asset_size=int(asset.get("size", 0) or 0),
        published_at=str(data.get("published_at", "") or ""),
    )


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------
def download_update(
    info: UpdateInfo,
    progress_cb: Optional[ProgressCallback] = None,
    cancelled: Optional[CancelCallback] = None,
    dest_dir: Optional[Path] = None,
) -> Path:
    """Download the release bundle into the update cache directory."""
    if not info.asset_url:
        raise UpdateDownloadError("The release asset has no download URL.")

    dest_dir = dest_dir or get_update_cache_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(info.asset_url.encode("utf-8")).hexdigest()[:12]
    suffix = ".zip" if info.asset_name.lower().endswith(".zip") else ".exe"
    dest = dest_dir / f"BuzzInstaller-{info.version}-{cache_key}{suffix}"
    partial = dest.with_suffix(dest.suffix + ".part")

    request = urllib.request.Request(
        info.asset_url,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            total = int(response.headers.get("Content-Length", 0) or info.asset_size or 0)
            received = 0
            with open(partial, "wb") as handle:
                while True:
                    if cancelled and cancelled():
                        partial.unlink(missing_ok=True)
                        raise UpdateDownloadError("Update download cancelled.")
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    if progress_cb is not None:
                        if progress_cb(received, total) is False:
                            partial.unlink(missing_ok=True)
                            raise UpdateDownloadError("Update download cancelled.")
    except UpdateDownloadError:
        raise
    except (urllib.error.URLError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateDownloadError(f"Download failed: {exc}") from exc

    if info.asset_size and partial.stat().st_size < info.asset_size:
        partial.unlink(missing_ok=True)
        raise UpdateDownloadError("The downloaded update is incomplete.")

    partial.replace(dest)
    _validate_download(dest)
    logger.info("Update %s downloaded to %s", info.version, dest)
    return dest


def _validate_download(path: Path) -> None:
    """Validate that *path* contains a usable BuzzInstaller build."""
    if path.suffix.lower() != ".zip":
        if not path.stat().st_size:
            raise UpdateDownloadError("Downloaded executable is empty.")
        return
    try:
        with zipfile.ZipFile(path) as archive:
            names = {name.replace("\\", "/") for name in archive.namelist()}
            if EXE_NAME not in names:
                raise UpdateDownloadError(
                    f"Update bundle does not contain {EXE_NAME}."
                )
            if not any(name.startswith("_internal/") for name in names):
                # Legacy bundles may still be single-exe style; accept them
                # as long as the executable exists.
                logger.warning("Bundle has no _internal/ directory")
            bad = archive.testzip()
            if bad:
                raise UpdateDownloadError(f"Update bundle is corrupt ({bad}).")
    except zipfile.BadZipFile as exc:
        raise UpdateDownloadError("Update bundle is not a valid ZIP file.") from exc


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------
_PS_SCRIPT = r"""
param(
    [int]$TargetPid,
    [string]$ZipPath,
    [string]$TargetDir,
    [string]$ExePath,
    [string]$DataDir,
    [string]$LogPath,
    [string]$ExpectedVersion
)

$ErrorActionPreference = 'Stop'
$script:LogDir = Split-Path -Parent $LogPath
$script:PreserveNames = @(
    'config.json', 'history.json', 'logs', 'cache', 'updates',
    'transfers', 'transfers.db', 'transfers.db-wal', 'transfers.db-shm',
    'config.lock'
)

function Write-Log([string]$Message) {
    try {
        if (-not (Test-Path -LiteralPath $script:LogDir)) {
            New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null
        }
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Add-Content -LiteralPath $LogPath -Value "[$stamp] $Message" -Encoding UTF8
    } catch { }
}

function Get-NormalisedPath([string]$Path) {
    if (-not $Path) { return '' }
    try {
        return [System.IO.Path]::GetFullPath($Path).TrimEnd('\').ToLowerInvariant()
    } catch {
        return $Path.TrimEnd('\').ToLowerInvariant()
    }
}

function Wait-ForProcessExit([int]$ProcessId, [int]$TimeoutSeconds) {
    if ($ProcessId -le 0) { return $true }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Stop-AllBuzzInstallerProcesses() {
    $target = Get-NormalisedPath $TargetDir
    for ($attempt = 0; $attempt -lt 80; $attempt++) {
        $ids = @()
        try {
            $procs = Get-CimInstance Win32_Process -Filter "Name='BuzzInstaller.exe'" -ErrorAction SilentlyContinue
            foreach ($proc in $procs) {
                $procExe = $proc.ExecutablePath
                if (-not $procExe) {
                    $ids += [int]$proc.ProcessId
                } else {
                    $normalised = Get-NormalisedPath $procExe
                    if ($normalised.StartsWith($target) -or $normalised -eq (Get-NormalisedPath $ExePath)) {
                        $ids += [int]$proc.ProcessId
                    }
                }
            }
        } catch { }
        if ($TargetPid -gt 0 -and (Get-Process -Id $TargetPid -ErrorAction SilentlyContinue)) {
            $ids += $TargetPid
        }
        $ids = @($ids | Sort-Object -Unique)
        if ($ids.Count -eq 0) { return }
        foreach ($processId in $ids) {
            try {
                Write-Log "Stopping BuzzInstaller process $processId"
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
            } catch { }
        }
        Start-Sleep -Milliseconds 300
    }
    throw "BuzzInstaller processes are still running; cannot replace the installation."
}

function Remove-PathWithRetry([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
        } catch { }
        if (-not (Test-Path -LiteralPath $Path)) { return }
        Start-Sleep -Milliseconds 250
    }
    if (Test-Path -LiteralPath $Path) {
        throw "Could not remove $Path (files may still be locked)."
    }
}

function Move-ToStash([string]$Source, [string]$StashDir) {
    if (-not (Test-Path -LiteralPath $Source)) { return }
    try {
        Move-Item -LiteralPath $Source -Destination (Join-Path $StashDir (Split-Path -Leaf $Source)) -Force
    } catch {
        Write-Log "Could not preserve $(Split-Path -Leaf $Source): $_"
    }
}

function Restore-Stash([string]$StashDir, [string]$DestDir) {
    if (-not (Test-Path -LiteralPath $StashDir)) { return }
    foreach ($item in (Get-ChildItem -LiteralPath $StashDir -Force -ErrorAction SilentlyContinue)) {
        $destPath = Join-Path $DestDir $item.Name
        try {
            if (Test-Path -LiteralPath $destPath) {
                Remove-PathWithRetry $destPath
            }
            Move-Item -LiteralPath $item.FullName -Destination $destPath -Force
        } catch {
            Write-Log "Could not restore $($item.Name): $_"
        }
    }
    Remove-PathWithRetry $StashDir
}

function Get-ExeVersionText([string]$Exe, [string]$WorkDir) {
    try {
        $outFile = Join-Path $env:TEMP ("buzzinstaller-ver-" + [guid]::NewGuid().ToString('N') + ".out")
        $errFile = Join-Path $env:TEMP ("buzzinstaller-ver-" + [guid]::NewGuid().ToString('N') + ".err")
        $proc = Start-Process -FilePath $Exe -ArgumentList '--version' `
            -WorkingDirectory $WorkDir -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        if (-not $proc.WaitForExit(20000)) {
            try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
        }
        $text = ''
        foreach ($file in @($outFile, $errFile)) {
            if (Test-Path -LiteralPath $file) {
                try { $text += (Get-Content -LiteralPath $file -Raw -ErrorAction SilentlyContinue) } catch { }
                Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
            }
        }
        return ($text | Out-String).Trim()
    } catch {
        Write-Log "Version check failed: $_"
        return ''
    }
}

$stage = Join-Path $env:TEMP ("buzzinstaller-stage-" + [guid]::NewGuid().ToString('N'))
$stash = Join-Path $env:TEMP ("buzzinstaller-data-" + [guid]::NewGuid().ToString('N'))
$backup = Join-Path (Split-Path -Parent $TargetDir) ("BuzzInstaller.old-" + [guid]::NewGuid().ToString('N'))
$backupCreated = $false
$committed = $false
$dataIsTarget = ((Get-NormalisedPath $DataDir) -eq (Get-NormalisedPath $TargetDir))

try {
    Write-Log "Update started. target=$TargetDir data=$DataDir"
    Write-Log "Waiting for BuzzInstaller (PID $TargetPid) to close..."
    Wait-ForProcessExit $TargetPid 20 | Out-Null
    Stop-AllBuzzInstallerProcesses

    # --- 1. Unpack and validate the new build in a staging folder ---------
    Write-Log "Extracting $ZipPath to $stage"
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $stage)

    $stagedExe = Join-Path $stage 'BuzzInstaller.exe'
    if (-not (Test-Path -LiteralPath $stagedExe)) {
        throw "The update bundle does not contain BuzzInstaller.exe."
    }
    $stagedVersion = Get-ExeVersionText $stagedExe $stage
    Write-Log "Staged build reports: '$stagedVersion'"
    if ($ExpectedVersion -and $stagedVersion -and ($stagedVersion -notmatch [regex]::Escape($ExpectedVersion))) {
        throw "The downloaded build is not version $ExpectedVersion (reported: $stagedVersion)."
    }

    # --- 2. Preserve user data when data lives inside the install dir -----
    if ($dataIsTarget) {
        New-Item -ItemType Directory -Path $stash -Force | Out-Null
        foreach ($name in $script:PreserveNames) {
            Move-ToStash (Join-Path $TargetDir $name) $stash
        }
        Write-Log "User data moved to $stash"
    }

    # --- 3. Move the old install aside (never delete it until we succeed) -
    if (Test-Path -LiteralPath $TargetDir) {
        $moved = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            try {
                Remove-PathWithRetry $backup
                Move-Item -LiteralPath $TargetDir -Destination $backup -Force -ErrorAction Stop
                $moved = $true
                break
            } catch {
                Start-Sleep -Milliseconds 300
            }
        }
        if (-not $moved) {
            throw "Could not move the existing installation aside (files are in use)."
        }
        $backupCreated = $true
        Write-Log "Old installation moved to $backup"
    }

    # --- 4. Install the new build -----------------------------------------
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
    Get-ChildItem -LiteralPath $stage -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $TargetDir -Recurse -Force -ErrorAction Stop
    }
    Write-Log "New build copied into $TargetDir"

    # --- 5. Verify the installed build ------------------------------------
    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "Updated executable missing after installation: $ExePath"
    }
    $finalVersion = Get-ExeVersionText $ExePath $TargetDir
    Write-Log "Installed build reports: '$finalVersion'"
    if ($ExpectedVersion -and $finalVersion -and ($finalVersion -notmatch [regex]::Escape($ExpectedVersion))) {
        throw "Installed build reports the wrong version (expected $ExpectedVersion, got $finalVersion)."
    }

    # --- 6. Restore user data ---------------------------------------------
    if ($dataIsTarget) {
        Restore-Stash $stash $TargetDir
        Write-Log "User data restored."
    }

    # --- 7. Launch and clean up -------------------------------------------
    $committed = $true
    Write-Log "Launching $ExePath"
    Start-Process -FilePath $ExePath -WorkingDirectory $TargetDir
    Write-Log "Update complete."

    try { if ($backupCreated) { Remove-PathWithRetry $backup } } catch { Write-Log "Could not remove backup: $_" }
    try { Remove-PathWithRetry $stage } catch { Write-Log "Could not remove staging dir: $_" }
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
} catch {
    Write-Log "UPDATE FAILED: $($_.Exception.Message)"
    if (-not $committed) {
        if ($backupCreated -and (Test-Path -LiteralPath $backup)) {
            try {
                Remove-PathWithRetry $TargetDir
                Move-Item -LiteralPath $backup -Destination $TargetDir -Force
                Write-Log "Old installation restored from backup."
            } catch {
                Write-Log "Rollback failed: $_"
            }
        }
        if ($dataIsTarget -and (Test-Path -LiteralPath $stash)) {
            try {
                New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
                Restore-Stash $stash $TargetDir
                Write-Log "User data restored after failure."
            } catch {
                Write-Log "Could not restore user data: $_"
            }
        }
    }
    try {
        if (Test-Path -LiteralPath $ExePath) {
            Start-Process -FilePath $ExePath -WorkingDirectory $TargetDir
        }
    } catch { }
    exit 1
} finally {
    # Keep a copy of this log inside the app's own log folder for support.
    try {
        $appLogDir = Join-Path $DataDir 'logs'
        if (Test-Path -LiteralPath $DataDir) {
            if (-not (Test-Path -LiteralPath $appLogDir)) {
                New-Item -ItemType Directory -Path $appLogDir -Force | Out-Null
            }
            Copy-Item -LiteralPath $LogPath -Destination (Join-Path $appLogDir 'update.log') -Force -ErrorAction SilentlyContinue
        }
    } catch { }
}
"""


def create_update_script(dest_dir: Optional[Path] = None) -> Path:
    """Write (or refresh) the PowerShell apply-update helper."""
    script_dir = dest_dir or get_update_cache_dir()
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / "apply_update.ps1"
    script_path.write_text(_PS_SCRIPT.strip() + "\n", encoding="utf-8")
    return script_path


def _expected_version(bundle_path: Path) -> str:
    """Best-effort version extraction from a downloaded bundle name."""
    match = re.search(r"BuzzInstaller-([0-9][0-9A-Za-z.\-]*)-[0-9a-f]{6,}", bundle_path.name)
    if match:
        return match.group(1)
    match = re.search(r"BuzzInstaller-([0-9][0-9A-Za-z.\-]*)\.zip", bundle_path.name, re.I)
    return match.group(1) if match else ""


def apply_update(bundle_path: Path, expected_version: Optional[str] = None) -> Path:
    """
    Apply a downloaded update after the current process exits.

    The helper waits for (and then terminates) every BuzzInstaller process
    running from the installation directory — including the ``--background``
    process — moves the old installation aside, unpacks the downloaded
    bundle, verifies the new executable's version, restores the user's
    config/history and finally relaunches the new build.

    Returns the path to the helper's log file.  The caller should quit the
    application immediately afterwards.
    """
    if not can_self_update():
        raise UpdateApplyError(
            "Self-update is only available in an installed build. "
            "Run BuzzInstaller from its installed folder to update."
        )
    if not bundle_path.is_file():
        raise UpdateApplyError("The downloaded update file is missing.")

    install_dir = get_install_dir()
    app_dir = get_app_dir()
    exe_path = install_dir / EXE_NAME

    # The helper log lives outside the install dir: on the default layout
    # the install dir *is* the data dir, so anything inside it is moved
    # aside while the update is applied.  It is copied into
    # <data>/logs/update.log once the update finishes.
    log_dir = Path(tempfile.gettempdir()) / "BuzzInstallerUpdate"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "update.log"
    try:
        log_path.unlink(missing_ok=True)
    except OSError:
        pass

    # Stage the bundle and the helper outside the installation directory.
    staging_dir = Path(tempfile.mkdtemp(prefix="buzzinstaller-update-"))
    staged_bundle = staging_dir / bundle_path.name
    try:
        shutil.copy2(bundle_path, staged_bundle)
        script_path = create_update_script(staging_dir)
    except OSError as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise UpdateApplyError(f"Could not stage the update file: {exc}") from exc

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )

    version = expected_version or _expected_version(bundle_path)

    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", str(script_path),
                "-TargetPid", str(os.getpid()),
                "-ZipPath", str(staged_bundle),
                "-TargetDir", str(install_dir),
                "-ExePath", str(exe_path),
                "-DataDir", str(app_dir),
                "-LogPath", str(log_path),
                "-ExpectedVersion", version,
            ],
            close_fds=True,
            cwd=str(staging_dir),
            creationflags=creationflags,
        )
    except OSError as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise UpdateApplyError(f"Could not start the update helper: {exc}") from exc

    logger.info("Update helper started — exiting for update")
    return log_path


def cleanup_downloads(keep: Optional[Path] = None) -> None:
    """Remove stale update downloads (best effort)."""
    cache = get_update_cache_dir()
    if not cache.is_dir():
        return
    cutoff = time.time() - 7 * 24 * 3600
    for item in cache.iterdir():
        try:
            if keep is not None and item.resolve() == keep.resolve():
                continue
            if item.is_file() and item.stat().st_mtime < cutoff:
                item.unlink(missing_ok=True)
        except OSError:
            pass
