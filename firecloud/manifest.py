"""JSON-backed file manifest with Lamport timestamps and tombstone support."""

import json
import threading
from dataclasses import dataclass, field, asdict
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


# ------------------------------------------------------------------
# Manifest
# ------------------------------------------------------------------


class Manifest:
    """Thread-safe, JSON-backed file manifest with Lamport clock.

    The manifest is persisted as a JSON file at
    ``{storage_path}/manifest.json``.  Every mutation increments a Lamport
    clock so that distributed nodes can merge their manifests using a
    last-writer-wins strategy.
    """

    def __init__(self, storage_path: Path | str) -> None:
        """Initialise the manifest.

        Args:
            storage_path: Directory that will contain ``manifest.json``.
        """
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
        """Add or update a file entry.

        The Lamport clock is incremented and the new timestamp is written
        onto *entry* before it is stored.

        Args:
            entry: The :class:`FileEntry` to insert or update.
        """
        with self._lock:
            self._clock += 1
            entry.lamport_ts = self._clock
            self._entries[entry.file_id] = entry
            self._save_unlocked()

    def get_file(self, file_id: str) -> FileEntry:
        """Retrieve a file entry by its ID.

        Args:
            file_id: Unique identifier of the file.

        Returns:
            The corresponding :class:`FileEntry`.

        Raises:
            firecloud.exceptions.FileNotFoundError: If the file is absent
                or has been tombstoned.
        """
        with self._lock:
            entry = self._entries.get(file_id)
            if entry is None or entry.deleted:
                raise ManifestFileNotFoundError(
                    f"File {file_id} not found in manifest"
                )
            return entry

    def delete_file(self, file_id: str) -> None:
        """Tombstone a file entry.

        The Lamport clock is incremented and the entry is marked as deleted
        with the current UTC time.

        Args:
            file_id: Unique identifier of the file.

        Raises:
            firecloud.exceptions.FileNotFoundError: If the file does not
                exist in the manifest.
        """
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
        """Return file entries tracked by the manifest.

        Args:
            include_deleted: When ``True``, tombstoned entries are included.

        Returns:
            A list of :class:`FileEntry` instances.
        """
        with self._lock:
            if include_deleted:
                return list(self._entries.values())
            return [e for e in self._entries.values() if not e.deleted]

    def merge(self, remote_entries: list[FileEntry]) -> None:
        """Merge remote manifest entries using last-writer-wins.

        For each remote entry the local entry is replaced only when the
        remote Lamport timestamp is strictly greater.  New file IDs are
        always accepted.  The local clock is advanced to the maximum of the
        local clock and the highest remote timestamp.

        Args:
            remote_entries: Entries received from a remote node.
        """
        with self._lock:
            for remote in remote_entries:
                local = self._entries.get(remote.file_id)
                if local is None or remote.lamport_ts > local.lamport_ts:
                    self._entries[remote.file_id] = remote
                # Advance clock to at least the remote timestamp.
                if remote.lamport_ts > self._clock:
                    self._clock = remote.lamport_ts
            self._save_unlocked()

    def increment_clock(self) -> int:
        """Increment and return the Lamport clock.

        Returns:
            The new clock value.
        """
        with self._lock:
            self._clock += 1
            return self._clock

    def gc_tombstones(self, max_age_days: int = 30) -> int:
        """Remove tombstoned entries older than *max_age_days*.

        Args:
            max_age_days: Number of days after which a tombstone is eligible
                for garbage collection.

        Returns:
            The number of entries removed.
        """
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
        """Serialise the entire manifest to a plain ``dict``."""
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
        """Load the manifest from disk.

        If the manifest file does not exist an empty manifest is created.
        """
        with self._lock:
            if not self._manifest_file.is_file():
                self._clock = 0
                self._entries = {}
                return
            raw = json.loads(self._manifest_file.read_text(encoding="utf-8"))
            self._clock = raw.get("clock", 0)
            self._entries = {}
            for fid, edict in raw.get("entries", {}).items():
                # Reconstruct ChunkInfo objects.
                chunks = [
                    ChunkInfo(**ci) for ci in edict.pop("chunks", [])
                ]
                self._entries[fid] = FileEntry(**edict, chunks=chunks)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _save_unlocked(self) -> None:
        """Write manifest JSON to disk (caller must hold ``_lock``)."""
        self._manifest_file.write_text(
            json.dumps(self.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
