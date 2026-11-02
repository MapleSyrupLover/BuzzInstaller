"""
Folder archives for bundle transfers.

Folders are packaged as ZIP64 archives using STORE mode (no compression).
Most files people send are already compressed, so DEFLATE would burn CPU
for little benefit and would make a resumed transfer depend on identical
compression settings.

The archive is written with bounded memory: each member is streamed in
1 MiB blocks instead of being read into RAM.  Extraction is delegated to
the existing, hardened :func:`utils.file_utils.extract_zip`.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Callable, Optional

from utils.file_utils import extract_zip, sanitize_filename
from utils.logging import get_logger

logger = get_logger(__name__)

ARCHIVE_FORMAT = "zip64-store"
IO_BLOCK = 1024 * 1024


class ArchiveError(Exception):
    """Folder archive creation failed."""


class ArchiveCancelled(ArchiveError):
    """The user cancelled archive creation."""


def archive_name(folder_name: str) -> str:
    safe = sanitize_filename(folder_name, max_len=160) or "archive"
    if not safe.lower().endswith(".zip"):
        safe += ".zip"
    return safe


def estimate_archive_size(source_dir: str | Path) -> int:
    """Approximate uncompressed size (used for progress before creation)."""
    root = Path(source_dir)
    total = 0
    for item in root.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def create_zip64_store(
    source_dir: str | Path,
    destination: str | Path,
    progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Path:
    """
    Create a ZIP64 archive of *source_dir* using STORE mode.

    Directory structure and filenames are preserved.  Symlinks are skipped
    so the archive cannot escape the source tree or recurse forever.
    """
    root = Path(source_dir)
    if not root.is_dir():
        raise ArchiveError(f"Source folder does not exist: {root}")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)

    files: list[tuple[Path, str]] = []
    dirs: list[str] = []
    for item in sorted(root.rglob("*"), key=lambda p: p.as_posix().lower()):
        if item.is_symlink():
            continue
        try:
            relative = item.relative_to(root).as_posix()
        except ValueError:
            continue
        if item.is_dir():
            dirs.append(relative.rstrip("/") + "/")
        elif item.is_file():
            files.append((item, relative))

    total = 0
    for item, _ in files:
        try:
            total += item.stat().st_size
        except OSError:
            pass

    done = 0
    try:
        with zipfile.ZipFile(
            target, "w", compression=zipfile.ZIP_STORED, allowZip64=True,
        ) as archive:
            for directory in dirs:
                info = zipfile.ZipInfo(directory)
                info.external_attr = (0o40755 << 16) | 0x10
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, b"")
            for item, relative in files:
                if cancelled is not None and cancelled():
                    raise ArchiveCancelled()
                info = zipfile.ZipInfo.from_file(str(item), arcname=relative)
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = 0o100644 << 16
                try:
                    with open(item, "rb") as src, archive.open(
                        info, "w", force_zip64=True,
                    ) as out:
                        while True:
                            if cancelled is not None and cancelled():
                                raise ArchiveCancelled()
                            block = src.read(IO_BLOCK)
                            if not block:
                                break
                            out.write(block)
                            done += len(block)
                            if progress_cb is not None:
                                if progress_cb(done, total) is False:
                                    raise ArchiveCancelled()
                except ArchiveCancelled:
                    raise
                except OSError as exc:
                    raise ArchiveError(
                        f"Cannot add {item} to the archive: {exc}"
                    ) from exc
    except ArchiveCancelled:
        target.unlink(missing_ok=True)
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        target.unlink(missing_ok=True)
        raise ArchiveError(f"Could not create ZIP64 archive: {exc}") from exc

    logger.info(
        "Created ZIP64 STORE archive: %s (%d bytes, %d files)",
        target, target.stat().st_size if target.exists() else 0, len(files),
    )
    return target


def extract_bundle_archive(
    archive_path: str | Path,
    destination: str | Path,
    overwrite: bool = False,
) -> list[Path]:
    """Extract a reconstructed bundle archive with the existing extractor."""
    return extract_zip(archive_path, destination, overwrite=overwrite)
