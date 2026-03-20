from pathlib import Path

from firecloud.metadata import MetadataStore
from firecloud.storage import NodeStore


def test_node_store_put_get_has_count(tmp_path: Path) -> None:
    store = NodeStore(node_id="node-1", root_dir=tmp_path / "node-1")
    rel = store.put_symbol(chunk_id="chunk-1", symbol_id=2, symbol_data=b"abc")
    assert store.has_symbol(rel)
    assert store.get_symbol(rel) == b"abc"
    assert store.symbol_count() == 1


def test_metadata_crud_and_audit(tmp_path: Path) -> None:
    db_path = tmp_path / "meta.db"
    metadata = MetadataStore(db_path)

    metadata.upsert_node("node-1", "online", endpoint=str(tmp_path / "node-1"), kind="local")
    nodes = metadata.list_nodes()
    assert len(nodes) == 1
    assert nodes[0].online
    assert nodes[0].kind == "local"

    metadata.create_file("file-1", "a.txt", 10)
    file_record = metadata.get_file("file-1")
    assert file_record is not None
    assert file_record.file_name == "a.txt"

    metadata.add_chunk("chunk-1", "file-1", 0, 10, 40)
    chunks = metadata.list_chunks("file-1")
    assert len(chunks) == 1
    assert chunks[0].encrypted_size == 40
    assert chunks[0].compressed_size == 10
    assert chunks[0].compression == "none"

    metadata.add_symbol("chunk-1", "node-1", 0, "symbols/chunk-1/0.bin")
    symbols = metadata.list_symbols("chunk-1")
    assert len(symbols) == 1
    assert symbols[0].symbol_id == 0

    counts = metadata.list_chunk_symbol_counts("file-1")
    assert counts == {"chunk-1": 1}

    assert metadata.latest_event_hash() == "GENESIS"
    metadata.append_audit_event(
        event_type="test_event",
        payload={"x": 1},
        prev_hash="GENESIS",
        event_hash="hash-1",
    )
    assert metadata.latest_event_hash() == "hash-1"

    events = metadata.list_audit_events(limit=10)
    assert len(events) == 1
    assert events[0].payload["x"] == 1

    fetched = metadata.get_node("node-1")
    assert fetched is not None
    metadata.remove_node("node-1")
    assert metadata.get_node("node-1") is None


def test_dedup_tables_and_refcount_lifecycle(tmp_path: Path) -> None:
    metadata = MetadataStore(tmp_path / "meta.db")
    metadata.create_file("file-1", "one.bin", 100)
    metadata.create_file("file-2", "two.bin", 100)
    metadata.add_chunk("chunk-1", "file-1", 0, 100, 120, compressed_size=80, compression="zlib:6")
    metadata.add_chunk("chunk-2", "file-2", 0, 100, 120, compressed_size=80, compression="zlib:6")

    metadata.create_dedup_chunk(
        chunk_hash="hash-1",
        canonical_chunk_id="chunk-1",
        plain_size=100,
        compressed_size=80,
        compression="zlib:6",
        encrypted_size=120,
    )
    metadata.add_chunk_dedup_ref("chunk-1", "hash-1")
    metadata.add_chunk_dedup_ref("chunk-2", "hash-1")
    metadata.increment_dedup_ref_count("hash-1")

    record = metadata.get_dedup_chunk("hash-1")
    assert record is not None
    assert record.ref_count == 2
    assert not record.gc_pending
    assert metadata.get_chunk_hash("chunk-2") == "hash-1"

    updated = metadata.decrement_dedup_ref_count("hash-1")
    assert updated.ref_count == 1
    assert not updated.gc_pending

    updated = metadata.decrement_dedup_ref_count("hash-1")
    assert updated.ref_count == 0
    assert updated.gc_pending

    metadata.upsert_dedup_symbol("hash-1", "node-1", 0, "symbols/hash-1/0.bin")
    metadata.upsert_dedup_symbol("hash-1", "node-2", 1, "symbols/hash-1/1.bin")
    dedup_symbols = metadata.list_dedup_symbols("hash-1")
    assert len(dedup_symbols) == 2

    pending = metadata.list_gc_pending_dedup_chunks(limit=10)
    assert any(item.chunk_hash == "hash-1" for item in pending)

    metadata.delete_dedup_symbols("hash-1")
    assert metadata.list_dedup_symbols("hash-1") == []
