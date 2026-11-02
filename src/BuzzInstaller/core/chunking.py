"""
Chunk planning and streaming I/O for resumable bundle transfers.

The chunking engine is deliberately independent of the network and UI
layers.  It only describes *where* each chunk lives inside a source file
and provides bounded-memory helpers to read, hash and copy those byte
ranges.

Design rules
------------
* Chunks are byte ranges of the original file — no duplicate chunk files
  are created for uploads.
* A chunk is at most ``chunk_size`` bytes.  The final chunk may be
  smaller; an exactly divisible file simply has no partial tail.
* Ordering is deterministic: chunk ``0`` starts at offset ``0`` and every
  following chunk starts where the previous one ended.
* All read helpers stream in 1 MiB blocks, so memory usage stays bounded
  regardless of file size.
* The original file is never modified.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from utils.file_utils import sanitize_filename

# 10 GiB default.  Configurable through ``chunk_size_bytes``.
DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024 * 1024
MIN_CHUNK_SIZE = 1 * 1024 * 1024          # 1 MiB
MAX_CHUNK_SIZE = 1024 * 1024 * 1024 * 1024  # 1 TiB

# Streaming block size for hashing/copying.  This is intentionally small
# and independent from the upload transport chunk size.
IO_BLOCK = 1024 * 1024  # 1 MiB


class ChunkingError(Exception):
    """Base error for chunk planning / local chunk I/O."""


class SourceChangedError(ChunkingError):
    """The source file no longer matches the identity recorded on disk."""


@dataclass(frozen=True)
class ChunkSpec:
    """A deterministic byte range inside a source file."""

    index: int
    offset: int
    size: int
    name: str
    sha256: str = ""
    remote_code: str = ""
    remote_url: str = ""

    @property
    def end(self) -> int:
        return self.offset + self.size

    def to_dict(self) -> dict:
        data = {
            "index": self.index,
            "offset": self.offset,
            "size": self.size,
            "name": self.name,
        }
        if self.sha256:
            data["sha256"] = self.sha256
        if self.remote_code:
            data["remote_code"] = self.remote_code
        if self.remote_url:
            data["remote_url"] = self.remote_url
        return data


def clamp_chunk_size(value: int | float | None) -> int:
    """Return a usable chunk size inside the supported bounds."""
    try:
        size = int(value) if value is not None else DEFAULT_CHUNK_SIZE
    except (TypeError, ValueError):
        size = DEFAULT_CHUNK_SIZE
    return max(MIN_CHUNK_SIZE, min(MAX_CHUNK_SIZE, size))


def chunk_size_from_config(config: Optional[dict]) -> int:
    """Read ``chunk_size_bytes`` from a config dict with a safe fallback."""
    if not config:
        return DEFAULT_CHUNK_SIZE
    return clamp_chunk_size(config.get("chunk_size_bytes", DEFAULT_CHUNK_SIZE))


def chunk_count(file_size: int, chunk_size: int) -> int:
    """Number of chunks for *file_size* (zero-byte files have no chunks)."""
    if file_size <= 0:
        return 0
    chunk_size = clamp_chunk_size(chunk_size)
    return (int(file_size) + chunk_size - 1) // chunk_size


def chunk_name(original_name: str, transfer_uuid: str, index: int,
               total: int) -> str:
    """
    Build a unique, deterministic remote filename for one chunk.

    The zero-padded index keeps lexical order identical to numeric order
    (important when a human looks at the remote file list), while the
    short transfer id prevents collisions between different transfers of
    the same file.
    """
    raw = Path(str(original_name)).name or "bundle"
    stem = sanitize_filename(Path(raw).stem or raw, max_len=80) or "bundle"
    short_id = "".join(c for c in str(transfer_uuid) if c.isalnum())[:8] or "00000000"
    width = max(5, len(str(max(0, total - 1))))
    return f"{stem}.{short_id}.part{index:0{width}d}-of-{total:0{width}d}.bzbchunk"


def plan_chunks(
    path: str | Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    transfer_uuid: str = "",
    compute_hashes: bool = False,
    progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> list[ChunkSpec]:
    """
    Plan the chunks of *path* without modifying it.

    Parameters
    ----------
    chunk_size
        Maximum chunk size.  The final chunk may be smaller.
    transfer_uuid
        Used to make chunk filenames unique.  A deterministic placeholder
        is used when omitted.
    compute_hashes
        Hash every chunk while planning (one extra streaming read).
    progress_cb
        Optional ``(bytes_done, total_bytes)`` callback used while hashing.
    cancelled
        Optional callable checked between read blocks.
    """
    source = Path(path)
    if not source.is_file():
        raise ChunkingError(f"Source file does not exist: {source}")

    size = source.stat().st_size
    size_chunk = clamp_chunk_size(chunk_size)
    total = chunk_count(size, size_chunk)
    if total == 0:
        return []

    specs: list[ChunkSpec] = []
    for index in range(total):
        offset = index * size_chunk
        length = min(size_chunk, size - offset)
        specs.append(ChunkSpec(
            index=index,
            offset=offset,
            size=length,
            name=chunk_name(source.name, transfer_uuid, index, total),
        ))

    if not compute_hashes:
        return specs

    hashed: list[ChunkSpec] = []
    done = 0
    for spec in specs:
        digest = hash_range(
            source, spec.offset, spec.size,
            progress_cb=None, cancelled=cancelled,
        )
        hashed.append(ChunkSpec(
            index=spec.index, offset=spec.offset, size=spec.size,
            name=spec.name, sha256=digest,
        ))
        done += spec.size
        if progress_cb is not None:
            if progress_cb(done, size) is False:
                raise ChunkingError("Chunk planning cancelled")
    return hashed


def iter_range(
    path: str | Path,
    offset: int,
    length: int,
    read_size: int = IO_BLOCK,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Iterator[bytes]:
    """Yield the byte range ``[offset, offset+length)`` in bounded blocks."""
    if length < 0:
        raise ChunkingError("Chunk length cannot be negative")
    remaining = int(length)
    with open(path, "rb") as handle:
        handle.seek(int(offset))
        while remaining > 0:
            if cancelled is not None and cancelled():
                raise ChunkingError("Chunk read cancelled")
            block = handle.read(min(read_size, remaining))
            if not block:
                raise ChunkingError(
                    f"Unexpected end of file while reading chunk at offset {offset}"
                )
            remaining -= len(block)
            yield block


def hash_range(
    path: str | Path,
    offset: int,
    length: int,
    read_size: int = IO_BLOCK,
    cancelled: Optional[Callable[[], bool]] = None,
) -> str:
    """SHA-256 of a byte range, streamed with bounded memory."""
    digest = hashlib.sha256()
    for block in iter_range(path, offset, length, read_size, cancelled):
        digest.update(block)
    return digest.hexdigest()


def copy_range(
    source: str | Path,
    offset: int,
    length: int,
    destination: str | Path,
    read_size: int = IO_BLOCK,
    cancelled: Optional[Callable[[], bool]] = None,
) -> tuple[int, str]:
    """
    Stream one byte range into *destination*.

    Returns ``(bytes_written, sha256)``.  The destination is written from
    scratch; the caller decides when it is safe to keep the file.
    """
    digest = hashlib.sha256()
    written = 0
    with open(destination, "wb") as out:
        for block in iter_range(source, offset, length, read_size, cancelled):
            out.write(block)
            digest.update(block)
            written += len(block)
    return written, digest.hexdigest()


def sha256_file(path: str | Path, block_size: int = IO_BLOCK,
                cancelled: Optional[Callable[[], bool]] = None) -> str:
    """Streaming SHA-256 of an entire file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            if cancelled is not None and cancelled():
                raise ChunkingError("File hashing cancelled")
            block = handle.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify_source_identity(
    path: str | Path,
    expected_size: int,
    expected_sha256: str,
    cancelled: Optional[Callable[[], bool]] = None,
) -> None:
    """
    Raise :class:`SourceChangedError` unless *path* still matches.

    Both size and SHA-256 are checked so a same-size edit cannot be
    silently resumed with stale chunk data.
    """
    source = Path(path)
    if not source.is_file():
        raise SourceChangedError(f"Source file is missing: {source}")
    actual_size = source.stat().st_size
    if int(actual_size) != int(expected_size):
        raise SourceChangedError(
            f"Source file size changed ({actual_size} != {expected_size})."
        )
    if not expected_sha256:
        return
    actual = sha256_file(source, cancelled=cancelled)
    if actual.lower() != str(expected_sha256).lower():
        raise SourceChangedError(
            "Source file content changed since the transfer started."
        )


def source_identity(path: str | Path,
                    cancelled: Optional[Callable[[], bool]] = None) -> tuple[int, str]:
    """Return ``(size, sha256)`` for a source file."""
    source = Path(path)
    if not source.is_file():
        raise ChunkingError(f"Source file does not exist: {source}")
    return source.stat().st_size, sha256_file(source, cancelled=cancelled)


def is_bundle_candidate(path: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> bool:
    """True when *path* should use the resumable bundle path.

    Folders always use a bundle (ZIP64 + chunks).  Files use it only when
    they are larger than one configured chunk, preserving the existing
    single-request upload path for ordinary small files.
    """
    p = Path(path)
    if p.is_dir():
        return True
    try:
        return p.is_file() and p.stat().st_size > clamp_chunk_size(chunk_size)
    except OSError:
        return False


def total_size(paths: Iterable[str | Path]) -> int:
    """Best-effort total size of files/folders (used for progress totals)."""
    total = 0
    for raw in paths:
        p = Path(raw)
        try:
            if p.is_file():
                total += p.stat().st_size
            elif p.is_dir():
                for item in p.rglob("*"):
                    if item.is_file():
                        total += item.stat().st_size
        except OSError:
            continue
    return total


def human_chunk_label(index: int, total: int) -> str:
    """``Chunk 3 of 12`` style label (1-based for display)."""
    return f"Chunk {index + 1} of {total}"


def _safe_relative_name(name: str) -> str:
    """Internal helper kept for symmetry with the archive module."""
    return sanitize_filename(os.path.basename(name))
