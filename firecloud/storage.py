"""Local filesystem chunk storage with sharded directories and quota enforcement."""

import shutil
import threading
from pathlib import Path

from firecloud.exceptions import ChunkNotFoundError, StorageFullError


class ChunkStore:
    """Thread-safe, sharded local storage for encrypted chunks.

    Chunks are stored in a two-level directory tree sharded by the first two
    hex characters of the chunk ID::

        base_path/ab/abcdef0123456789...

    A storage quota is enforced on every ``store()`` call.  When *max_storage*
    is ``None`` the quota defaults to 80 % of the free space reported by the OS
    at construction time.
    """

    def __init__(self, base_path: Path | str, max_storage: int | None = None) -> None:
        """Initialise the chunk store.

        Args:
            base_path: Root directory for chunk storage.
            max_storage: Maximum bytes allowed.  ``None`` means 80 % of the
                available disk space at *base_path*.
        """
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        if max_storage is not None:
            self._max_storage = max_storage
        else:
            self._max_storage = int(shutil.disk_usage(self._base).free * 0.8)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, chunk_id: str, data: bytes) -> None:
        """Store an encrypted chunk on disk.

        Args:
            chunk_id: Hex string identifying the chunk.
            data: Raw (already-encrypted) chunk bytes.

        Raises:
            StorageFullError: If storing *data* would exceed the quota.
        """
        with self._lock:
            if self.used_bytes() + len(data) > self._max_storage:
                raise StorageFullError(
                    f"Storing chunk {chunk_id} ({len(data)} bytes) would exceed "
                    f"the quota of {self._max_storage} bytes"
                )
            path = self._chunk_path(chunk_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def retrieve(self, chunk_id: str) -> bytes:
        """Retrieve a stored chunk by its ID.

        Args:
            chunk_id: Hex string identifying the chunk.

        Returns:
            The raw bytes of the chunk.

        Raises:
            ChunkNotFoundError: If the chunk is not in the store.
        """
        with self._lock:
            path = self._chunk_path(chunk_id)
            if not path.is_file():
                raise ChunkNotFoundError(
                    f"Chunk {chunk_id} not found in store"
                )
            return path.read_bytes()

    def delete(self, chunk_id: str) -> None:
        """Delete a chunk from the store.

        This is a no-op if the chunk does not exist.

        Args:
            chunk_id: Hex string identifying the chunk.
        """
        with self._lock:
            path = self._chunk_path(chunk_id)
            if path.is_file():
                path.unlink()
                # Clean up empty shard directory.
                try:
                    path.parent.rmdir()
                except OSError:
                    pass  # Directory not empty — that's fine.

    def has(self, chunk_id: str) -> bool:
        """Check whether a chunk exists in the store.

        Args:
            chunk_id: Hex string identifying the chunk.

        Returns:
            ``True`` if the chunk is stored, ``False`` otherwise.
        """
        with self._lock:
            return self._chunk_path(chunk_id).is_file()

    def used_bytes(self) -> int:
        """Return the total number of bytes consumed by stored chunks."""
        total = 0
        for path in self._base.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

    def available_bytes(self) -> int:
        """Return the number of bytes remaining before the quota is hit."""
        return max(0, self._max_storage - self.used_bytes())

    def list_chunks(self) -> list[str]:
        """Return a list of all stored chunk IDs."""
        chunks: list[str] = []
        for shard_dir in sorted(self._base.iterdir()):
            if not shard_dir.is_dir():
                continue
            for chunk_file in sorted(shard_dir.iterdir()):
                if chunk_file.is_file():
                    chunks.append(chunk_file.name)
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _chunk_path(self, chunk_id: str) -> Path:
        """Return the sharded filesystem path for *chunk_id*.

        Layout: ``base_path / chunk_id[:2] / chunk_id``
        """
        return self._base / chunk_id[:2] / chunk_id
