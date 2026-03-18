import os
import random
from pathlib import Path

import pytest

from firecloud.config import FECConfig, FireCloudConfig
from firecloud.controller import FireCloudController


def _controller(tmp_path: Path) -> FireCloudController:
    cfg = FireCloudConfig(
        root_dir=tmp_path / "state",
        node_count=5,
        fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128),
    )
    return FireCloudController(cfg)


def _set_all_nodes(controller: FireCloudController, online: bool) -> None:
    for node in controller.list_nodes():
        controller.set_node_online(node.node_id, online)


def test_randomized_outages_within_tolerance_keep_file_recoverable(tmp_path: Path) -> None:
    rng = random.Random(42)
    controller = _controller(tmp_path)
    source = tmp_path / "source.bin"
    original = os.urandom(20_000)
    source.write_bytes(original)
    file_id = controller.upload_file(source)
    node_ids = [node.node_id for node in controller.list_nodes()]

    for scenario in range(15):
        _set_all_nodes(controller, True)
        offline_count = rng.randint(0, 2)  # <= n-k tolerance
        offline_nodes = rng.sample(node_ids, offline_count)
        for node_id in offline_nodes:
            controller.set_node_online(node_id, False)

        restored = tmp_path / f"restored-{scenario}.bin"
        controller.download_file(file_id, restored)
        assert restored.read_bytes() == original


def test_randomized_outages_above_tolerance_fail_download(tmp_path: Path) -> None:
    rng = random.Random(7)
    controller = _controller(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"x" * 8_000)
    file_id = controller.upload_file(source)
    node_ids = [node.node_id for node in controller.list_nodes()]

    for _ in range(5):
        _set_all_nodes(controller, True)
        for node_id in rng.sample(node_ids, 3):  # > n-k tolerance
            controller.set_node_online(node_id, False)
        with pytest.raises(RuntimeError):
            controller.download_file(file_id, tmp_path / "unrecoverable.bin")


def test_repair_restores_missing_symbol_files(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    source = tmp_path / "small.bin"
    source.write_bytes(b"repair-check" * 20)  # single chunk for this config
    file_id = controller.upload_file(source)

    chunk = controller.metadata.list_chunks(file_id)[0]
    symbols = controller.metadata.list_symbols(chunk.chunk_id)
    removed = symbols[:2]
    for symbol in removed:
        full_path = controller.local_symbol_path(symbol.node_id, symbol.symbol_path)
        assert full_path is not None
        full_path.unlink()
        assert not full_path.exists()

    repaired_count = controller.repair_file(file_id)
    assert repaired_count >= len(removed)

    for symbol in symbols:
        full_path = controller.local_symbol_path(symbol.node_id, symbol.symbol_path)
        assert full_path is not None
        assert full_path.exists()

    restored = tmp_path / "restored.bin"
    controller.download_file(file_id, restored)
    assert restored.read_bytes() == source.read_bytes()


def test_repair_fails_when_too_many_symbols_lost(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    source = tmp_path / "small.bin"
    source.write_bytes(b"insufficient-symbols" * 20)  # single chunk for this config
    file_id = controller.upload_file(source)

    chunk = controller.metadata.list_chunks(file_id)[0]
    symbols = controller.metadata.list_symbols(chunk.chunk_id)
    for symbol in symbols[:3]:
        full_path = controller.local_symbol_path(symbol.node_id, symbol.symbol_path)
        assert full_path is not None
        full_path.unlink()
        assert not full_path.exists()

    with pytest.raises(RuntimeError):
        controller.repair_file(file_id)
