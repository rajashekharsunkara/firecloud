"""Local filesystem chunk storage with sharded directories and quota enforcement."""

import os
import shutil
import threading
from pathlib import Path

from firecloud.exceptions import ChunkNotFoundError, StorageFullError

# Suffix for in-progress writes; never counted toward usage or listings.
_TMP_SUFFIX = ".tmp"


class ChunkStore:
    """Thread-safe on-disk chunk storage, sharded by the first two hex chars
    of the chunk ID (base_path/ab/abcdef...).

    A quota is enforced on every store(). max_storage=None means 80% of the
    free disk space at construction time.
    """

    def __init__(self, base_path: Path | str, max_storage: int | None = None) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        if max_storage is not None:
            self._max_storage = max_storage
        else:
            self._max_storage = int(shutil.disk_usage(self._base).free * 0.8)

        # Track usage incrementally; walking the tree on every store()
        # would make ingest quadratic. Interrupted writes leave .tmp files,
        # cleaned up here.
        self._used = 0
        for path in self._base.rglob("*"):
            if not path.is_file():
                continue
            if path.name.endswith(_TMP_SUFFIX):
                path.unlink(missing_ok=True)
            else:
                self._used += path.stat().st_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, chunk_id: str, data: bytes) -> None:
        """Write a chunk; StorageFullError if it would blow the quota."""
        with self._lock:
            path = self._chunk_path(chunk_id)
            existing = path.stat().st_size if path.is_file() else 0
            if self._used - existing + len(data) > self._max_storage:
                raise StorageFullError(
                    f"Storing chunk {chunk_id} ({len(data)} bytes) would exceed "
                    f"the quota of {self._max_storage} bytes"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename: a crash can't leave a truncated chunk
            # sitting under its content address.
            tmp_path = path.with_name(path.name + _TMP_SUFFIX)
            tmp_path.write_bytes(data)
            os.replace(tmp_path, path)
            self._used += len(data) - existing

    def retrieve(self, chunk_id: str) -> bytes:
        """Read a chunk back; ChunkNotFoundError if it isn't here."""
        with self._lock:
            path = self._chunk_path(chunk_id)
            if not path.is_file():
                raise ChunkNotFoundError(
                    f"Chunk {chunk_id} not found in store"
                )
            return path.read_bytes()

    def delete(self, chunk_id: str) -> None:
        """Remove a chunk; silently does nothing if it doesn't exist."""
        with self._lock:
            path = self._chunk_path(chunk_id)
            if path.is_file():
                size = path.stat().st_size
                path.unlink()
                self._used = max(0, self._used - size)
                try:
                    path.parent.rmdir()
                except OSError:
                    pass  # shard dir not empty

    def has(self, chunk_id: str) -> bool:
        with self._lock:
            return self._chunk_path(chunk_id).is_file()

    def used_bytes(self) -> int:
        return self._used

    def available_bytes(self) -> int:
        """Bytes left before the quota is hit."""
        return max(0, self._max_storage - self.used_bytes())

    def list_chunks(self) -> list[str]:
        """IDs of every stored chunk."""
        chunks: list[str] = []
        for shard_dir in sorted(self._base.iterdir()):
            if not shard_dir.is_dir():
                continue
            for chunk_file in sorted(shard_dir.iterdir()):
                if chunk_file.is_file() and not chunk_file.name.endswith(_TMP_SUFFIX):
                    chunks.append(chunk_file.name)
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _chunk_path(self, chunk_id: str) -> Path:
        return self._base / chunk_id[:2] / chunk_id
