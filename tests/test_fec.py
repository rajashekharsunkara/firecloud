import os
import random

import pytest

from firecloud.fec import RaptorQCodec


def test_raptorq_roundtrip_with_any_k_symbols() -> None:
    codec = RaptorQCodec(source_symbols=3, total_symbols=5, symbol_size=256)
    payload = os.urandom(codec.payload_size - 33)
    encoded = codec.encode(payload)

    symbol_ids = list(encoded.symbols.keys())
    random.shuffle(symbol_ids)
    selected = symbol_ids[: codec.source_symbols]
    decoded = codec.decode({sid: encoded.symbols[sid] for sid in selected}, original_size=len(payload))
    assert decoded == payload


def test_raptorq_config_validation() -> None:
    with pytest.raises(ValueError):
        RaptorQCodec(source_symbols=0, total_symbols=5, symbol_size=64)
    with pytest.raises(ValueError):
        RaptorQCodec(source_symbols=3, total_symbols=2, symbol_size=64)
    with pytest.raises(ValueError):
        RaptorQCodec(source_symbols=3, total_symbols=5, symbol_size=0)


def test_raptorq_encode_rejects_oversized_chunk() -> None:
    codec = RaptorQCodec(source_symbols=3, total_symbols=5, symbol_size=128)
    with pytest.raises(ValueError):
        codec.encode(os.urandom(codec.payload_size + 1))


def test_raptorq_decode_rejects_invalid_inputs() -> None:
    codec = RaptorQCodec(source_symbols=3, total_symbols=5, symbol_size=128)
    payload = os.urandom(200)
    encoded = codec.encode(payload)

    with pytest.raises(ValueError):
        codec.decode({0: encoded.symbols[0], 1: encoded.symbols[1]}, original_size=len(payload))
    with pytest.raises(ValueError):
        codec.decode({0: encoded.symbols[0], 1: encoded.symbols[1], 2: encoded.symbols[2]}, original_size=9999)


def test_raptorq_roundtrip_exact_payload_size() -> None:
    codec = RaptorQCodec(source_symbols=4, total_symbols=7, symbol_size=64)
    payload = os.urandom(codec.payload_size)
    encoded = codec.encode(payload)
    selected = {sid: encoded.symbols[sid] for sid in range(codec.source_symbols)}
    decoded = codec.decode(selected, original_size=len(payload))
    assert decoded == payload


def test_raptorq_roundtrip_empty_payload() -> None:
    codec = RaptorQCodec(source_symbols=3, total_symbols=5, symbol_size=32)
    encoded = codec.encode(b"")
    selected = {sid: encoded.symbols[sid] for sid in range(codec.source_symbols)}
    decoded = codec.decode(selected, original_size=0)
    assert decoded == b""
