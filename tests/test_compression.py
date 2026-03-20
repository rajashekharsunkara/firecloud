from __future__ import annotations

import importlib.util
import os
import zlib

import pytest

from firecloud.compression import ALGO_NONE, compress_chunk, decompress_chunk

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("zstandard") is None,
    reason="zstandard package is not installed",
)


def test_text_data_is_compressed_and_roundtrips() -> None:
    payload = (b"line=firecloud\n" * 2000) + (b"id=123\n" * 1000)
    result = compress_chunk("sample.txt", payload)
    assert result.algorithm.startswith("zstd:")
    restored = decompress_chunk(result.algorithm, result.payload)
    assert restored == payload


def test_already_compressed_type_skips_compression() -> None:
    payload = os.urandom(8_000)
    result = compress_chunk("video.mp4", payload)
    assert result.algorithm == ALGO_NONE
    assert result.payload == payload


def test_unknown_binary_can_be_left_uncompressed() -> None:
    payload = os.urandom(6_000)
    result = compress_chunk("unknown.data", payload)
    assert decompress_chunk(result.algorithm, result.payload) == payload


def test_zlib_backward_compatibility_decompression() -> None:
    payload = b"legacy-zlib-payload" * 400
    compressed = zlib.compress(payload, level=6)
    restored = decompress_chunk("zlib:6", compressed)
    assert restored == payload
