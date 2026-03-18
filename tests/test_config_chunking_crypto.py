from pathlib import Path

import pytest

from firecloud.chunking import iter_file_chunks, split_bytes
from firecloud.config import FECConfig, FireCloudConfig, NodeConfig
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
