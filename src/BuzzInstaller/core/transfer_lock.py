"""
Cross-process transfer locks.

A transfer UUID may only be executed by one worker at a time, even when
two BuzzInstaller processes are open.  Locks are byte-range locks on a
small file under ``%LOCALAPPDATA%\\BuzzInstaller\\transfers\\locks``:

* Windows: ``msvcrt.locking(LK_NBLCK)``
* POSIX:   ``fcntl.flock(LOCK_EX | LOCK_NB)``

The operating system releases the lock automatically if the process dies,
so stale lock files are harmless.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from config.paths import get_transfers_dir
from utils.logging import get_logger

logger = get_logger(__name__)


class TransferInProgressError(RuntimeError):
    """Another worker/process is already running this transfer."""


class TransferLock:
    """An exclusive, non-blocking lock for one transfer UUID."""

    def __init__(self, transfer_uuid: str,
                 directory: Optional[str | Path] = None) -> None:
        safe = "".join(
            c for c in str(transfer_uuid) if c.isalnum() or c in "-_"
        )[:96] or "unknown"
        self._directory = Path(directory) if directory is not None else (
            get_transfers_dir() / "locks"
        )
        self._path = self._directory / f"{safe}.lock"
        self._handle = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self, timeout: float = 0.0) -> bool:
        """Try to acquire the lock, waiting up to *timeout* seconds."""
        if self._handle is not None:
            return True
        self._directory.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            handle = open(self._path, "a+b")
            try:
                if self._lock_handle(handle):
                    try:
                        handle.seek(0)
                        handle.truncate()
                        handle.write(
                            f"{os.getpid()} {time.time():.3f}\n".encode("ascii")
                        )
                        handle.flush()
                    except OSError:
                        pass
                    self._handle = handle
                    return True
            except OSError:
                pass
            try:
                handle.close()
            except OSError:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            self._unlock_handle(handle)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass

    def __enter__(self) -> "TransferLock":
        if not self.acquire():
            raise TransferInProgressError(
                f"Transfer is already running: {self._path.name}"
            )
        return self

    def __exit__(self, *_exc) -> None:
        self.release()

    def is_locked(self) -> bool:
        """True when another live worker holds this transfer's lock."""
        probe = TransferLock(self._path.stem, self._directory)
        if probe.acquire():
            probe.release()
            return False
        return True

    # -- platform primitives ---------------------------------------------
    @staticmethod
    def _lock_handle(handle) -> bool:
        if sys.platform == "win32":
            import msvcrt
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        import fcntl
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    @staticmethod
    def _unlock_handle(handle) -> None:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


_ACTIVE_LOCK = threading.Lock()
_ACTIVE: set[str] = set()


class TransferGuard:
    """Combined in-process and cross-process guard for one transfer."""

    def __init__(self, transfer_uuid: str,
                 directory: Optional[str | Path] = None) -> None:
        self.uuid = str(transfer_uuid)
        self._lock = TransferLock(self.uuid, directory)
        self._reserved = False

    def acquire(self, timeout: float = 0.0) -> bool:
        with _ACTIVE_LOCK:
            if self.uuid in _ACTIVE:
                return False
            _ACTIVE.add(self.uuid)
            self._reserved = True
        if self._lock.acquire(timeout=timeout):
            return True
        self.release()
        return False

    def release(self) -> None:
        if self._reserved:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(self.uuid)
            self._reserved = False
        self._lock.release()

    def __enter__(self) -> "TransferGuard":
        if not self.acquire():
            raise TransferInProgressError(
                f"Transfer is already running: {self.uuid}"
            )
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def held_transfer_uuids(uuids) -> set[str]:
    """Return the subset of *uuids* whose locks are currently held."""
    held: set[str] = set()
    for uuid in uuids:
        try:
            if TransferLock(str(uuid)).is_locked():
                held.add(str(uuid))
        except OSError:
            continue
    return held
