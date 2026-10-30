"""
Persistent transfer history.

Every upload and download is recorded in
``%LOCALAPPDATA%\\BuzzInstaller\\history.json`` so the dashboard can show
recent activity and the History page can offer re-copy / re-download /
open-folder actions.  The store is intentionally tiny and dependency-free
and is safe to call from worker threads.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from config.paths import get_app_dir
from utils.logging import get_logger

logger = get_logger(__name__)

HISTORY_FILE_NAME = "history.json"
MAX_ENTRIES = 500


@dataclass
class HistoryEntry:
    kind: str                     # "upload" | "download"
    name: str
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    size: int = 0
    code: str = ""
    bz_url: str = ""
    url: str = ""
    path: str = ""                # local path (downloads)
    extracted_to: str = ""        # extracted folder (downloads)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["HistoryEntry"]:
        try:
            kind = str(data.get("kind", ""))
            name = str(data.get("name", ""))
            if kind not in ("upload", "download") or not name:
                return None
            return cls(
                kind=kind,
                name=name,
                uid=str(data.get("uid") or uuid.uuid4().hex),
                size=int(data.get("size", 0) or 0),
                code=str(data.get("code", "") or ""),
                bz_url=str(data.get("bz_url", "") or ""),
                url=str(data.get("url", "") or ""),
                path=str(data.get("path", "") or ""),
                extracted_to=str(data.get("extracted_to", "") or ""),
                timestamp=float(data.get("timestamp", time.time()) or time.time()),
            )
        except (TypeError, ValueError):
            return None


class HistoryStore:
    """Thread-safe JSON-backed history store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[HistoryEntry] = []
        self._loaded = False

    @property
    def path(self) -> Path:
        return get_app_dir() / HISTORY_FILE_NAME

    # -- persistence -----------------------------------------------------
    def _load_locked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            items = data.get("entries", []) if isinstance(data, dict) else []
            entries = []
            for item in items:
                entry = HistoryEntry.from_dict(item) if isinstance(item, dict) else None
                if entry is not None:
                    entries.append(entry)
            self._entries = entries[:MAX_ENTRIES]
        except FileNotFoundError:
            self._entries = []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("History read failed: %s", exc)
            self._entries = []

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            payload = {
                "version": 1,
                "entries": [e.to_dict() for e in self._entries],
            }
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning("History write failed: %s", exc)

    # -- public API ------------------------------------------------------
    def entries(self) -> list[HistoryEntry]:
        with self._lock:
            self._load_locked()
            return list(self._entries)

    def recent(self, limit: int = 5, kind: Optional[str] = None) -> list[HistoryEntry]:
        items = self.entries()
        if kind:
            items = [e for e in items if e.kind == kind]
        return items[:limit]

    def count(self, kind: Optional[str] = None) -> int:
        items = self.entries()
        if kind:
            items = [e for e in items if e.kind == kind]
        return len(items)

    def add(self, entry: HistoryEntry) -> HistoryEntry:
        with self._lock:
            self._load_locked()
            self._entries.insert(0, entry)
            # De-duplicate identical transfers recorded twice in quick
            # succession (e.g. a retried UI action).
            seen: set[tuple] = set()
            unique: list[HistoryEntry] = []
            for item in self._entries:
                key = (item.kind, item.code or item.path or item.name, item.name)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
            self._entries = unique[:MAX_ENTRIES]
            self._save_locked()
            return entry

    def remove(self, uid: str) -> bool:
        with self._lock:
            self._load_locked()
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.uid != uid]
            changed = len(self._entries) != before
            if changed:
                self._save_locked()
            return changed

    def clear(self) -> None:
        with self._lock:
            self._load_locked()
            self._entries = []
            self._save_locked()

    def reload(self) -> None:
        with self._lock:
            self._loaded = False
            self._load_locked()


# Shared singleton -----------------------------------------------------------
history = HistoryStore()


def format_relative_time(timestamp: float) -> str:
    """Format a UNIX timestamp as a short human-friendly relative time."""
    delta = max(0.0, time.time() - timestamp)
    if delta < 45:
        return "just now"
    if delta < 90:
        return "a minute ago"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 7200:
        return "an hour ago"
    if delta < 86400:
        return f"{int(delta // 3600)} hours ago"
    if delta < 172800:
        return "yesterday"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)} days ago"
    return time.strftime("%b %d", time.localtime(timestamp))
