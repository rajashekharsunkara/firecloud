from __future__ import annotations

from typing import Iterator


def split_bytes(data: bytes, chunk_size: int) -> list[bytes]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    return [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]


def iter_file_chunks(path: str, chunk_size: int) -> Iterator[bytes]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk
