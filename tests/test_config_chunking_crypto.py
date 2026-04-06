from pathlib import Path

import pytest

from firecloud.chunking import iter_file_chunks, split_bytes, split_bytes_fastcdc
from firecloud.config import (
    ChunkingConfig,
    CompressionConfig,
    DedupGCConfig,
    FECConfig,
    FireCloudConfig,
    NodeConfig,
)
from firecloud.crypto import (
    ENCRYPTION_OVERHEAD,
    KEY_SIZE,
    NONCE_SIZE,
    decrypt_xchacha20poly1305,
    encrypt_xchacha20poly1305,
    generate_key,
)


def test_fec_config_validation() -> None:
    with pytest.raises(ValueError):
        FECConfig(source_symbols=0, total_symbols=5, symbol_size=1024)
    with pytest.raises(ValueError):
        FECConfig(source_symbols=3, total_symbols=2, symbol_size=1024)
    with pytest.raises(ValueError):
        FECConfig(source_symbols=3, total_symbols=5, symbol_size=0)


def test_firecloud_config_validation() -> None:
    with pytest.raises(ValueError):
        FireCloudConfig(node_count=2, fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128))

    with pytest.raises(ValueError):
        FireCloudConfig(
            nodes=(
                NodeConfig(node_id="node-a", endpoint="/tmp/a", kind="local"),
                NodeConfig(node_id="node-a", endpoint="/tmp/b", kind="local"),
            ),
            fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128),
        )

    with pytest.raises(ValueError):
        FireCloudConfig(
            nodes=(NodeConfig(node_id="node-a", endpoint="/tmp/a", kind="local"),),
            fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128),
        )

    cfg = FireCloudConfig(
        decentralized_mode=True,
        fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128),
    )
    assert cfg.node_definitions() == []

    with pytest.raises(ValueError):
        FireCloudConfig(
            decentralized_mode=True,
            nodes=(
                NodeConfig(node_id="node-a", endpoint="http://127.0.0.1:8091", kind="http"),
                NodeConfig(node_id="node-b", endpoint="/tmp/b", kind="local"),
                NodeConfig(node_id="node-c", endpoint="http://127.0.0.1:8093", kind="http"),
                NodeConfig(node_id="node-d", endpoint="http://127.0.0.1:8094", kind="http"),
                NodeConfig(node_id="node-e", endpoint="http://127.0.0.1:8095", kind="http"),
            ),
            fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128),
        )

    cfg_with_bootstrap = FireCloudConfig(
        decentralized_mode=True,
        bootstrap_peers=(" http://127.0.0.1:8080/ ",),
        fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128),
    )
    assert cfg_with_bootstrap.bootstrap_peers == ("http://127.0.0.1:8080",)

    with pytest.raises(ValueError):
        FireCloudConfig(bootstrap_peers=("",))
    with pytest.raises(ValueError):
        FireCloudConfig(bootstrap_peers=("ftp://127.0.0.1:8080",))
    with pytest.raises(ValueError):
        FireCloudConfig(
            bootstrap_peers=("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        )


def test_split_bytes_and_iter_chunks(tmp_path: Path) -> None:
    data = b"abcdefg"
    assert split_bytes(data, 3) == [b"abc", b"def", b"g"]
    assert split_bytes(b"", 3) == []
    with pytest.raises(ValueError):
        split_bytes(b"abc", 0)

    file_path = tmp_path / "data.bin"
    file_path.write_bytes(data)
    assert list(iter_file_chunks(str(file_path), 3)) == [b"abc", b"def", b"g"]
    with pytest.raises(ValueError):
        list(iter_file_chunks(str(file_path), -1))


def test_fastcdc_chunking_validation_and_boundaries() -> None:
    with pytest.raises(ValueError):
        split_bytes_fastcdc(b"abc", min_size=0, avg_size=4, max_size=8)
    with pytest.raises(ValueError):
        split_bytes_fastcdc(b"abc", min_size=8, avg_size=4, max_size=16)
    with pytest.raises(ValueError):
        split_bytes_fastcdc(b"abc", min_size=4, avg_size=8, max_size=6)

    data = (b"abc123" * 300) + (b"ZZZZ" * 300) + (b"abc123" * 300)
    chunks = split_bytes_fastcdc(
        data,
        min_size=64,
        avg_size=128,
        max_size=256,
        normalization_level=2,
    )
    assert b"".join(chunks) == data
    assert all(1 <= len(chunk) <= 256 for chunk in chunks)
    if len(chunks) > 1:
        assert all(len(chunk) >= 64 for chunk in chunks[:-1])


def test_chunking_and_compression_config_validation() -> None:
    with pytest.raises(ValueError):
        ChunkingConfig(min_size=0, avg_size=1024, max_size=2048)
    with pytest.raises(ValueError):
        ChunkingConfig(min_size=2048, avg_size=1024, max_size=4096)
    with pytest.raises(ValueError):
        ChunkingConfig(min_size=1024, avg_size=2048, max_size=4096, normalization_level=10)

    with pytest.raises(ValueError):
        CompressionConfig(min_savings_ratio=1.0)
    with pytest.raises(ValueError):
        CompressionConfig(sample_size=0)
    with pytest.raises(ValueError):
        DedupGCConfig(grace_period_days=-1)
    with pytest.raises(ValueError):
        DedupGCConfig(max_chunks_per_run=0)


def test_crypto_roundtrip_and_lengths() -> None:
    key = generate_key()
    payload = b"firecloud-payload"
    aad = b"context"
    encrypted = encrypt_xchacha20poly1305(key, payload, aad=aad)
    assert len(encrypted) == len(payload) + ENCRYPTION_OVERHEAD
    assert NONCE_SIZE > 0
    decrypted = decrypt_xchacha20poly1305(key, encrypted, aad=aad)
    assert decrypted == payload


def test_crypto_rejects_wrong_key_length() -> None:
    key = b"x" * (KEY_SIZE - 1)
    with pytest.raises(ValueError):
        encrypt_xchacha20poly1305(key, b"data")
    with pytest.raises(ValueError):
        decrypt_xchacha20poly1305(key, b"data")


def test_crypto_detects_tamper_and_wrong_aad() -> None:
    key = generate_key()
    payload = b"important-data"
    encrypted = encrypt_xchacha20poly1305(key, payload, aad=b"aad")

    tampered = bytearray(encrypted)
    tampered[-1] ^= 0x01
    with pytest.raises(ValueError):
        decrypt_xchacha20poly1305(key, bytes(tampered), aad=b"aad")

    with pytest.raises(ValueError):
        decrypt_xchacha20poly1305(key, encrypted, aad=b"wrong-aad")

    with pytest.raises(ValueError):
        decrypt_xchacha20poly1305(key, b"x" * NONCE_SIZE, aad=b"aad")
