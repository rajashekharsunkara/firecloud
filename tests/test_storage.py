"""Tests for firecloud.storage.ChunkStore."""

import threading

import pytest

from firecloud.exceptions import ChunkNotFoundError, StorageFullError
from firecloud.storage import ChunkStore


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

CHUNK_ID_A = "abcdef0123456789abcdef0123456789"
CHUNK_ID_B = "bb00112233445566778899aabbccddee"
CHUNK_ID_C = "cc11223344556677889900aabbccddee"
CHUNK_DATA = b"encrypted-chunk-payload-" * 10  # 240 bytes


# ------------------------------------------------------------------
# Basic round-trip
# ------------------------------------------------------------------


class TestStoreRetrieve:
    """Store → retrieve round-trip tests."""

    def test_store_and_retrieve(self, storage_dir):
        store = ChunkStore(storage_dir)
        store.store(CHUNK_ID_A, CHUNK_DATA)
        assert store.retrieve(CHUNK_ID_A) == CHUNK_DATA

    def test_retrieve_missing_raises(self, storage_dir):
        store = ChunkStore(storage_dir)
        with pytest.raises(ChunkNotFoundError):
            store.retrieve("does_not_exist_00000000000000000")


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------


class TestDelete:
    """Chunk deletion tests."""

    def test_delete_existing(self, storage_dir):
        store = ChunkStore(storage_dir)
        store.store(CHUNK_ID_A, CHUNK_DATA)
        store.delete(CHUNK_ID_A)
        assert not store.has(CHUNK_ID_A)

    def test_delete_missing_is_silent(self, storage_dir):
        store = ChunkStore(storage_dir)
        # Should not raise.
        store.delete("nonexistent_000000000000000000000")


# ------------------------------------------------------------------
# has()
# ------------------------------------------------------------------


class TestHas:
    """Existence check tests."""

    def test_has_returns_true(self, storage_dir):
        store = ChunkStore(storage_dir)
        store.store(CHUNK_ID_A, CHUNK_DATA)
        assert store.has(CHUNK_ID_A) is True

    def test_has_returns_false(self, storage_dir):
        store = ChunkStore(storage_dir)
        assert store.has("missing_chunk_id_00000000000000") is False


# ------------------------------------------------------------------
# used_bytes
# ------------------------------------------------------------------


class TestUsedBytes:
    """Storage accounting tests."""

    def test_used_bytes_increases(self, storage_dir):
        store = ChunkStore(storage_dir)
        before = store.used_bytes()
        store.store(CHUNK_ID_A, CHUNK_DATA)
        after = store.used_bytes()
        assert after - before == len(CHUNK_DATA)


# ------------------------------------------------------------------
# list_chunks
# ------------------------------------------------------------------


class TestListChunks:
    """Chunk enumeration tests."""

    def test_list_all_stored(self, storage_dir):
        store = ChunkStore(storage_dir)
        store.store(CHUNK_ID_A, CHUNK_DATA)
        store.store(CHUNK_ID_B, b"other-data")
        ids = store.list_chunks()
        assert set(ids) == {CHUNK_ID_A, CHUNK_ID_B}


# ------------------------------------------------------------------
# Quota enforcement
# ------------------------------------------------------------------


class TestQuota:
    """Storage quota tests."""

    def test_store_exceeds_quota_raises(self, storage_dir):
        store = ChunkStore(storage_dir, max_storage=100)
        with pytest.raises(StorageFullError):
            store.store(CHUNK_ID_A, b"x" * 101)

    def test_store_within_quota_succeeds(self, storage_dir):
        store = ChunkStore(storage_dir, max_storage=500)
        store.store(CHUNK_ID_A, b"x" * 200)
        store.store(CHUNK_ID_B, b"y" * 200)
        assert store.has(CHUNK_ID_A)
        assert store.has(CHUNK_ID_B)

    def test_cumulative_exceeds_quota(self, storage_dir):
        store = ChunkStore(storage_dir, max_storage=300)
        store.store(CHUNK_ID_A, b"x" * 200)
        with pytest.raises(StorageFullError):
            store.store(CHUNK_ID_B, b"y" * 200)


# ------------------------------------------------------------------
# Sharded directory structure
# ------------------------------------------------------------------


class TestSharding:
    """Directory sharding tests."""

    def test_shard_directory_exists(self, storage_dir):
        store = ChunkStore(storage_dir)
        store.store(CHUNK_ID_A, CHUNK_DATA)
        shard = storage_dir / CHUNK_ID_A[:2]
        assert shard.is_dir()
        assert (shard / CHUNK_ID_A).is_file()

    def test_different_shards(self, storage_dir):
        store = ChunkStore(storage_dir)
        store.store(CHUNK_ID_A, b"aaa")
        store.store(CHUNK_ID_B, b"bbb")
        # CHUNK_ID_A starts with "ab", CHUNK_ID_B starts with "bb"
        assert (storage_dir / "ab").is_dir()
        assert (storage_dir / "bb").is_dir()


# ------------------------------------------------------------------
# Thread safety
# ------------------------------------------------------------------


class TestThreadSafety:
    """Concurrent access tests."""

    def test_concurrent_stores(self, storage_dir):
        store = ChunkStore(storage_dir, max_storage=10 * 1024 * 1024)
        errors: list[Exception] = []
        ids = [f"{i:032x}" for i in range(50)]

        def _store(chunk_id: str) -> None:
            try:
                store.store(chunk_id, b"payload-" + chunk_id.encode())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_store, args=(cid,)) for cid in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Unexpected errors: {errors}"
        stored = store.list_chunks()
        assert set(stored) == set(ids)
