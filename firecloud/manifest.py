"""JSON-backed file manifest with Lamport timestamps and tombstone support."""

import json
import threading
from dataclasses import dataclass, field, fields, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from firecloud.exceptions import FileNotFoundError as ManifestFileNotFoundError


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------


@dataclass
class ChunkInfo:
    """Metadata for a single chunk belonging to a file."""

    chunk_id: str
    integrity_hash: str
    index: int
    size: int
    stored_on: list[str] = field(default_factory=list)


@dataclass
class FileEntry:
    """Metadata for a file tracked by the manifest."""

    file_id: str
    name: str
    size: int
    chunk_count: int
    uploaded_at: str  # ISO 8601
    uploaded_by: str  # node ID
    lamport_ts: int = 0
    chunks: list[ChunkInfo] = field(default_factory=list)
    fec_enabled: bool = False
    replication_factor: int = 1
    deleted: bool = False  # tombstone flag
    deleted_at: str | None = None  # ISO 8601 when tombstoned


_CHUNK_INFO_FIELDS = {f.name for f in fields(ChunkInfo)}
_FILE_ENTRY_FIELDS = {f.name for f in fields(FileEntry)}


def entry_from_dict(raw: dict) -> FileEntry:
    """Build a FileEntry from a dict, ignoring unknown keys.

    Used for disk loads and remote manifest sync, so a newer node adding
    fields can't break older nodes.
    """
    data = dict(raw)
    chunks = [
        ChunkInfo(**{k: v for k, v in ci.items() if k in _CHUNK_INFO_FIELDS})
        for ci in data.pop("chunks", [])
    ]
    kwargs = {k: v for k, v in data.items() if k in _FILE_ENTRY_FIELDS}
    return FileEntry(**kwargs, chunks=chunks)


# ------------------------------------------------------------------
# Manifest
# ------------------------------------------------------------------


class Manifest:
    """Thread-safe file manifest persisted as manifest.json.

    Every mutation bumps a Lamport clock so nodes can merge manifests
    last-writer-wins.
    """

    def __init__(self, storage_path: Path | str) -> None:
        self._storage_path = Path(storage_path)
        self._storage_path.mkdir(parents=True, exist_ok=True)
        self._manifest_file = self._storage_path / "manifest.json"
        self._lock = threading.Lock()
        self._clock: int = 0
        self._entries: dict[str, FileEntry] = {}
        self.load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_file(self, entry: FileEntry) -> None:
        """Insert or update an entry, stamping it with the next clock tick."""
        with self._lock:
            self._clock += 1
            entry.lamport_ts = self._clock
            self._entries[entry.file_id] = entry
            self._save_unlocked()

    def get_file(self, file_id: str) -> FileEntry:
        """Look up an entry; raises FileNotFoundError if absent or deleted."""
        with self._lock:
            entry = self._entries.get(file_id)
            if entry is None or entry.deleted:
                raise ManifestFileNotFoundError(
                    f"File {file_id} not found in manifest"
                )
            return entry

    def delete_file(self, file_id: str) -> None:
        """Tombstone an entry (kept for sync; gc_tombstones prunes later)."""
        with self._lock:
            entry = self._entries.get(file_id)
            if entry is None:
                raise ManifestFileNotFoundError(
                    f"File {file_id} not found in manifest"
                )
            self._clock += 1
            entry.lamport_ts = self._clock
            entry.deleted = True
            entry.deleted_at = datetime.now(timezone.utc).isoformat()
            self._save_unlocked()

    def list_files(self, include_deleted: bool = False) -> list[FileEntry]:
        """Entries in the manifest, skipping tombstones unless asked."""
        with self._lock:
            if include_deleted:
                return list(self._entries.values())
            return [e for e in self._entries.values() if not e.deleted]

    @staticmethod
    def _remote_wins(remote: FileEntry, local: FileEntry) -> bool:
        """Deterministic last-writer-wins ordering for concurrent entries.

        Primary order is the Lamport timestamp.  Equal timestamps from
        different nodes are broken by preferring tombstones, then by
        uploader node ID, so every node converges on the same winner
        instead of each keeping its own version forever.
        """
        if remote.lamport_ts != local.lamport_ts:
            return remote.lamport_ts > local.lamport_ts
        if remote.deleted != local.deleted:
            return remote.deleted
        return remote.uploaded_by > local.uploaded_by

    def merge(self, remote_entries: list[FileEntry]) -> None:
        """Merge remote entries last-writer-wins.

        New file IDs are always accepted; the local clock advances to the
        highest timestamp seen.
        """
        with self._lock:
            for remote in remote_entries:
                local = self._entries.get(remote.file_id)
                if local is None or self._remote_wins(remote, local):
                    self._entries[remote.file_id] = remote
                # Advance clock to at least the remote timestamp.
                if remote.lamport_ts > self._clock:
                    self._clock = remote.lamport_ts
            self._save_unlocked()

    def increment_clock(self) -> int:
        """Bump and return the Lamport clock."""
        with self._lock:
            self._clock += 1
            return self._clock

    def gc_tombstones(self, max_age_days: int = 30) -> int:
        """Drop tombstones older than *max_age_days*; returns the count."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        removed = 0
        with self._lock:
            to_remove: list[str] = []
            for file_id, entry in self._entries.items():
                if entry.deleted and entry.deleted_at is not None:
                    deleted_dt = datetime.fromisoformat(entry.deleted_at)
                    if deleted_dt < cutoff:
                        to_remove.append(file_id)
            for file_id in to_remove:
                del self._entries[file_id]
                removed += 1
            if removed:
                self._save_unlocked()
        return removed

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Whole manifest as a plain dict."""
        with self._lock:
            return self._to_dict_unlocked()

    def _to_dict_unlocked(self) -> dict:
        return {
            "clock": self._clock,
            "entries": {
                fid: asdict(entry)
                for fid, entry in self._entries.items()
            },
        }

    def to_entries(self) -> list[FileEntry]:
        """Return all entries (including tombstones) for sync."""
        with self._lock:
            return list(self._entries.values())

    def save(self) -> None:
        """Persist the manifest to disk."""
        with self._lock:
            self._save_unlocked()

    def load(self) -> None:
        """Load from disk; missing file means an empty manifest."""
        with self._lock:
            if not self._manifest_file.is_file():
                self._clock = 0
                self._entries = {}
                return
            raw = json.loads(self._manifest_file.read_text(encoding="utf-8"))
            self._clock = raw.get("clock", 0)
            self._entries = {}
            for fid, edict in raw.get("entries", {}).items():
                self._entries[fid] = entry_from_dict(edict)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _save_unlocked(self) -> None:
        # Temp file + rename: a crash mid-write can't truncate the manifest.
        tmp_file = self._manifest_file.with_name(self._manifest_file.name + ".tmp")
        tmp_file.write_text(
            json.dumps(self._to_dict_unlocked(), indent=2, default=str),
            encoding="utf-8",
        )
        tmp_file.replace(self._manifest_file)
