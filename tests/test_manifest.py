"""Tests for firecloud.manifest.Manifest."""

import threading
from datetime import datetime, timezone, timedelta

import pytest

from firecloud.exceptions import FileNotFoundError as ManifestFileNotFoundError
from firecloud.manifest import ChunkInfo, FileEntry, Manifest


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_entry(
    file_id: str = "file-001",
    name: str = "report.pdf",
    size: int = 4096,
    chunk_count: int = 2,
    uploaded_by: str = "node-A",
    lamport_ts: int = 0,
    deleted: bool = False,
    deleted_at: str | None = None,
) -> FileEntry:
    """Create a minimal FileEntry for testing."""
    return FileEntry(
        file_id=file_id,
        name=name,
        size=size,
        chunk_count=chunk_count,
        uploaded_at=datetime.now(timezone.utc).isoformat(),
        uploaded_by=uploaded_by,
        lamport_ts=lamport_ts,
        chunks=[
            ChunkInfo(
                chunk_id=f"{file_id}-chunk-{i}",
                integrity_hash=f"sha256-{file_id}-{i}",
                index=i,
                size=size // chunk_count,
                stored_on=[uploaded_by],
            )
            for i in range(chunk_count)
        ],
    )


# ------------------------------------------------------------------
# add_file / get_file round-trip
# ------------------------------------------------------------------


class TestAddGet:
    """Basic add/get round-trip."""

    def test_add_and_get(self, tmp_dir):
        m = Manifest(tmp_dir)
        entry = _make_entry()
        m.add_file(entry)
        result = m.get_file("file-001")
        assert result.file_id == "file-001"
        assert result.name == "report.pdf"
        assert result.size == 4096
        assert len(result.chunks) == 2

    def test_get_missing_raises(self, tmp_dir):
        m = Manifest(tmp_dir)
        with pytest.raises(ManifestFileNotFoundError):
            m.get_file("no-such-file")


# ------------------------------------------------------------------
# Tombstoning (delete_file)
# ------------------------------------------------------------------


class TestTombstone:
    """Tombstone behaviour."""

    def test_delete_creates_tombstone(self, tmp_dir):
        m = Manifest(tmp_dir)
        m.add_file(_make_entry())
        m.delete_file("file-001")
        # Entry is tombstoned — listing with include_deleted shows it.
        all_entries = m.list_files(include_deleted=True)
        assert any(e.file_id == "file-001" and e.deleted for e in all_entries)

    def test_get_tombstoned_raises(self, tmp_dir):
        m = Manifest(tmp_dir)
        m.add_file(_make_entry())
        m.delete_file("file-001")
        with pytest.raises(ManifestFileNotFoundError):
            m.get_file("file-001")


# ------------------------------------------------------------------
# list_files
# ------------------------------------------------------------------


class TestListFiles:
    """Listing behaviour with and without tombstones."""

    def test_excludes_tombstoned(self, tmp_dir):
        m = Manifest(tmp_dir)
        m.add_file(_make_entry(file_id="f1"))
        m.add_file(_make_entry(file_id="f2"))
        m.delete_file("f1")
        live = m.list_files()
        assert [e.file_id for e in live] == ["f2"]

    def test_includes_tombstoned(self, tmp_dir):
        m = Manifest(tmp_dir)
        m.add_file(_make_entry(file_id="f1"))
        m.add_file(_make_entry(file_id="f2"))
        m.delete_file("f1")
        all_ = m.list_files(include_deleted=True)
        ids = {e.file_id for e in all_}
        assert ids == {"f1", "f2"}


# ------------------------------------------------------------------
# Lamport clock
# ------------------------------------------------------------------


class TestLamportClock:
    """Clock increment semantics."""

    def test_clock_increments_on_add(self, tmp_dir):
        m = Manifest(tmp_dir)
        m.add_file(_make_entry(file_id="a"))
        m.add_file(_make_entry(file_id="b"))
        m.add_file(_make_entry(file_id="c"))
        # Each add bumps the clock by 1; clock starts at 0.
        assert m._clock == 3

    def test_clock_increments_on_delete(self, tmp_dir):
        m = Manifest(tmp_dir)
        m.add_file(_make_entry(file_id="a"))  # clock → 1
        m.delete_file("a")  # clock → 2
        assert m._clock == 2

    def test_increment_clock_standalone(self, tmp_dir):
        m = Manifest(tmp_dir)
        val = m.increment_clock()
        assert val == 1
        val = m.increment_clock()
        assert val == 2


# ------------------------------------------------------------------
# Merge
# ------------------------------------------------------------------


class TestMerge:
    """CRDT-style merge (last-writer-wins by Lamport timestamp)."""

    def test_remote_higher_ts_wins(self, tmp_dir):
        m = Manifest(tmp_dir)
        local = _make_entry(file_id="f1", name="old.txt")
        local.lamport_ts = 5
        m._entries["f1"] = local
        m._clock = 5

        remote = _make_entry(file_id="f1", name="new.txt")
        remote.lamport_ts = 10

        m.merge([remote])
        assert m.get_file("f1").name == "new.txt"

    def test_local_higher_ts_preserved(self, tmp_dir):
        m = Manifest(tmp_dir)
        local = _make_entry(file_id="f1", name="local.txt")
        local.lamport_ts = 10
        m._entries["f1"] = local
        m._clock = 10

        remote = _make_entry(file_id="f1", name="remote.txt")
        remote.lamport_ts = 5

        m.merge([remote])
        assert m.get_file("f1").name == "local.txt"

    def test_new_file_from_remote(self, tmp_dir):
        m = Manifest(tmp_dir)
        remote = _make_entry(file_id="brand-new")
        remote.lamport_ts = 42

        m.merge([remote])
        assert m.get_file("brand-new").file_id == "brand-new"
        # Clock must advance to at least 42.
        assert m._clock >= 42


# ------------------------------------------------------------------
# Garbage collection
# ------------------------------------------------------------------


class TestGCTombstones:
    """Tombstone garbage collection."""

    def test_removes_old_tombstones(self, tmp_dir):
        m = Manifest(tmp_dir)
        m.add_file(_make_entry(file_id="old"))
        m.delete_file("old")

        # Backdate the tombstone by 60 days.
        entry = m._entries["old"]
        old_time = datetime.now(timezone.utc) - timedelta(days=60)
        entry.deleted_at = old_time.isoformat()
        m.save()

        removed = m.gc_tombstones(max_age_days=30)
        assert removed == 1
        assert "old" not in m._entries

    def test_keeps_recent_tombstones(self, tmp_dir):
        m = Manifest(tmp_dir)
        m.add_file(_make_entry(file_id="recent"))
        m.delete_file("recent")

        removed = m.gc_tombstones(max_age_days=30)
        assert removed == 0
        assert "recent" in m._entries


# ------------------------------------------------------------------
# Save / load round-trip
# ------------------------------------------------------------------


class TestPersistence:
    """Disk persistence tests."""

    def test_save_and_reload(self, tmp_dir):
        m = Manifest(tmp_dir)
        m.add_file(_make_entry(file_id="persist"))
        m.add_file(_make_entry(file_id="persist2", name="data.bin"))

        # Create a fresh Manifest reading the same file.
        m2 = Manifest(tmp_dir)
        assert m2.get_file("persist").file_id == "persist"
        assert m2.get_file("persist2").name == "data.bin"
        assert m2._clock == m._clock

    def test_load_empty_creates_blank_manifest(self, tmp_dir):
        sub = tmp_dir / "empty_sub"
        m = Manifest(sub)
        assert m._clock == 0
        assert m.list_files() == []


# ------------------------------------------------------------------
# Thread safety
# ------------------------------------------------------------------


class TestThreadSafety:
    """Concurrent mutation tests."""

    def test_concurrent_add_file(self, tmp_dir):
        m = Manifest(tmp_dir)
        errors: list[Exception] = []

        def _add(idx: int) -> None:
            try:
                m.add_file(_make_entry(file_id=f"file-{idx}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_add, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Unexpected errors: {errors}"
        assert len(m.list_files()) == 50
        # Clock should equal number of mutations.
        assert m._clock == 50
