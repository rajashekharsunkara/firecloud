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
    encrypted_size: int


@dataclass(frozen=True)
class SymbolRecord:
    record_id: int
    chunk_id: str
    node_id: str
    symbol_id: int
    symbol_path: str


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
            self._ensure_column("nodes", "endpoint", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("nodes", "kind", "TEXT NOT NULL DEFAULT 'local'")

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
        self, chunk_id: str, file_id: str, chunk_index: int, plain_size: int, encrypted_size: int
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO chunks(chunk_id, file_id, chunk_index, plain_size, encrypted_size)
                VALUES(?, ?, ?, ?, ?)
                """,
                (chunk_id, file_id, chunk_index, plain_size, encrypted_size),
            )

    def list_chunks(self, file_id: str) -> list[ChunkRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT chunk_id, file_id, chunk_index, plain_size, encrypted_size
                FROM chunks WHERE file_id = ? ORDER BY chunk_index ASC
                """,
                (file_id,),
            ).fetchall()
        return [ChunkRecord(**dict(row)) for row in rows]

    def add_symbol(self, chunk_id: str, node_id: str, symbol_id: int, symbol_path: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO symbols(chunk_id, node_id, symbol_id, symbol_path)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(chunk_id, node_id, symbol_id)
                DO UPDATE SET symbol_path=excluded.symbol_path
                """,
                (chunk_id, node_id, symbol_id, symbol_path),
            )

    def list_symbols(self, chunk_id: str) -> list[SymbolRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id AS record_id, chunk_id, node_id, symbol_id, symbol_path
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
