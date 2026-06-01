"""FireCloud Chunker Engine.

Wraps FastCDC for content-defined chunking with keyed chunk addressing
and integrity verification.
"""

from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path
from fastcdc import fastcdc

from firecloud.crypto import derive_chunk_id, compute_integrity_hash


@dataclass
class Chunk:
    """Represents a content-defined chunk of a file."""
    index: int
    offset: int
    length: int
    data: bytes
    chunk_id: str        # HMAC-SHA-256 (keyed address)
    integrity_hash: str  # SHA-256 (for verification)


def chunk_file(
    filepath: Path | str,
    hmac_key: bytes,
    min_size: int = 4096,
    avg_size: int = 16384,
    max_size: int = 65536,
) -> list[Chunk]:
    """Read a file and chunk it with FastCDC.

    Computes the keyed chunk_id and integrity_hash for each chunk.
    """
    path = Path(filepath)
    if not path.exists() or path.stat().st_size == 0:
        return []
        
    # fastcdc expects string filepath or bytes.
    # We use fat=True so that c.data contains the actual chunk bytes.
    cdc_chunks = fastcdc(
        str(path),
        min_size=min_size,
        avg_size=avg_size,
        max_size=max_size,
        fat=True,
    )
    
    chunks = []
    for index, c in enumerate(cdc_chunks):
        chunk_data = c.data
        chunk_id = derive_chunk_id(chunk_data, hmac_key)
        int_hash = compute_integrity_hash(chunk_data)
        chunks.append(
            Chunk(
                index=index,
                offset=c.offset,
                length=c.length,
                data=chunk_data,
                chunk_id=chunk_id,
                integrity_hash=int_hash,
            )
        )
    return chunks


def chunk_bytes(
    data: bytes,
    hmac_key: bytes,
    min_size: int = 4096,
    avg_size: int = 16384,
    max_size: int = 65536,
) -> list[Chunk]:
    """Chunk in-memory bytes using FastCDC."""
    if not data:
        return []
        
    cdc_chunks = fastcdc(
        data,
        min_size=min_size,
        avg_size=avg_size,
        max_size=max_size,
        fat=True,
    )
    
    chunks = []
    for index, c in enumerate(cdc_chunks):
        chunk_data = c.data
        chunk_id = derive_chunk_id(chunk_data, hmac_key)
        int_hash = compute_integrity_hash(chunk_data)
        chunks.append(
            Chunk(
                index=index,
                offset=c.offset,
                length=c.length,
                data=chunk_data,
                chunk_id=chunk_id,
                integrity_hash=int_hash,
            )
        )
    return chunks


def reassemble_chunks(chunks: list[Chunk]) -> bytes:
    """Reassemble chunks in index order back to original bytes."""
    sorted_chunks = sorted(chunks, key=lambda c: c.index)
    return b"".join(c.data for c in sorted_chunks)


def compute_file_id(filepath: Path | str, hmac_key: bytes) -> str:
    """Compute the HMAC-SHA-256 of the entire file content."""
    h = hmac.new(hmac_key, digestmod=hashlib.sha256)
    with open(filepath, "rb") as f:
        while True:
            block = f.read(65536)
            if not block:
                break
            h.update(block)
    return h.hexdigest()
