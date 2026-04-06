from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileRecord:
    file_id: str
    file_name: str
    file_size: int
    created_at: str


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    file_id: str
    chunk_index: int
    plain_size: int
    compressed_size: int
    compression: str
    encrypted_size: int


@dataclass(frozen=True)
class SymbolRecord:
    record_id: int
    chunk_id: str
    node_id: str
    symbol_id: int
    symbol_path: str
    symbol_hash: str


@dataclass(frozen=True)
class DedupChunkRecord:
    chunk_hash: str
    canonical_chunk_id: str
    plain_size: int
    compressed_size: int
    compression: str
    encrypted_size: int
    ref_count: int
    gc_pending: bool
    gc_marked_at: str | None


@dataclass(frozen=True)
class DedupSymbolRecord:
    chunk_hash: str
    node_id: str
    symbol_id: int
    symbol_path: str
    symbol_hash: str


@dataclass(frozen=True)
class NodeRecord:
    node_id: str
    status: str
    endpoint: str
    kind: str
    updated_at: str

    @property
    def online(self) -> bool:
        return self.status == "online"


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    event_time: str
    event_type: str
    payload_json: str
    prev_hash: str
    event_hash: str

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)


class MetadataStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    file_id TEXT PRIMARY KEY,
                    file_name TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    plain_size INTEGER NOT NULL,
                    compressed_size INTEGER NOT NULL DEFAULT 0,
                    compression TEXT NOT NULL DEFAULT 'none',
                    encrypted_size INTEGER NOT NULL,
                    FOREIGN KEY(file_id) REFERENCES files(file_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    symbol_id INTEGER NOT NULL,
                    symbol_path TEXT NOT NULL,
                    symbol_hash TEXT NOT NULL DEFAULT '',
                    UNIQUE(chunk_id, node_id, symbol_id),
                    FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    endpoint TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'local',
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_time TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dedup_chunks (
                    chunk_hash TEXT PRIMARY KEY,
                    canonical_chunk_id TEXT NOT NULL,
                    plain_size INTEGER NOT NULL,
                    compressed_size INTEGER NOT NULL,
                    compression TEXT NOT NULL,
                    encrypted_size INTEGER NOT NULL,
                    ref_count INTEGER NOT NULL,
                    gc_pending INTEGER NOT NULL DEFAULT 0,
                    gc_marked_at TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_dedup_refs (
                    chunk_id TEXT PRIMARY KEY,
                    chunk_hash TEXT NOT NULL,
                    FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    FOREIGN KEY(chunk_hash) REFERENCES dedup_chunks(chunk_hash)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dedup_symbols (
                    chunk_hash TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    symbol_id INTEGER NOT NULL,
                    symbol_path TEXT NOT NULL,
                    symbol_hash TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(chunk_hash, node_id, symbol_id),
                    FOREIGN KEY(chunk_hash) REFERENCES dedup_chunks(chunk_hash) ON DELETE CASCADE
                )
                """
            )
            self._ensure_column("nodes", "endpoint", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("nodes", "kind", "TEXT NOT NULL DEFAULT 'local'")
            self._ensure_column("chunks", "compressed_size", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("chunks", "compression", "TEXT NOT NULL DEFAULT 'none'")
            self._ensure_column("symbols", "symbol_hash", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("dedup_symbols", "symbol_hash", "TEXT NOT NULL DEFAULT ''")

    def _ensure_column(self, table_name: str, column_name: str, definition_sql: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {row["name"] for row in rows}
        if column_name in existing_columns:
            return
        self._conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition_sql}"
        )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(tz=UTC).isoformat()

    def create_file(self, file_id: str, file_name: str, file_size: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO files(file_id, file_name, file_size, created_at) VALUES(?, ?, ?, ?)",
                (file_id, file_name, file_size, self._utc_now()),
            )

    def list_files(self) -> list[FileRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_id, file_name, file_size, created_at FROM files ORDER BY created_at DESC"
            ).fetchall()
        return [FileRecord(**dict(row)) for row in rows]

    def get_file(self, file_id: str) -> FileRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT file_id, file_name, file_size, created_at FROM files WHERE file_id = ?",
                (file_id,),
            ).fetchone()
        if row is None:
            return None
        return FileRecord(**dict(row))

    def add_chunk(
        self,
        chunk_id: str,
        file_id: str,
        chunk_index: int,
        plain_size: int,
        encrypted_size: int,
        compressed_size: int | None = None,
        compression: str = "none",
    ) -> None:
        effective_compressed_size = plain_size if compressed_size is None else compressed_size
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO chunks(
                    chunk_id, file_id, chunk_index, plain_size, compressed_size, compression, encrypted_size
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    file_id,
                    chunk_index,
                    plain_size,
                    effective_compressed_size,
                    compression,
                    encrypted_size,
                ),
            )

    def list_chunks(self, file_id: str) -> list[ChunkRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    chunk_id,
                    file_id,
                    chunk_index,
                    plain_size,
                    COALESCE(compressed_size, plain_size) AS compressed_size,
                    COALESCE(compression, 'none') AS compression,
                    encrypted_size
                FROM chunks WHERE file_id = ? ORDER BY chunk_index ASC
                """,
                (file_id,),
            ).fetchall()
        return [ChunkRecord(**dict(row)) for row in rows]

    def add_symbol(
        self,
        chunk_id: str,
        node_id: str,
        symbol_id: int,
        symbol_path: str,
        symbol_hash: str = "",
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO symbols(chunk_id, node_id, symbol_id, symbol_path, symbol_hash)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id, node_id, symbol_id)
                DO UPDATE SET symbol_path=excluded.symbol_path, symbol_hash=excluded.symbol_hash
                """,
                (chunk_id, node_id, symbol_id, symbol_path, symbol_hash),
            )

    def list_symbols(self, chunk_id: str) -> list[SymbolRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id AS record_id, chunk_id, node_id, symbol_id, symbol_path, symbol_hash
                FROM symbols WHERE chunk_id = ?
                ORDER BY symbol_id ASC, node_id ASC
                """,
                (chunk_id,),
            ).fetchall()
        return [SymbolRecord(**dict(row)) for row in rows]

    def list_chunk_symbol_counts(self, file_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT c.chunk_id AS chunk_id, COUNT(s.id) AS symbol_count
                FROM chunks c
                LEFT JOIN symbols s ON s.chunk_id = c.chunk_id
                WHERE c.file_id = ?
                GROUP BY c.chunk_id
                """,
                (file_id,),
            ).fetchall()
        return {row["chunk_id"]: row["symbol_count"] for row in rows}

    def copy_symbols(self, source_chunk_id: str, target_chunk_id: str) -> None:
        with self._lock, self._conn:
            rows = self._conn.execute(
                """
                SELECT node_id, symbol_id, symbol_path, symbol_hash
                FROM symbols
                WHERE chunk_id = ?
                ORDER BY symbol_id ASC, node_id ASC
                """,
                (source_chunk_id,),
            ).fetchall()
            for row in rows:
                self._conn.execute(
                    """
                    INSERT INTO symbols(chunk_id, node_id, symbol_id, symbol_path, symbol_hash)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id, node_id, symbol_id)
                    DO UPDATE SET symbol_path=excluded.symbol_path, symbol_hash=excluded.symbol_hash
                    """,
                    (
                        target_chunk_id,
                        row["node_id"],
                        row["symbol_id"],
                        row["symbol_path"],
                        row["symbol_hash"],
                    ),
                )

    def upsert_dedup_symbol(
        self,
        chunk_hash: str,
        node_id: str,
        symbol_id: int,
        symbol_path: str,
        symbol_hash: str = "",
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO dedup_symbols(chunk_hash, node_id, symbol_id, symbol_path, symbol_hash)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(chunk_hash, node_id, symbol_id)
                DO UPDATE SET symbol_path=excluded.symbol_path, symbol_hash=excluded.symbol_hash
                """,
                (chunk_hash, node_id, symbol_id, symbol_path, symbol_hash),
            )

    def list_dedup_symbols(self, chunk_hash: str) -> list[DedupSymbolRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT chunk_hash, node_id, symbol_id, symbol_path, symbol_hash
                FROM dedup_symbols
                WHERE chunk_hash = ?
                ORDER BY symbol_id ASC, node_id ASC
                """,
                (chunk_hash,),
            ).fetchall()
        return [DedupSymbolRecord(**dict(row)) for row in rows]

    def commit_upload(
        self,
        *,
        file_id: str,
        file_name: str,
        file_size: int,
        chunks: list[dict[str, Any]],
        chunk_refs: list[tuple[str, str]],
        copied_symbol_chunks: list[tuple[str, str]],
        symbols: list[tuple[str, str, int, str, str]],
        dedup_chunks: list[dict[str, Any]],
        dedup_symbols: list[tuple[str, str, int, str, str]],
        dedup_increment_counts: dict[str, int],
        canonical_updates: dict[str, str],
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO files(file_id, file_name, file_size, created_at) VALUES(?, ?, ?, ?)",
                (file_id, file_name, file_size, self._utc_now()),
            )
            for chunk in chunks:
                self._conn.execute(
                    """
                    INSERT INTO chunks(
                        chunk_id, file_id, chunk_index, plain_size, compressed_size, compression, encrypted_size
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["chunk_id"],
                        chunk["file_id"],
                        chunk["chunk_index"],
                        chunk["plain_size"],
                        chunk["compressed_size"],
                        chunk["compression"],
                        chunk["encrypted_size"],
                    ),
                )
            for dedup in dedup_chunks:
                self._conn.execute(
                    """
                    INSERT INTO dedup_chunks(
                        chunk_hash,
                        canonical_chunk_id,
                        plain_size,
                        compressed_size,
                        compression,
                        encrypted_size,
                        ref_count,
                        gc_pending,
                        gc_marked_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, 0, NULL)
                    """,
                    (
                        dedup["chunk_hash"],
                        dedup["canonical_chunk_id"],
                        dedup["plain_size"],
                        dedup["compressed_size"],
                        dedup["compression"],
                        dedup["encrypted_size"],
                        dedup["ref_count"],
                    ),
                )
            for chunk_id, chunk_hash in chunk_refs:
                self._conn.execute(
                    """
                    INSERT INTO chunk_dedup_refs(chunk_id, chunk_hash)
                    VALUES(?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET chunk_hash=excluded.chunk_hash
                    """,
                    (chunk_id, chunk_hash),
                )
            for source_chunk_id, target_chunk_id in copied_symbol_chunks:
                rows = self._conn.execute(
                    """
                    SELECT node_id, symbol_id, symbol_path, symbol_hash
                    FROM symbols
                    WHERE chunk_id = ?
                    ORDER BY symbol_id ASC, node_id ASC
                    """,
                    (source_chunk_id,),
                ).fetchall()
                for row in rows:
                    self._conn.execute(
                        """
                        INSERT INTO symbols(chunk_id, node_id, symbol_id, symbol_path, symbol_hash)
                        VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(chunk_id, node_id, symbol_id)
                        DO UPDATE SET symbol_path=excluded.symbol_path, symbol_hash=excluded.symbol_hash
                        """,
                        (
                            target_chunk_id,
                            row["node_id"],
                            row["symbol_id"],
                            row["symbol_path"],
                            row["symbol_hash"],
                        ),
                    )
            for chunk_id, node_id, symbol_id, symbol_path, symbol_hash in symbols:
                self._conn.execute(
                    """
                    INSERT INTO symbols(chunk_id, node_id, symbol_id, symbol_path, symbol_hash)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id, node_id, symbol_id)
                    DO UPDATE SET symbol_path=excluded.symbol_path, symbol_hash=excluded.symbol_hash
                    """,
                    (chunk_id, node_id, symbol_id, symbol_path, symbol_hash),
                )
            for chunk_hash, node_id, symbol_id, symbol_path, symbol_hash in dedup_symbols:
                self._conn.execute(
                    """
                    INSERT INTO dedup_symbols(chunk_hash, node_id, symbol_id, symbol_path, symbol_hash)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_hash, node_id, symbol_id)
                    DO UPDATE SET symbol_path=excluded.symbol_path, symbol_hash=excluded.symbol_hash
                    """,
                    (chunk_hash, node_id, symbol_id, symbol_path, symbol_hash),
                )
            for chunk_hash, increment in dedup_increment_counts.items():
                cursor = self._conn.execute(
                    """
                    UPDATE dedup_chunks
                    SET ref_count = ref_count + ?, gc_pending = 0, gc_marked_at = NULL
                    WHERE chunk_hash = ?
                    """,
                    (increment, chunk_hash),
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"Unknown chunk_hash: {chunk_hash}")
            for chunk_hash, canonical_chunk_id in canonical_updates.items():
                cursor = self._conn.execute(
                    """
                    UPDATE dedup_chunks
                    SET canonical_chunk_id = ?
                    WHERE chunk_hash = ?
                    """,
                    (canonical_chunk_id, chunk_hash),
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"Unknown chunk_hash: {chunk_hash}")

    def delete_dedup_symbols(self, chunk_hash: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM dedup_symbols WHERE chunk_hash = ?", (chunk_hash,))

    def delete_symbols(self, chunk_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM symbols WHERE chunk_id = ?", (chunk_id,))

    def delete_chunk(self, chunk_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunks WHERE chunk_id = ?", (chunk_id,))

    def delete_file(self, file_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM files WHERE file_id = ?", (file_id,))

    def add_chunk_dedup_ref(self, chunk_id: str, chunk_hash: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO chunk_dedup_refs(chunk_id, chunk_hash)
                VALUES(?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET chunk_hash=excluded.chunk_hash
                """,
                (chunk_id, chunk_hash),
            )

    def get_chunk_hash(self, chunk_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT chunk_hash FROM chunk_dedup_refs WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        if row is None:
            return None
        return row["chunk_hash"]

    def remove_chunk_dedup_ref(self, chunk_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunk_dedup_refs WHERE chunk_id = ?", (chunk_id,))

    def find_chunk_for_hash(self, chunk_hash: str, exclude_chunk_id: str | None = None) -> str | None:
        with self._lock:
            if exclude_chunk_id is None:
                row = self._conn.execute(
                    """
                    SELECT chunk_id
                    FROM chunk_dedup_refs
                    WHERE chunk_hash = ?
                    ORDER BY chunk_id ASC
                    LIMIT 1
                    """,
                    (chunk_hash,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """
                    SELECT chunk_id
                    FROM chunk_dedup_refs
                    WHERE chunk_hash = ? AND chunk_id != ?
                    ORDER BY chunk_id ASC
                    LIMIT 1
                    """,
                    (chunk_hash, exclude_chunk_id),
                ).fetchone()
        if row is None:
            return None
        return row["chunk_id"]

    def get_dedup_chunk(self, chunk_hash: str) -> DedupChunkRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    chunk_hash,
                    canonical_chunk_id,
                    plain_size,
                    compressed_size,
                    compression,
                    encrypted_size,
                    ref_count,
                    gc_pending,
                    gc_marked_at
                FROM dedup_chunks
                WHERE chunk_hash = ?
                """,
                (chunk_hash,),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["gc_pending"] = bool(payload["gc_pending"])
        return DedupChunkRecord(**payload)

    def list_dedup_chunks(self) -> list[DedupChunkRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    chunk_hash,
                    canonical_chunk_id,
                    plain_size,
                    compressed_size,
                    compression,
                    encrypted_size,
                    ref_count,
                    gc_pending,
                    gc_marked_at
                FROM dedup_chunks
                ORDER BY chunk_hash ASC
                """
            ).fetchall()
        records: list[DedupChunkRecord] = []
        for row in rows:
            payload = dict(row)
            payload["gc_pending"] = bool(payload["gc_pending"])
            records.append(DedupChunkRecord(**payload))
        return records

    def list_gc_pending_dedup_chunks(
        self,
        due_before: str | None = None,
        limit: int = 1000,
    ) -> list[DedupChunkRecord]:
        with self._lock:
            if due_before is None:
                rows = self._conn.execute(
                    """
                    SELECT
                        chunk_hash,
                        canonical_chunk_id,
                        plain_size,
                        compressed_size,
                        compression,
                        encrypted_size,
                        ref_count,
                        gc_pending,
                        gc_marked_at
                    FROM dedup_chunks
                    WHERE gc_pending = 1
                    ORDER BY gc_marked_at ASC, chunk_hash ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT
                        chunk_hash,
                        canonical_chunk_id,
                        plain_size,
                        compressed_size,
                        compression,
                        encrypted_size,
                        ref_count,
                        gc_pending,
                        gc_marked_at
                    FROM dedup_chunks
                    WHERE gc_pending = 1 AND gc_marked_at IS NOT NULL AND gc_marked_at <= ?
                    ORDER BY gc_marked_at ASC, chunk_hash ASC
                    LIMIT ?
                    """,
                    (due_before, limit),
                ).fetchall()
        records: list[DedupChunkRecord] = []
        for row in rows:
            payload = dict(row)
            payload["gc_pending"] = bool(payload["gc_pending"])
            records.append(DedupChunkRecord(**payload))
        return records

    def create_dedup_chunk(
        self,
        chunk_hash: str,
        canonical_chunk_id: str,
        plain_size: int,
        compressed_size: int,
        compression: str,
        encrypted_size: int,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO dedup_chunks(
                    chunk_hash,
                    canonical_chunk_id,
                    plain_size,
                    compressed_size,
                    compression,
                    encrypted_size,
                    ref_count,
                    gc_pending,
                    gc_marked_at
                )
                VALUES(?, ?, ?, ?, ?, ?, 1, 0, NULL)
                """,
                (
                    chunk_hash,
                    canonical_chunk_id,
                    plain_size,
                    compressed_size,
                    compression,
                    encrypted_size,
                ),
            )

    def increment_dedup_ref_count(self, chunk_hash: str) -> None:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE dedup_chunks
                SET ref_count = ref_count + 1, gc_pending = 0, gc_marked_at = NULL
                WHERE chunk_hash = ?
                """,
                (chunk_hash,),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Unknown chunk_hash: {chunk_hash}")

    def decrement_dedup_ref_count(self, chunk_hash: str) -> DedupChunkRecord:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT ref_count FROM dedup_chunks WHERE chunk_hash = ?",
                (chunk_hash,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown chunk_hash: {chunk_hash}")
            new_ref_count = max(0, int(row["ref_count"]) - 1)
            gc_pending = 1 if new_ref_count == 0 else 0
            gc_marked_at = self._utc_now() if new_ref_count == 0 else None
            self._conn.execute(
                """
                UPDATE dedup_chunks
                SET ref_count = ?, gc_pending = ?, gc_marked_at = ?
                WHERE chunk_hash = ?
                """,
                (new_ref_count, gc_pending, gc_marked_at, chunk_hash),
            )
        record = self.get_dedup_chunk(chunk_hash)
        if record is None:
            raise ValueError(f"Unknown chunk_hash: {chunk_hash}")
        return record

    def set_dedup_canonical_chunk(self, chunk_hash: str, canonical_chunk_id: str) -> None:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE dedup_chunks
                SET canonical_chunk_id = ?
                WHERE chunk_hash = ?
                """,
                (canonical_chunk_id, chunk_hash),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Unknown chunk_hash: {chunk_hash}")

    def delete_dedup_chunk(self, chunk_hash: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM dedup_chunks WHERE chunk_hash = ?", (chunk_hash,))

    def cleanup_chunk_refs_for_hash(self, chunk_hash: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunk_dedup_refs WHERE chunk_hash = ?", (chunk_hash,))

    def upsert_node(self, node_id: str, status: str, endpoint: str, kind: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO nodes(node_id, status, endpoint, kind, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    status=excluded.status,
                    endpoint=excluded.endpoint,
                    kind=excluded.kind,
                    updated_at=excluded.updated_at
                """,
                (node_id, status, endpoint, kind, self._utc_now()),
            )

    def remove_node(self, node_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))

    def get_node(self, node_id: str) -> NodeRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT node_id, status, endpoint, kind, updated_at FROM nodes WHERE node_id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        return NodeRecord(**dict(row))

    def set_node_status(self, node_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE nodes SET status = ?, updated_at = ? WHERE node_id = ?",
                (status, self._utc_now(), node_id),
            )

    def list_nodes(self) -> list[NodeRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT node_id, status, endpoint, kind, updated_at FROM nodes ORDER BY node_id ASC"
            ).fetchall()
        return [NodeRecord(**dict(row)) for row in rows]

    def latest_event_hash(self) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return "GENESIS"
        return row["event_hash"]

    def append_audit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        prev_hash: str,
        event_hash: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO audit_events(event_time, event_type, payload_json, prev_hash, event_hash)
                VALUES(?, ?, ?, ?, ?)
                """,
                (self._utc_now(), event_type, json.dumps(payload, sort_keys=True), prev_hash, event_hash),
            )

    def list_audit_events(self, limit: int = 200) -> list[AuditEvent]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT sequence, event_time, event_type, payload_json, prev_hash, event_hash
                FROM audit_events
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [AuditEvent(**dict(row)) for row in rows]

    def list_audit_events_ascending(self) -> list[AuditEvent]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT sequence, event_time, event_type, payload_json, prev_hash, event_hash
                FROM audit_events
                ORDER BY sequence ASC
                """
            ).fetchall()
        return [AuditEvent(**dict(row)) for row in rows]
