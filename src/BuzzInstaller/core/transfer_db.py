"""
SQLite persistence for resumable bundle transfers.

Every transfer and every individual chunk is stored in
``%LOCALAPPDATA%\\BuzzInstaller\\transfers.db``.  Completed chunks are
committed immediately so an interrupted transfer can resume after an
application restart without re-uploading or re-downloading verified data.

Concurrency
-----------
A single :class:`TransferStore` instance is shared by all worker threads.
The underlying ``sqlite3`` connection is opened with
``check_same_thread=False`` and every write is serialised with a re-entrant
lock and an explicit ``BEGIN IMMEDIATE`` transaction.  WAL mode keeps
readers from blocking the chunk workers.

The store never trusts a caller's claim that a chunk is valid: the
expected size/checksum stored in the database must match the values passed
to :meth:`mark_chunk_completed`, otherwise the update is rejected.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

from config.paths import get_transfer_db_path
from core.chunking import ChunkSpec
from utils.logging import get_logger

logger = get_logger(__name__)

# Transfer status values -----------------------------------------------------
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_INTERRUPTED = "interrupted"
STATUS_FAILED = "failed"
STATUS_FINALIZING = "finalizing"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = (
    STATUS_PENDING, STATUS_RUNNING, STATUS_PAUSED,
    STATUS_INTERRUPTED, STATUS_FAILED, STATUS_FINALIZING,
)
RESUMABLE_STATUSES = (
    STATUS_PENDING, STATUS_PAUSED, STATUS_INTERRUPTED,
    STATUS_FAILED, STATUS_FINALIZING, STATUS_RUNNING,
)

# Chunk status values --------------------------------------------------------
CHUNK_PENDING = "pending"
CHUNK_UPLOADING = "uploading"
CHUNK_DOWNLOADING = "downloading"
CHUNK_COMPLETED = "completed"
CHUNK_FAILED = "failed"
CHUNK_RETRYING = "retrying"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transfers (
    uuid              TEXT PRIMARY KEY,
    direction         TEXT NOT NULL,
    kind              TEXT NOT NULL DEFAULT 'file',
    name              TEXT NOT NULL,
    source_path       TEXT NOT NULL DEFAULT '',
    size              INTEGER NOT NULL,
    sha256            TEXT NOT NULL DEFAULT '',
    chunk_size        INTEGER NOT NULL,
    total_chunks      INTEGER NOT NULL,
    completed_chunks  INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL,
    destination       TEXT NOT NULL DEFAULT '',
    manifest_code     TEXT NOT NULL DEFAULT '',
    manifest_url      TEXT NOT NULL DEFAULT '',
    manifest_sha256   TEXT NOT NULL DEFAULT '',
    manifest_json     TEXT NOT NULL DEFAULT '',
    extra             TEXT NOT NULL DEFAULT '{}',
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    last_error        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chunks (
    transfer_uuid  TEXT NOT NULL,
    chunk_index    INTEGER NOT NULL,
    offset         INTEGER NOT NULL,
    size           INTEGER NOT NULL,
    sha256         TEXT NOT NULL DEFAULT '',
    name           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending',
    attempts       INTEGER NOT NULL DEFAULT 0,
    remote_code    TEXT NOT NULL DEFAULT '',
    remote_url     TEXT NOT NULL DEFAULT '',
    local_path     TEXT NOT NULL DEFAULT '',
    updated_at     REAL NOT NULL,
    last_error     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (transfer_uuid, chunk_index),
    FOREIGN KEY (transfer_uuid) REFERENCES transfers(uuid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transfers_status
    ON transfers(direction, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_chunks_status
    ON chunks(transfer_uuid, status);
"""


class TransferStoreError(Exception):
    """Base error for transfer persistence."""


class TransferExistsError(TransferStoreError):
    """A transfer with the same UUID already exists."""


class ChunkVerificationError(TransferStoreError):
    """A chunk update did not match the persisted expected metadata."""


class TransferStore:
    """Thread-safe SQLite store for transfers and chunks."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(db_path) if db_path is not None else get_transfer_db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, timeout=30.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA user_version=1")
            self._conn.commit()

    # -- lifecycle -------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "TransferStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @contextmanager
    def _tx(self):
        """Serialise a write transaction across worker threads."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        data = dict(row)
        raw_extra = data.get("extra") or "{}"
        try:
            data["extra_data"] = json.loads(raw_extra) if raw_extra else {}
        except (TypeError, ValueError):
            data["extra_data"] = {}
        return data

    # -- creation --------------------------------------------------------
    def create_transfer(
        self,
        *,
        uuid: str,
        direction: str,
        name: str,
        size: int,
        sha256: str,
        chunk_size: int,
        chunks: Iterable[ChunkSpec | dict],
        kind: str = "file",
        source_path: str = "",
        status: str = STATUS_PENDING,
        destination: str = "",
        manifest_code: str = "",
        manifest_url: str = "",
        manifest_sha256: str = "",
        manifest_json: str = "",
        extra: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Create a transfer and all of its chunk rows in one transaction."""
        now = self._now()
        chunk_rows = [self._coerce_chunk(c) for c in chunks]
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT uuid FROM transfers WHERE uuid = ?", (uuid,)
            ).fetchone()
            if existing is not None:
                raise TransferExistsError(f"Transfer already exists: {uuid}")
            conn.execute(
                """
                INSERT INTO transfers (
                    uuid, direction, kind, name, source_path, size, sha256,
                    chunk_size, total_chunks, completed_chunks, status,
                    destination, manifest_code, manifest_url, manifest_sha256,
                    manifest_json, extra, created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')
                """,
                (
                    uuid, direction, kind, name, source_path, int(size),
                    str(sha256 or ""), int(chunk_size), len(chunk_rows),
                    status, destination, manifest_code, manifest_url,
                    manifest_sha256, manifest_json,
                    json.dumps(extra or {}, ensure_ascii=False),
                    now, now,
                ),
            )
            for chunk in chunk_rows:
                conn.execute(
                    """
                    INSERT INTO chunks (
                        transfer_uuid, chunk_index, offset, size, sha256,
                        name, status, attempts, remote_code, remote_url,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        uuid, chunk.index, chunk.offset, chunk.size,
                        chunk.sha256 or "", chunk.name, CHUNK_PENDING,
                        chunk.remote_code or "", chunk.remote_url or "", now,
                    ),
                )
        return self.get_transfer(uuid) or {}

    @staticmethod
    def _coerce_chunk(chunk: ChunkSpec | dict) -> ChunkSpec:
        if isinstance(chunk, ChunkSpec):
            return chunk
        return ChunkSpec(
            index=int(chunk["index"]),
            offset=int(chunk.get("offset", 0)),
            size=int(chunk["size"]),
            name=str(chunk.get("name", "")),
            sha256=str(chunk.get("sha256", "") or ""),
            remote_code=str(chunk.get("remote_code", "") or ""),
            remote_url=str(chunk.get("remote_url", "") or ""),
        )

    # -- reads -----------------------------------------------------------
    def get_transfer(self, uuid: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM transfers WHERE uuid = ?", (uuid,)
            ).fetchone()
        return self._row_to_dict(row)

    def get_chunks(self, uuid: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE transfer_uuid = ? ORDER BY chunk_index",
                (uuid,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_chunk(self, uuid: str, index: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chunks WHERE transfer_uuid = ? AND chunk_index = ?",
                (uuid, int(index)),
            ).fetchone()
        return dict(row) if row is not None else None

    def completed_chunk_indexes(self, uuid: str) -> set[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT chunk_index FROM chunks "
                "WHERE transfer_uuid = ? AND status = ?",
                (uuid, CHUNK_COMPLETED),
            ).fetchall()
        return {int(row["chunk_index"]) for row in rows}

    def list_transfers(
        self,
        direction: Optional[str] = None,
        statuses: Optional[Iterable[str]] = None,
        include_completed: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if direction:
            clauses.append("direction = ?")
            params.append(direction)
        if statuses is not None:
            status_list = list(statuses)
            if status_list:
                clauses.append(
                    "status IN (" + ",".join("?" for _ in status_list) + ")"
                )
                params.extend(status_list)
        elif not include_completed:
            clauses.append("status != ?")
            params.append(STATUS_COMPLETED)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM transfers{where} "
                "ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_dict(row) or {} for row in rows]

    def interrupted(self, direction: Optional[str] = None) -> list[dict[str, Any]]:
        """Transfers that can be resumed (never completed)."""
        return self.list_transfers(
            direction=direction,
            statuses=(STATUS_PENDING, STATUS_PAUSED, STATUS_INTERRUPTED,
                      STATUS_FAILED, STATUS_FINALIZING),
            limit=200,
        )

    def find_resumable(
        self,
        direction: str,
        source_path: str,
        size: int,
        sha256: str,
    ) -> Optional[dict[str, Any]]:
        """Find an unfinished transfer for the same source identity."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM transfers
                WHERE direction = ? AND source_path = ? AND size = ?
                  AND sha256 = ? AND status != ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (direction, source_path, int(size), str(sha256 or ""),
                 STATUS_COMPLETED),
            ).fetchone()
        return self._row_to_dict(row)

    def find_resumable_folder(
        self, original_path: str, name: str,
    ) -> Optional[dict[str, Any]]:
        """Find an unfinished folder upload for the same original folder."""
        wanted_path = str(original_path)
        wanted_name = str(name)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM transfers
                WHERE direction = 'upload' AND kind = 'folder'
                  AND status != ?
                ORDER BY updated_at DESC LIMIT 100
                """,
                (STATUS_COMPLETED,),
            ).fetchall()
        for row in rows:
            data = self._row_to_dict(row)
            if data is None:
                continue
            extra = data.get("extra_data") or {}
            if (str(extra.get("original_path", "")) == wanted_path
                    and str(data.get("name", "")) == wanted_name):
                return data
        return None

    def find_by_manifest_code(self, code: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM transfers WHERE manifest_code = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (str(code),),
            ).fetchone()
        return self._row_to_dict(row)

    # -- transfer updates -------------------------------------------------
    def set_status(
        self,
        uuid: str,
        status: str,
        error: str = "",
        destination: Optional[str] = None,
    ) -> None:
        now = self._now()
        with self._tx() as conn:
            if destination is None:
                conn.execute(
                    "UPDATE transfers SET status = ?, last_error = ?, updated_at = ? "
                    "WHERE uuid = ?",
                    (status, str(error or ""), now, uuid),
                )
            else:
                conn.execute(
                    "UPDATE transfers SET status = ?, last_error = ?, "
                    "destination = ?, updated_at = ? WHERE uuid = ?",
                    (status, str(error or ""), destination, now, uuid),
                )

    def set_manifest(
        self,
        uuid: str,
        *,
        code: str,
        url: str,
        sha256: str = "",
        manifest_json: str = "",
    ) -> None:
        now = self._now()
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE transfers
                SET manifest_code = ?, manifest_url = ?, manifest_sha256 = ?,
                    manifest_json = ?, updated_at = ?
                WHERE uuid = ?
                """,
                (code, url, sha256, manifest_json, now, uuid),
            )

    def set_extra(self, uuid: str, extra: dict) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE transfers SET extra = ?, updated_at = ? WHERE uuid = ?",
                (json.dumps(extra or {}, ensure_ascii=False), self._now(), uuid),
            )

    # -- chunk updates ----------------------------------------------------
    def mark_chunk_started(self, uuid: str, index: int) -> None:
        now = self._now()
        with self._tx() as conn:
            transfer = conn.execute(
                "SELECT direction FROM transfers WHERE uuid = ?", (uuid,),
            ).fetchone()
            if transfer is None:
                raise TransferStoreError(f"Unknown transfer: {uuid}")
            direction = str(transfer["direction"])
            active = CHUNK_UPLOADING if direction == "upload" else CHUNK_DOWNLOADING
            conn.execute(
                """
                UPDATE chunks
                SET status = ?, attempts = attempts + 1,
                    updated_at = ?, last_error = ''
                WHERE transfer_uuid = ? AND chunk_index = ?
                """,
                (active, now, uuid, int(index)),
            )
            conn.execute(
                "UPDATE transfers SET status = ?, updated_at = ? "
                "WHERE uuid = ? AND status IN (?, ?, ?, ?, ?)",
                (STATUS_RUNNING, now, uuid, STATUS_PENDING, STATUS_PAUSED,
                 STATUS_INTERRUPTED, STATUS_FAILED, STATUS_FINALIZING),
            )

    def set_chunk_status(
        self, uuid: str, index: int, status: str, error: str = "",
    ) -> None:
        now = self._now()
        with self._tx() as conn:
            conn.execute(
                "UPDATE chunks SET status = ?, last_error = ?, updated_at = ? "
                "WHERE transfer_uuid = ? AND chunk_index = ?",
                (status, str(error or ""), now, uuid, int(index)),
            )

    def mark_chunk_completed(
        self,
        uuid: str,
        index: int,
        *,
        sha256: str,
        size: int,
        remote_code: str = "",
        remote_url: str = "",
        local_path: str = "",
    ) -> bool:
        """
        Persist a verified chunk atomically.

        The size and (when known) SHA-256 must match the values recorded
        when the transfer was planned.  A mismatch raises
        :class:`ChunkVerificationError` and leaves the chunk uncompleted.
        """
        now = self._now()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM chunks WHERE transfer_uuid = ? AND chunk_index = ?",
                (uuid, int(index)),
            ).fetchone()
            if row is None:
                raise TransferStoreError(
                    f"Unknown chunk {index} for transfer {uuid}"
                )
            if row["status"] == CHUNK_COMPLETED:
                return False
            if int(size) != int(row["size"]):
                raise ChunkVerificationError(
                    f"Chunk {index} size mismatch ({size} != {row['size']})."
                )
            expected = str(row["sha256"] or "")
            actual = str(sha256 or "")
            if expected and actual.lower() != expected.lower():
                raise ChunkVerificationError(
                    f"Chunk {index} checksum mismatch."
                )
            if not actual:
                raise ChunkVerificationError(
                    f"Chunk {index} cannot be completed without a checksum."
                )

            conn.execute(
                """
                UPDATE chunks
                SET status = ?, sha256 = ?, remote_code = ?, remote_url = ?,
                    local_path = ?, last_error = '', updated_at = ?
                WHERE transfer_uuid = ? AND chunk_index = ?
                """,
                (CHUNK_COMPLETED, actual, remote_code, remote_url,
                 local_path, now, uuid, int(index)),
            )
            completed = conn.execute(
                "SELECT COUNT(*) AS n FROM chunks "
                "WHERE transfer_uuid = ? AND status = ?",
                (uuid, CHUNK_COMPLETED),
            ).fetchone()["n"]
            total = conn.execute(
                "SELECT total_chunks, status FROM transfers WHERE uuid = ?",
                (uuid,),
            ).fetchone()
            if int(completed) >= int(total["total_chunks"]):
                new_status = STATUS_FINALIZING
            elif total["status"] in (STATUS_PAUSED, STATUS_INTERRUPTED,
                                     STATUS_CANCELLED, STATUS_FAILED):
                new_status = total["status"]
            else:
                new_status = STATUS_RUNNING
            conn.execute(
                "UPDATE transfers SET completed_chunks = ?, status = ?, "
                "updated_at = ? WHERE uuid = ?",
                (int(completed), new_status, now, uuid),
            )
        return True

    def mark_chunk_failed(self, uuid: str, index: int, error: str) -> None:
        now = self._now()
        with self._tx() as conn:
            conn.execute(
                "UPDATE chunks SET status = ?, last_error = ?, updated_at = ? "
                "WHERE transfer_uuid = ? AND chunk_index = ?",
                (CHUNK_FAILED, str(error or ""), now, uuid, int(index)),
            )
            conn.execute(
                "UPDATE transfers SET last_error = ?, updated_at = ? WHERE uuid = ?",
                (str(error or ""), now, uuid),
            )

    def requeue_incomplete_chunks(self, uuid: str) -> None:
        """Reset in-flight chunks to pending after a restart/pause."""
        now = self._now()
        with self._tx() as conn:
            conn.execute(
                "UPDATE chunks SET status = ?, updated_at = ? "
                "WHERE transfer_uuid = ? AND status != ?",
                (CHUNK_PENDING, now, uuid, CHUNK_COMPLETED),
            )
            conn.execute(
                "UPDATE transfers SET status = ?, updated_at = ? WHERE uuid = ?",
                (STATUS_RUNNING, now, uuid),
            )

    def reset_chunk(self, uuid: str, index: int,
                    include_completed: bool = False) -> None:
        now = self._now()
        with self._tx() as conn:
            if include_completed:
                conn.execute(
                    "UPDATE chunks SET status = ?, last_error = '', "
                    "updated_at = ? WHERE transfer_uuid = ? AND chunk_index = ?",
                    (CHUNK_PENDING, now, uuid, int(index)),
                )
            else:
                conn.execute(
                    "UPDATE chunks SET status = ?, last_error = '', updated_at = ? "
                    "WHERE transfer_uuid = ? AND chunk_index = ? AND status != ?",
                    (CHUNK_PENDING, now, uuid, int(index), CHUNK_COMPLETED),
                )
            conn.execute(
                "UPDATE transfers SET completed_chunks = ("
                "SELECT COUNT(*) FROM chunks WHERE transfer_uuid = ? AND status = ?"
                "), updated_at = ? WHERE uuid = ?",
                (uuid, CHUNK_COMPLETED, now, uuid),
            )

    # -- maintenance ------------------------------------------------------
    def recover_stale(self, exclude_uuids: Iterable[str] = ()) -> int:
        """
        Mark transfers left in ``running`` by a dead process as interrupted.

        *exclude_uuids* should contain transfers whose cross-process lock
        is still held by a live worker.
        """
        excluded = {str(u) for u in exclude_uuids}
        now = self._now()
        changed = 0
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT uuid FROM transfers WHERE status = ?", (STATUS_RUNNING,)
            ).fetchall()
            for row in rows:
                uuid = str(row["uuid"])
                if uuid in excluded:
                    continue
                conn.execute(
                    "UPDATE transfers SET status = ?, updated_at = ? WHERE uuid = ?",
                    (STATUS_INTERRUPTED, now, uuid),
                )
                conn.execute(
                    "UPDATE chunks SET status = ?, updated_at = ? "
                    "WHERE transfer_uuid = ? AND status IN (?, ?)",
                    (CHUNK_PENDING, now, uuid, CHUNK_UPLOADING,
                     CHUNK_DOWNLOADING),
                )
                changed += 1
        if changed:
            logger.info("Recovered %d interrupted transfer(s)", changed)
        return changed

    def delete_transfer(self, uuid: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM transfers WHERE uuid = ?", (uuid,))
            conn.execute("DELETE FROM chunks WHERE transfer_uuid = ?", (uuid,))
        return cur.rowcount > 0

    def update_from_chunks(self, uuid: str) -> None:
        """Recompute the completed count (used after manual repair)."""
        now = self._now()
        with self._tx() as conn:
            completed = conn.execute(
                "SELECT COUNT(*) AS n FROM chunks "
                "WHERE transfer_uuid = ? AND status = ?",
                (uuid, CHUNK_COMPLETED),
            ).fetchone()["n"]
            conn.execute(
                "UPDATE transfers SET completed_chunks = ?, updated_at = ? "
                "WHERE uuid = ?",
                (int(completed), now, uuid),
            )


# ---------------------------------------------------------------------------
# Shared default store
# ---------------------------------------------------------------------------
_default_store: Optional[TransferStore] = None
_default_lock = threading.Lock()


def default_store() -> TransferStore:
    """Return the process-wide store backed by the default database path."""
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = TransferStore()
        return _default_store


def reset_default_store() -> None:
    """Close and forget the shared store (used by tests and shutdown)."""
    global _default_store
    with _default_lock:
        if _default_store is not None:
            try:
                _default_store.close()
            except Exception:
                pass
        _default_store = None
