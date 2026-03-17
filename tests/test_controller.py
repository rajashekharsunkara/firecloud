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
    symbol_full_path = controller.nodes[online_symbol.node_id].root_dir / online_symbol.symbol_path
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
