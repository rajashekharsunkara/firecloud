from pathlib import Path

import pytest

from firecloud.config import FECConfig, FireCloudConfig
from firecloud.controller import FireCloudController


def _controller(tmp_path: Path, symbol_size: int = 128, nodes: int = 5) -> FireCloudController:
    cfg = FireCloudConfig(
        root_dir=tmp_path / "state",
        node_count=nodes,
        fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=symbol_size),
    )
    return FireCloudController(config=cfg)


def _total_symbols(controller: FireCloudController) -> int:
    return sum(node.symbol_count for node in controller.list_nodes())


def test_upload_download_and_repair_flow(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    source_path = tmp_path / "sample.bin"
    original_data = b"firecloud-local-simulation" * 400
    source_path.write_bytes(original_data)

    file_id = controller.upload_file(source_path)
    assert len(controller.list_files()) == 1

    controller.set_node_online("node-1", False)
    controller.set_node_online("node-2", False)

    restored_path = tmp_path / "restored.bin"
    controller.download_file(file_id=file_id, destination_path=restored_path)
    assert restored_path.read_bytes() == original_data

    controller.set_node_online("node-3", False)
    with pytest.raises(RuntimeError):
        controller.download_file(file_id=file_id, destination_path=tmp_path / "should_fail.bin")

    controller.set_node_online("node-3", True)
    repaired = controller.repair_file(file_id=file_id)
    assert repaired >= 0

    valid, details = controller.verify_audit_chain()
    assert valid, details


def test_upload_rejects_missing_file(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    with pytest.raises(ValueError):
        controller.upload_file(tmp_path / "not-found.bin")


def test_upload_not_enough_online_nodes_does_not_persist_file(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.set_node_online("node-1", False)
    payload = tmp_path / "x.bin"
    payload.write_bytes(b"123")

    with pytest.raises(RuntimeError):
        controller.upload_file(payload)

    assert controller.list_files() == []


def test_download_unknown_file_raises(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    with pytest.raises(ValueError):
        controller.download_file("missing", tmp_path / "out.bin")


def test_set_unknown_node_raises(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    with pytest.raises(ValueError):
        controller.set_node_online("node-999", False)


def test_zero_length_file_roundtrip(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    source_path = tmp_path / "empty.bin"
    source_path.write_bytes(b"")
    file_id = controller.upload_file(source_path)
    out = tmp_path / "out" / "empty-restored.bin"
    controller.download_file(file_id, out)
    assert out.read_bytes() == b""


def test_download_fails_if_missing_symbol_with_only_k_nodes_left(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    source_path = tmp_path / "sample.bin"
    source_path.write_bytes(b"a" * 5000)
    file_id = controller.upload_file(source_path)

    controller.set_node_online("node-1", False)
    controller.set_node_online("node-2", False)

    chunk = controller.metadata.list_chunks(file_id)[0]
    symbols = controller.metadata.list_symbols(chunk.chunk_id)
    online_symbol = next(s for s in symbols if s.node_id in {"node-3", "node-4", "node-5"})
    symbol_full_path = controller.local_symbol_path(
        online_symbol.node_id, online_symbol.symbol_path
    )
    assert symbol_full_path is not None
    symbol_full_path.unlink()

    with pytest.raises(RuntimeError):
        controller.download_file(file_id, tmp_path / "out.bin")


def test_repair_unknown_file_raises(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    with pytest.raises(ValueError):
        controller.repair_file("missing")


def test_repair_requires_enough_online_nodes(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    source_path = tmp_path / "sample.bin"
    source_path.write_bytes(b"abc" * 200)
    file_id = controller.upload_file(source_path)

    controller.set_node_online("node-1", False)
    controller.set_node_online("node-2", False)
    controller.set_node_online("node-3", False)

    with pytest.raises(RuntimeError):
        controller.repair_file(file_id)


def test_verify_audit_detects_tamper(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    source_path = tmp_path / "sample.bin"
    source_path.write_bytes(b"hello")
    controller.upload_file(source_path)

    with controller.metadata._conn:
        controller.metadata._conn.execute(
            "UPDATE audit_events SET event_hash = 'bad-hash' WHERE sequence = 1"
        )

    valid, details = controller.verify_audit_chain()
    assert not valid
    assert "broken" in details.lower()


def test_controller_rejects_invalid_existing_master_key(tmp_path: Path) -> None:
    cfg = FireCloudConfig(
        root_dir=tmp_path / "state",
        node_count=5,
        fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128),
    )
    cfg.ensure_dirs()
    cfg.master_key_path.write_bytes(b"bad")
    with pytest.raises(ValueError):
        FireCloudController(config=cfg)


def test_controller_state_survives_restart(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    source_path = tmp_path / "sample.bin"
    source_path.write_bytes(b"persist-me" * 300)
    file_id = controller.upload_file(source_path)

    restarted = _controller(tmp_path)
    restored = tmp_path / "restored-restart.bin"
    restarted.download_file(file_id, restored)
    assert restored.read_bytes() == source_path.read_bytes()


def test_add_and_remove_node(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    extra_node_path = tmp_path / "extra-node"
    extra_node_path.mkdir(parents=True, exist_ok=True)

    controller.add_node("node-extra", "http://127.0.0.1:9999", kind="http")
    assert any(node.node_id == "node-extra" for node in controller.list_nodes())

    controller.remove_node("node-extra")
    assert all(node.node_id != "node-extra" for node in controller.list_nodes())


def test_dedup_skips_duplicate_symbol_storage_and_updates_refcount_on_delete(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    source = tmp_path / "same.bin"
    source.write_bytes((b"same-data-" * 400) + b"tail")

    file_a = controller.upload_file(source)
    symbols_after_a = _total_symbols(controller)

    file_b = controller.upload_file(source)
    symbols_after_b = _total_symbols(controller)
    assert symbols_after_b == symbols_after_a

    dedup_entries = controller.metadata.list_dedup_chunks()
    assert dedup_entries
    assert all(entry.ref_count >= 2 for entry in dedup_entries)

    controller.delete_file(file_a)
    dedup_after_delete = controller.metadata.list_dedup_chunks()
    assert dedup_after_delete
    assert all(entry.ref_count >= 1 for entry in dedup_after_delete)

    restored = tmp_path / "restored-dedup.bin"
    controller.download_file(file_b, restored)
    assert restored.read_bytes() == source.read_bytes()


def test_delete_unknown_file_raises(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    with pytest.raises(ValueError):
        controller.delete_file("missing-file")


def test_fastcdc_boundary_shift_preserves_some_chunk_hashes(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    base = b"".join(
        [(f"block-{index:04d}|".encode() + bytes([65 + (index % 26)]) * 96) for index in range(120)]
    )
    shifted = base[:500] + b"INSERTION" + base[500:]

    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(base)
    b.write_bytes(shifted)
    file_a = controller.upload_file(a)
    file_b = controller.upload_file(b)

    hashes_a = {controller.metadata.get_chunk_hash(chunk.chunk_id) for chunk in controller.metadata.list_chunks(file_a)}
    hashes_b = {controller.metadata.get_chunk_hash(chunk.chunk_id) for chunk in controller.metadata.list_chunks(file_b)}
    shared = {item for item in hashes_a.intersection(hashes_b) if item is not None}
    assert shared


def test_controller_compression_roundtrip_and_metadata_flags(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    source = tmp_path / "compressible.txt"
    source.write_text("firecloud-compression-line\n" * 5000)

    file_id = controller.upload_file(source)
    chunks = controller.metadata.list_chunks(file_id)
    assert chunks
    assert any(chunk.compression != "none" for chunk in chunks)

    restored = tmp_path / "restored-compressible.txt"
    controller.download_file(file_id, restored)
    assert restored.read_text() == source.read_text()


def test_dedup_gc_force_deletes_symbol_files_and_index_entries(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    source = tmp_path / "gc.bin"
    source.write_bytes((b"gc-payload-" * 300) + b"end")

    file_id = controller.upload_file(source)
    chunks = controller.metadata.list_chunks(file_id)
    assert chunks
    chunk_hash = controller.metadata.get_chunk_hash(chunks[0].chunk_id)
    assert chunk_hash is not None

    dedup_symbols = controller.metadata.list_dedup_symbols(chunk_hash)
    assert dedup_symbols
    full_paths = [
        controller.local_symbol_path(symbol.node_id, symbol.symbol_path) for symbol in dedup_symbols
    ]
    assert all(path is not None for path in full_paths)
    assert all(path.exists() for path in full_paths if path is not None)

    controller.delete_file(file_id)
    dedup_record = controller.metadata.get_dedup_chunk(chunk_hash)
    assert dedup_record is not None
    assert dedup_record.gc_pending
    assert dedup_record.ref_count == 0

    normal_run = controller.run_dedup_gc(force=False)
    assert normal_run["deleted_chunks"] == 0

    forced = controller.run_dedup_gc(force=True)
    assert forced["deleted_chunks"] >= 1
    assert controller.metadata.get_dedup_chunk(chunk_hash) is None
    assert controller.metadata.list_dedup_symbols(chunk_hash) == []
    assert all(not path.exists() for path in full_paths if path is not None)
