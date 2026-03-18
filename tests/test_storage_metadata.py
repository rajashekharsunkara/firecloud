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
