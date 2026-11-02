"""
BZB bundle manifest — the versioned JSON description of a chunked transfer.

A bundle is uploaded as ordinary BuzzHeavier files: one file per chunk
plus a final manifest file.  The manifest is the only file referenced by
the public ``BZ://`` link; receivers resolve it, validate it, then fetch
the chunks.

Security
--------
The manifest is untrusted input.  :func:`parse_manifest` validates the
format identifier, version, UUID, sizes, chunk ordering, checksums and
remote codes before anything is downloaded or written to disk.  Original
filenames are reduced to a safe basename and are never used directly as a
path.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from utils.file_utils import sanitize_filename

FORMAT_NAME = "BuzzInstaller-Bundle"
FORMAT_ID = "BZB"
FORMAT_VERSION = 1

MAX_CHUNKS = 1_000_000
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_UUID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_REMOTE_CODE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")


class ManifestError(ValueError):
    """The manifest is missing, malformed, unsupported or unsafe."""


def manifest_filename(original_name: str, transfer_uuid: str) -> str:
    """Deterministic, unique remote filename for a bundle manifest."""
    raw = Path(str(original_name or "bundle")).name or "bundle"
    stem = sanitize_filename(Path(raw).stem or raw, max_len=80) or "bundle"
    short_id = "".join(c for c in str(transfer_uuid) if c.isalnum())[:8] or "00000000"
    return f"{stem}.{short_id}.bzb"


def build_manifest(
    *,
    transfer_uuid: str,
    name: str,
    size: int,
    sha256: str,
    chunk_size: int,
    chunks: Iterable[dict[str, Any]],
    kind: str = "file",
    folder_name: str = "",
    archive_format: str = "",
    created_at: Optional[float] = None,
) -> dict[str, Any]:
    """
    Build a validated manifest dictionary.

    *chunks* must be ordered by index and contain ``index``, ``size``,
    ``sha256``, ``name``, ``remote_code`` and optionally ``remote_url``.
    """
    ordered: list[dict[str, Any]] = []
    for item in chunks:
        ordered.append({
            "index": int(item["index"]),
            "offset": int(item.get("offset", 0)),
            "size": int(item["size"]),
            "sha256": str(item.get("sha256", "")),
            "name": str(item.get("name", "")),
            "remote_code": str(item.get("remote_code", "")),
            "remote_url": str(item.get("remote_url", "")),
            "bz_url": str(item.get("bz_url", "")),
        })
    ordered.sort(key=lambda c: c["index"])

    manifest = {
        "format": FORMAT_NAME,
        "format_id": FORMAT_ID,
        "version": FORMAT_VERSION,
        "transfer_uuid": str(transfer_uuid),
        "name": str(name),
        "kind": str(kind or "file"),
        "size": int(size),
        "sha256": str(sha256),
        "chunk_size": int(chunk_size),
        "total_chunks": len(ordered),
        "created_at": float(created_at if created_at is not None else time.time()),
        "chunks": ordered,
    }
    if folder_name:
        manifest["folder_name"] = str(folder_name)
    if archive_format:
        manifest["archive_format"] = str(archive_format)
    # Validate our own output before it can be uploaded.
    return parse_manifest(manifest)


def serialize_manifest(manifest: dict[str, Any]) -> bytes:
    """Canonical compact JSON bytes."""
    normalized = parse_manifest(manifest)
    return json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> Path:
    """Atomically write a manifest to *path*."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(serialize_manifest(manifest))
    os.replace(tmp, target)
    return target


def read_manifest(path: str | Path) -> dict[str, Any]:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ManifestError(f"Cannot read manifest: {exc}") from exc
    return parse_manifest(data)


def is_manifest_prefix(data: bytes | bytearray | memoryview) -> bool:
    """
    Cheap check used before a full manifest download.

    It intentionally requires both the format id and the version key so a
    normal JSON file is not mistaken for a bundle.
    """
    if not data:
        return False
    head = bytes(data[:16384]).lstrip(b"\xef\xbb\xbf \t\r\n")
    if not head.startswith(b"{"):
        return False
    text = head.decode("utf-8", "ignore")
    return (
        f'"format_id"' in text
        and f'"{FORMAT_ID}"' in text
        and f'"version"' in text
    )


def parse_manifest(data: bytes | bytearray | str | dict[str, Any]) -> dict[str, Any]:
    """Parse and fully validate an untrusted manifest."""
    if isinstance(data, dict):
        raw = data
    else:
        if isinstance(data, (bytes, bytearray, memoryview)):
            text = bytes(data).decode("utf-8-sig", "strict")
        else:
            text = str(data)
        try:
            raw = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ManifestError("Manifest is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise ManifestError("Manifest root must be a JSON object.")

    if raw.get("format_id") != FORMAT_ID:
        raise ManifestError("Not a BZB bundle manifest.")
    if raw.get("format") != FORMAT_NAME:
        raise ManifestError("Unsupported bundle format.")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ManifestError("Manifest version is missing or invalid.")
    if version != FORMAT_VERSION:
        raise ManifestError(
            f"Unsupported bundle manifest version {version}; "
            f"this build supports version {FORMAT_VERSION}."
        )

    transfer_uuid = raw.get("transfer_uuid")
    if not isinstance(transfer_uuid, str) or not _UUID.match(transfer_uuid):
        raise ManifestError("Manifest transfer UUID is invalid.")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ManifestError("Manifest original filename is missing.")
    safe_name = safe_original_name(name)
    if not safe_name:
        raise ManifestError("Manifest original filename is unsafe.")

    kind = raw.get("kind", "file")
    if kind not in ("file", "folder"):
        raise ManifestError("Manifest kind must be 'file' or 'folder'.")

    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ManifestError("Manifest size is invalid.")

    sha256 = raw.get("sha256")
    if not isinstance(sha256, str) or not _HEX64.match(sha256):
        raise ManifestError("Manifest SHA-256 is invalid.")

    chunk_size = raw.get("chunk_size")
    if (not isinstance(chunk_size, int) or isinstance(chunk_size, bool)
            or chunk_size <= 0):
        raise ManifestError("Manifest chunk size is invalid.")

    total = raw.get("total_chunks")
    if (not isinstance(total, int) or isinstance(total, bool)
            or total < 0 or total > MAX_CHUNKS):
        raise ManifestError("Manifest chunk count is invalid.")

    chunks_raw = raw.get("chunks")
    if not isinstance(chunks_raw, list) or len(chunks_raw) != total:
        raise ManifestError("Manifest chunk list does not match total_chunks.")
    if size == 0 and total != 0:
        raise ManifestError("An empty file cannot contain chunks.")
    if size > 0 and total == 0:
        raise ManifestError("A non-empty file must contain at least one chunk.")

    chunks: list[dict[str, Any]] = []
    expected_offset = 0
    for expected_index, item in enumerate(chunks_raw):
        if not isinstance(item, dict):
            raise ManifestError("Manifest chunk entry is not an object.")
        index = item.get("index")
        if index != expected_index:
            raise ManifestError("Manifest chunks are not in deterministic order.")
        offset = item.get("offset", expected_offset)
        if offset != expected_offset:
            raise ManifestError("Manifest chunk offsets are not contiguous.")
        chunk_length = item.get("size")
        if (not isinstance(chunk_length, int) or isinstance(chunk_length, bool)
                or chunk_length <= 0):
            raise ManifestError(f"Chunk {index} has an invalid size.")
        if chunk_length > chunk_size:
            raise ManifestError(f"Chunk {index} exceeds the configured chunk size.")
        chunk_sha = item.get("sha256")
        if not isinstance(chunk_sha, str) or not _HEX64.match(chunk_sha):
            raise ManifestError(f"Chunk {index} has an invalid SHA-256.")
        remote_code = item.get("remote_code")
        if not isinstance(remote_code, str) or not _REMOTE_CODE.match(remote_code):
            raise ManifestError(f"Chunk {index} has an invalid remote code.")
        remote_url = item.get("remote_url", "")
        if remote_url and not isinstance(remote_url, str):
            raise ManifestError(f"Chunk {index} has an invalid remote URL.")
        chunk_name = item.get("name", "")
        if not isinstance(chunk_name, str):
            raise ManifestError(f"Chunk {index} has an invalid name.")
        chunks.append({
            "index": index,
            "offset": expected_offset,
            "size": chunk_length,
            "sha256": chunk_sha.lower(),
            "name": safe_original_name(chunk_name) if chunk_name else f"chunk-{index}",
            "remote_code": remote_code,
            "remote_url": remote_url,
            "bz_url": str(item.get("bz_url", "") or ""),
        })
        expected_offset += chunk_length

    if expected_offset != size:
        raise ManifestError("Manifest chunk sizes do not add up to the file size.")

    created_at = raw.get("created_at", 0)
    try:
        created_at = float(created_at)
    except (TypeError, ValueError):
        created_at = 0.0

    folder_name = raw.get("folder_name", "")
    if folder_name and not isinstance(folder_name, str):
        raise ManifestError("Manifest folder name is invalid.")
    archive_format = raw.get("archive_format", "")
    if archive_format and not isinstance(archive_format, str):
        raise ManifestError("Manifest archive format is invalid.")

    return {
        "format": FORMAT_NAME,
        "format_id": FORMAT_ID,
        "version": FORMAT_VERSION,
        "transfer_uuid": transfer_uuid,
        "name": safe_name,
        "kind": kind,
        "size": size,
        "sha256": sha256.lower(),
        "chunk_size": chunk_size,
        "total_chunks": total,
        "created_at": created_at,
        "chunks": chunks,
        "folder_name": safe_original_name(folder_name) if folder_name else "",
        "archive_format": archive_format,
    }


def safe_original_name(name: str) -> str:
    """
    Reduce an untrusted filename to a safe basename.

    Directory components, drive letters, UNC prefixes and ``..`` are
    discarded rather than trusted.  The result is always a single name
    that can be joined to a destination directory.
    """
    if not name:
        return ""
    text = str(name).replace("\\", "/")
    # Drop any directory portion and Windows drive/UNC prefixes.
    text = text.split("/")[-1]
    text = text.replace(":", "").strip()
    if text in ("", ".", ".."):
        return ""
    cleaned = sanitize_filename(text, max_len=200)
    if not cleaned or cleaned in (".", ".."):
        return ""
    return cleaned


def manifest_chunk_codes(manifest: dict[str, Any]) -> list[str]:
    return [str(chunk["remote_code"]) for chunk in manifest["chunks"]]


def manifest_chunk_map(manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(chunk["index"]): chunk for chunk in manifest["chunks"]}
