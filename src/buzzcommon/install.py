"""Transactional install/update helpers for BuzzInstaller.

The installation directory is usually also the application's data
directory (``%LOCALAPPDATA%\\BuzzInstaller``), so user data is moved to a
temporary stash before the old build is replaced and restored afterwards.
If anything fails the old build and the user data are restored.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Iterable, Optional

from buzzcommon import metadata, pathman, shortcuts
from buzzcommon.versions import VersionError, parse_version

EXE_NAME = "BuzzInstaller.exe"
UPDATER_NAME = "BuzzInstallerUpdater.exe"
SETUP_NAME = "BuzzInstallerSetup.exe"
APP_NAME = "BuzzInstaller"

PRESERVE_ON_UPDATE = {
    "config.json",
    "history.json",
    "logs",
    "cache",
    "updates",
    "transfers",
    "transfers.db",
    "transfers.db-wal",
    "transfers.db-shm",
    "config.lock",
}

ProgressCallback = Callable[[int, int], None]


class InstallError(Exception):
    """Raised when an installation or update cannot be completed."""


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> Path:
    """Directory containing the running executable (frozen) or the repo."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def app_data_dir() -> Path:
    return metadata.get_app_data_dir()


def _same_path(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(
            os.path.realpath(b)
        )
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _is_under(path: Path, directory: Path) -> bool:
    try:
        return os.path.normcase(str(path)).startswith(
            os.path.normcase(str(directory)).rstrip("\\") + "\\"
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Windows process helpers
# ---------------------------------------------------------------------------
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
PROCESS_SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _kernel32():
    if sys.platform != "win32":
        return None
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _configure_kernel32(kernel32) -> None:
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESSENTRY32W),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESSENTRY32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD


def _process_path(kernel32, pid: int) -> Optional[Path]:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value)
        return None
    finally:
        kernel32.CloseHandle(handle)


def find_processes(image_name: str, install_dir: Optional[Path] = None) -> list[tuple[int, Optional[Path]]]:
    """Return ``(pid, path)`` for every process with *image_name*."""
    if sys.platform != "win32":
        return []
    kernel32 = _kernel32()
    if kernel32 is None:
        return []
    _configure_kernel32(kernel32)
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return []

    results: list[tuple[int, Optional[Path]]] = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        while True:
            if str(entry.szExeFile).lower() == image_name.lower():
                pid = int(entry.th32ProcessID)
                path = _process_path(kernel32, pid)
                if install_dir is None:
                    results.append((pid, path))
                elif path is None or _is_under(path, install_dir) or _same_path(
                    path, install_dir / image_name
                ):
                    results.append((pid, path))
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return results


def wait_for_pid(pid: int, timeout: float = 15.0) -> bool:
    """Wait for *pid* to exit. Returns True when it is gone."""
    if not pid or pid <= 0 or sys.platform != "win32":
        return True
    kernel32 = _kernel32()
    if kernel32 is None:
        return True
    _configure_kernel32(kernel32)
    handle = kernel32.OpenProcess(PROCESS_SYNCHRONIZE, False, pid)
    if not handle:
        return True
    try:
        result = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        return result in (WAIT_OBJECT_0, 0xFFFFFFFF)
    finally:
        kernel32.CloseHandle(handle)


def _terminate(pid: int) -> None:
    if sys.platform != "win32":
        return
    kernel32 = _kernel32()
    if kernel32 is not None:
        _configure_kernel32(kernel32)
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (subprocess.SubprocessError, OSError):
        pass


def stop_app_processes(
    install_dir: Optional[Path] = None,
    timeout: float = 12.0,
) -> list[int]:
    """Terminate every installed BuzzInstaller process.

    This includes the hidden ``--background`` hotkey process.  Returns the
    list of terminated process IDs.  Raises :class:`InstallError` when a
    process cannot be stopped before *timeout*.
    """
    if sys.platform != "win32":
        return []
    deadline = time.monotonic() + timeout
    killed: list[int] = []
    while True:
        processes = find_processes(EXE_NAME, install_dir)
        if not processes:
            return killed
        for pid, _path in processes:
            _terminate(pid)
            killed.append(pid)
        if time.monotonic() >= deadline:
            remaining = find_processes(EXE_NAME, install_dir)
            if remaining:
                raise InstallError(
                    "BuzzInstaller is still running and could not be closed. "
                    "Close it manually and try again."
                )
            return killed
        time.sleep(0.1)


def is_app_running(install_dir: Optional[Path] = None) -> bool:
    return bool(find_processes(EXE_NAME, install_dir))


# ---------------------------------------------------------------------------
# Version resource helpers
# ---------------------------------------------------------------------------
class _VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", wintypes.DWORD),
        ("dwStrucVersion", wintypes.DWORD),
        ("dwFileVersionMS", wintypes.DWORD),
        ("dwFileVersionLS", wintypes.DWORD),
        ("dwProductVersionMS", wintypes.DWORD),
        ("dwProductVersionLS", wintypes.DWORD),
        ("dwFileFlagsMask", wintypes.DWORD),
        ("dwFileFlags", wintypes.DWORD),
        ("dwFileOS", wintypes.DWORD),
        ("dwFileType", wintypes.DWORD),
        ("dwFileSubtype", wintypes.DWORD),
        ("dwFileDateMS", wintypes.DWORD),
        ("dwFileDateLS", wintypes.DWORD),
    ]


def get_file_version(path: Path) -> Optional[str]:
    """Read the Windows file version without launching the executable."""
    if sys.platform != "win32":
        return None
    try:
        version = ctypes.WinDLL("version")
        version.GetFileVersionInfoSizeW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.UINT),
        ]
        version.VerQueryValueW.restype = wintypes.BOOL

        path_text = str(path)
        size = version.GetFileVersionInfoSizeW(path_text, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path_text, 0, size, data):
            return None
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None
        info = ctypes.cast(pointer, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
        if info.dwSignature != 0xFEEF04BD:
            return None
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def installed_version(install_dir: Path) -> Optional[str]:
    """Best-effort version of the BuzzInstaller build in *install_dir*."""
    exe = Path(install_dir) / EXE_NAME
    if not exe.is_file():
        return None
    detected = get_file_version(exe)
    if detected:
        return detected
    # Fallback marker written by the setup/updater.
    marker = Path(install_dir) / "installed-version.txt"
    try:
        text = marker.read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


def _write_version_marker(install_dir: Path, version: str) -> None:
    try:
        (Path(install_dir) / "installed-version.txt").write_text(
            version, encoding="utf-8"
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# File operation helpers
# ---------------------------------------------------------------------------
def _rmtree(path: Path, attempts: int = 40, delay: float = 0.15) -> None:
    if not path.exists():
        return
    last_error: Optional[Exception] = None
    for _ in range(attempts):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(delay)
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        raise InstallError(f"Could not remove {path}: {last_error}")


def _move(src: Path, dst: Path, attempts: int = 40, delay: float = 0.15) -> None:
    last_error: Optional[Exception] = None
    for _ in range(attempts):
        try:
            if dst.exists():
                _rmtree(dst)
            shutil.move(str(src), str(dst))
            return
        except OSError as exc:
            last_error = exc
            time.sleep(delay)
    raise InstallError(f"Could not move {src} to {dst}: {last_error}")


def _copy_with_retry(src: Path, dst: Path, attempts: int = 40, delay: float = 0.15) -> None:
    last_error: Optional[Exception] = None
    for _ in range(attempts):
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(delay)
    raise InstallError(f"Could not copy {src} to {dst}: {last_error}")


def _stash_user_data(install_dir: Path, stash: Path) -> list[str]:
    """Move preserved user data out of *install_dir*.

    Returns the names that could not be moved (for example, a log file
    briefly locked by an antivirus scanner).  Those entries are recovered
    from the backup folder after the old installation is moved aside so an
    update never silently drops the user's settings.
    """
    stash.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for name in PRESERVE_ON_UPDATE:
        source = install_dir / name
        if not source.exists():
            continue
        try:
            _move(source, stash / name)
        except InstallError:
            # Data preservation is best-effort for individual entries; a
            # locked log file must not abort an otherwise valid update.
            failed.append(name)
    return failed


def _recover_user_data(backup: Path, stash: Path, names: Iterable[str]) -> None:
    """Move preserved items that were still inside the old installation."""
    if not backup.is_dir():
        return
    for name in names:
        target = stash / name
        if target.exists():
            continue
        source = backup / name
        if not source.exists():
            continue
        try:
            _move(source, target)
        except InstallError:
            pass


def _restore_user_data(install_dir: Path, stash: Path) -> None:
    if not stash.is_dir():
        return
    for item in list(stash.iterdir()):
        target = install_dir / item.name
        try:
            if target.is_dir():
                _rmtree(target)
            elif target.exists():
                target.unlink()
            _move(item, target)
        except (OSError, InstallError):
            continue
    # Only drop the stash once everything has been restored; a leftover
    # entry stays in %TEMP% so nothing is lost if a file was locked.
    try:
        if not any(stash.iterdir()):
            _rmtree(stash)
    except (OSError, InstallError):
        pass


def _salvage_user_data(
    install_dir: Path, stash: Path, backup: Path, names: Iterable[str]
) -> None:
    """Copy preserved data that could not be moved back into the install."""
    for name in names:
        target = install_dir / name
        if target.exists():
            continue
        for base in (stash, backup):
            source = base / name
            if not source.exists():
                continue
            try:
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    _copy_with_retry(source, target)
                break
            except (OSError, InstallError):
                continue


def _extract_bundle(bundle_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(bundle_path) as archive:
            names = {name.replace("\\", "/") for name in archive.namelist()}
            if EXE_NAME not in names:
                raise InstallError(f"The update bundle does not contain {EXE_NAME}.")
            bad = archive.testzip()
            if bad:
                raise InstallError(f"The update bundle is corrupt ({bad}).")
            archive.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise InstallError("The update bundle is not a valid ZIP file.") from exc


def _verify_version(exe: Path, expected: Optional[str]) -> None:
    if not expected:
        return
    detected = get_file_version(exe)
    if not detected:
        return
    try:
        if parse_version(detected) != parse_version(expected):
            raise InstallError(
                f"The installed build reports version {detected}, "
                f"expected {expected}."
            )
    except VersionError:
        return


# ---------------------------------------------------------------------------
# Public install/update API
# ---------------------------------------------------------------------------
def install_bundle(
    bundle_path: Path,
    install_dir: Path,
    *,
    expected_version: Optional[str] = None,
    extra_files: Optional[dict[str, Path]] = None,
    data_dir: Optional[Path] = None,
) -> Path:
    """Replace *install_dir* with the contents of *bundle_path*.

    The operation is transactional: the previous installation is moved
    aside and restored if anything fails.  User data is preserved when the
    installation directory is also the data directory.

    Returns the path to the installed ``BuzzInstaller.exe``.
    """
    bundle_path = Path(bundle_path)
    install_dir = Path(install_dir)
    data_dir = Path(data_dir) if data_dir is not None else install_dir
    if not bundle_path.is_file():
        raise InstallError("The downloaded update file is missing.")

    staging = Path(tempfile.mkdtemp(prefix="buzzinstaller-stage-"))
    stash = Path(tempfile.mkdtemp(prefix="buzzinstaller-data-"))
    backup = install_dir.parent / f"{install_dir.name}.old-{uuid.uuid4().hex[:8]}"
    backup_created = False
    preserve = _same_path(data_dir, install_dir)
    stash_failures: list[str] = []

    try:
        if zipfile.is_zipfile(bundle_path):
            _extract_bundle(bundle_path, staging)
        else:
            _copy_with_retry(bundle_path, staging / EXE_NAME)

        staged_exe = staging / EXE_NAME
        if not staged_exe.is_file():
            raise InstallError(f"The update bundle does not contain {EXE_NAME}.")
        _verify_version(staged_exe, expected_version)

        install_dir.parent.mkdir(parents=True, exist_ok=True)
        if preserve:
            stash_failures = _stash_user_data(install_dir, stash)

        if install_dir.exists():
            _move(install_dir, backup)
            backup_created = True
            if preserve and stash_failures:
                _recover_user_data(backup, stash, stash_failures)
                stash_failures = [
                    name for name in stash_failures if not (stash / name).exists()
                ]

        install_dir.mkdir(parents=True, exist_ok=True)
        for item in staging.iterdir():
            target = install_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                _copy_with_retry(item, target)

        for name, source in (extra_files or {}).items():
            _copy_with_retry(Path(source), install_dir / name)

        final_exe = install_dir / EXE_NAME
        if not final_exe.is_file():
            raise InstallError(f"The installed build is missing {EXE_NAME}.")
        _verify_version(final_exe, expected_version)

        if preserve:
            _restore_user_data(install_dir, stash)
            if backup_created:
                _salvage_user_data(
                    install_dir, stash, backup, PRESERVE_ON_UPDATE
                )

        if expected_version:
            _write_version_marker(install_dir, expected_version)

        if backup_created:
            # Keep the backup when a preserved user file is still only
            # available there; losing settings is worse than a stale folder.
            stranded = [
                name
                for name in PRESERVE_ON_UPDATE
                if (backup / name).exists() and not (install_dir / name).exists()
            ]
            if not stranded:
                try:
                    _rmtree(backup)
                except InstallError:
                    pass
        return final_exe

    except Exception as exc:
        # Roll back to the previous installation.  Only delete the target
        # directory when a backup was actually created - otherwise the
        # original installation is still in place and must be kept.
        try:
            if backup_created:
                if install_dir.exists():
                    _rmtree(install_dir)
                if backup.exists():
                    _move(backup, install_dir)
            if preserve:
                if not install_dir.exists():
                    install_dir.mkdir(parents=True, exist_ok=True)
                _restore_user_data(install_dir, stash)
        except Exception:
            pass
        if isinstance(exc, InstallError):
            raise
        raise InstallError(f"Could not install BuzzInstaller: {exc}") from exc
    finally:
        try:
            _rmtree(staging)
        except InstallError:
            pass
        # Never delete a non-empty stash: it may hold the only copy of a
        # locked user file.  It stays in %TEMP% for manual recovery.
        if stash.exists():
            try:
                if not any(stash.iterdir()):
                    _rmtree(stash)
            except (OSError, InstallError):
                pass


def launch_app(exe_path: Path, *args: str) -> None:
    """Launch BuzzInstaller detached from the installer/updater."""
    try:
        subprocess.Popen(
            [str(exe_path), *args],
            close_fds=True,
            cwd=str(Path(exe_path).parent),
            creationflags=(
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            )
            if sys.platform == "win32"
            else 0,
        )
    except OSError as exc:
        raise InstallError(f"Could not launch BuzzInstaller: {exc}") from exc


def register_integration(exe_path: Path, timeout: int = 30) -> bool:
    """Register the BZ:// protocol and Explorer menu via ``--install``."""
    exe_path = Path(exe_path)
    if not exe_path.is_file():
        return False
    try:
        result = subprocess.run(
            [str(exe_path), "--install"],
            capture_output=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32"
            else 0,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def unregister_integration(exe_path: Path, timeout: int = 30) -> bool:
    exe_path = Path(exe_path)
    if not exe_path.is_file():
        return False
    try:
        result = subprocess.run(
            [str(exe_path), "--uninstall"],
            capture_output=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32"
            else 0,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


# ---------------------------------------------------------------------------
# Removal / deep clean
# ---------------------------------------------------------------------------
def delete_install(install_dir: Path) -> bool:
    """Remove BuzzInstaller, its shortcuts and its PATH entry."""
    install_dir = Path(install_dir)
    exe = install_dir / EXE_NAME
    try:
        stop_app_processes(install_dir if install_dir.exists() else None)
    except InstallError:
        pass
    unregister_integration(exe)
    try:
        _rmtree(install_dir)
    except InstallError:
        return False
    shortcuts.remove_all_shortcuts()
    try:
        pathman.remove_from_path(install_dir)
    except pathman.PathError:
        pass
    metadata.clear_metadata()
    return True


def _delete_registry_tree(root, subkey: str) -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_registry_tree(root, f"{subkey}\\{child}")
        winreg.DeleteKey(root, subkey)
        return True
    except (FileNotFoundError, OSError):
        return False


def _delete_registry_value(root, subkey: str, name: str) -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_WRITE) as key:
            winreg.DeleteValue(key, name)
            return True
    except (FileNotFoundError, OSError):
        return False


def clean_all() -> dict[str, bool]:
    """Remove every BuzzInstaller trace (used by the setup's CLEAN action)."""
    results: dict[str, bool] = {}
    try:
        stop_app_processes()
        results["processes"] = True
    except InstallError:
        results["processes"] = False

    if sys.platform == "win32":
        import winreg

        results["protocol"] = _delete_registry_tree(
            winreg.HKEY_CURRENT_USER, r"Software\Classes\BZ"
        )
        results["context_menu"] = _delete_registry_tree(
            winreg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\BuzzInstallerSend"
        ) or _delete_registry_tree(
            winreg.HKEY_CURRENT_USER, r"Software\Classes\Directory\shell\BuzzInstallerSend"
        )
        results["startup"] = any(
            _delete_registry_value(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                name,
            )
            for name in ("BuzzInstaller", "BuzzInstallerBackground")
        )
    else:
        results["protocol"] = False
        results["context_menu"] = False
        results["startup"] = False

    for key, path in (
        ("app_data", metadata.get_app_data_dir()),
        ("installer_data", metadata.get_metadata_path().parent),
        ("shortcuts", shortcuts.SHORTCUT_DIR),
    ):
        try:
            _rmtree(path)
            results[key] = True
        except InstallError:
            results[key] = False

    try:
        pathman.remove_from_path(metadata.get_default_install_dir())
        pathman.remove_from_path(metadata.get_install_dir())
        results["path"] = True
    except pathman.PathError:
        results["path"] = False

    shortcuts.remove_all_shortcuts()
    metadata.clear_metadata()
    return results
