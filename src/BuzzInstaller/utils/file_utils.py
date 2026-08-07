"""
File-system helper utilities.

Pure, no external dependencies — safe to import from any thread.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Filename sanitisation
# ---------------------------------------------------------------------------
def sanitize_filename(name: str, max_len: int = 200) -> str:
    """Make *name* safe for use as a Windows file name."""
    keep = " ._-()"
    cleaned = "".join(c if c.isalnum() or c in keep else "_" for c in name).strip(" ._")
    cleaned = cleaned or "unnamed"
    return cleaned[:max_len]


def get_unique_path(directory: Path, filename: str) -> Path:
    """Return *directory/filename*, appending a counter if it already exists."""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# Size formatting
# ---------------------------------------------------------------------------
def format_size(num_bytes: int) -> str:
    """Human-readable byte size (e.g. ``1.4 MiB``)."""
    if num_bytes < 0:
        return "0 B"
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024.0 or unit == "PiB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PiB"


# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------
def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# ZIP handling
# ---------------------------------------------------------------------------
def is_zip(path: str | Path) -> bool:
    """Quick check whether *path* is a valid ZIP archive."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        return zipfile.is_zipfile(p)
    except OSError:
        return False


def extract_zip(zip_path: str | Path, dest_dir: str | Path) -> list[Path]:
    """
    Extract *zip_path* into *dest_dir*.

    Returns a sorted list of extracted file paths.
    """
    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Sanitise member names to prevent path traversal (zip-slip)
        for member in zf.infolist():
            safe_name = sanitize_member(member.filename)
            target = dest_dir / safe_name
            # Final safety check
            if not str(target.resolve()).startswith(str(dest_dir.resolve())):
                continue
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                extracted.append(target)
    return sorted(extracted)


# Names like "src/" or "sub\\file" or "C:\\evil" need normalisation
def sanitize_member(name: str) -> str:
    import os.path as osp
    # Replace backslash with forward slash, strip leading slashes
    clean = name.replace("\\", "/")
    clean = clean.lstrip("/")
    # Keep relative path components
    parts = [p for p in clean.split("/") if p not in ("", ".", "..")]
    return "/".join(parts) if parts else "unnamed"


# ---------------------------------------------------------------------------
# Download filename guess
# ---------------------------------------------------------------------------
def guess_filename_from_content(content_disp: str | None, code: str,
                                content_type: str | None = None) -> str:
    """Best-effort filename from Content-Disposition header."""
    if content_disp:
        # filename="example.zip"
        for part in content_disp.split(";"):
            part = part.strip()
            if part.lower().startswith("filename="):
                fname = part.split("=", 1)[1].strip().strip('"')
                if fname:
                    return sanitize_filename(fname)
        for part in content_disp.split(";"):
            part = part.strip()
            if part.lower().startswith("filename*="):
                # RFC 5987: filename*=UTF-8''example.zip
                fname = part.split("=", 1)[1].strip()
                if fname.startswith("UTF-8''"):
                    fname = fname[7:]
                from urllib.parse import unquote
                fname = unquote(fname).strip('"')
                if fname:
                    return sanitize_filename(fname)
    # Fallback
    return f"{code}.zip" if content_type and "zip" in (content_type or "") else code


def safe_join(base: Path, *parts: str) -> Path:
    """Join *parts* under *base*, refusing results that escape via '..'."""
    target = (base / "/".join(parts)).resolve()
    base_resolved = base.resolve()
    if not str(target).startswith(str(base_resolved)):
        raise ValueError(f"Path traversal detected: {parts}")
    return target
