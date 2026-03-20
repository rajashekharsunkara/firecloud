from __future__ import annotations

import math
from typing import Iterator

_MASK64 = (1 << 64) - 1


def _build_gear_table() -> tuple[int, ...]:
    # Deterministic values are enough for boundary stability in tests/runtime.
    state = 0x9E3779B185EBCA87
    values: list[int] = []
    for _ in range(256):
        state ^= (state << 7) & _MASK64
        state ^= (state >> 9) & _MASK64
        state ^= (state << 8) & _MASK64
        values.append(state & _MASK64)
    return tuple(values)


_GEAR = _build_gear_table()


def split_bytes(data: bytes, chunk_size: int) -> list[bytes]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    return [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]


def split_bytes_fastcdc(
    data: bytes,
    min_size: int,
    avg_size: int,
    max_size: int,
    normalization_level: int = 2,
) -> list[bytes]:
    if min_size <= 0:
        raise ValueError("min_size must be > 0")
    if avg_size <= 0:
        raise ValueError("avg_size must be > 0")
    if max_size <= 0:
        raise ValueError("max_size must be > 0")
    if min_size > avg_size:
        raise ValueError("min_size must be <= avg_size")
    if avg_size > max_size:
        raise ValueError("avg_size must be <= max_size")
    if normalization_level < 0 or normalization_level > 3:
        raise ValueError("normalization_level must be between 0 and 3")
    if not data:
        return []

    avg_bits = max(1, int(round(math.log2(avg_size))))
    small_bits = max(1, avg_bits + normalization_level)
    large_bits = max(1, avg_bits - normalization_level)
    small_mask = (1 << small_bits) - 1
    large_mask = (1 << large_bits) - 1

    start = 0
    chunks: list[bytes] = []
    total = len(data)
    while start < total:
        end = _find_next_boundary(
            data=data,
            start=start,
            min_size=min_size,
            avg_size=avg_size,
            max_size=max_size,
            small_mask=small_mask,
            large_mask=large_mask,
        )
        chunks.append(data[start:end])
        start = end
    return chunks


def _find_next_boundary(
    data: bytes,
    start: int,
    min_size: int,
    avg_size: int,
    max_size: int,
    small_mask: int,
    large_mask: int,
) -> int:
    total = len(data)
    forced_end = min(start + max_size, total)
    if forced_end - start <= min_size:
        return forced_end

    idx = start + min_size
    normal_end = min(start + avg_size, forced_end)
    fingerprint = 0
    while idx < normal_end:
        fingerprint = ((fingerprint << 1) + _GEAR[data[idx]]) & _MASK64
        if (fingerprint & small_mask) == 0:
            return idx + 1
        idx += 1

    while idx < forced_end:
        fingerprint = ((fingerprint << 1) + _GEAR[data[idx]]) & _MASK64
        if (fingerprint & large_mask) == 0:
            return idx + 1
        idx += 1
    return forced_end


def iter_file_chunks(path: str, chunk_size: int) -> Iterator[bytes]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk
