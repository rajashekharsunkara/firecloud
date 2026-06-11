import os

from firecloud.chunker import (
    chunk_file,
    chunk_bytes,
    reassemble_chunks,
    compute_file_id,
)


def test_small_file(tmp_path, network_key):
    # Small file (< min_size) -> single chunk
    filepath = tmp_path / "small.txt"
    content = b"Hello, firecloud!"
    filepath.write_bytes(content)
    
    chunks = chunk_file(filepath, network_key)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].offset == 0
    assert chunks[0].length == len(content)
    assert chunks[0].data == content


def test_medium_file(sample_file, network_key):
    # sample_file (~18KB) -> multiple chunks
    content = sample_file.read_bytes()
    # Let's chunk it with small sizes to guarantee multiple chunks
    chunks = chunk_file(
        sample_file,
        network_key,
        min_size=1024,
        avg_size=4096,
        max_size=8192,
    )
    assert len(chunks) > 1

    # Chunks must concatenate back to the original content
    assert b"".join(c.data for c in chunks) == content

    # Assert proper indexing
    for idx, c in enumerate(chunks):
        assert c.index == idx
        if idx > 0:
            assert c.offset == chunks[idx - 1].offset + chunks[idx - 1].length


def test_large_file(large_sample_file, network_key):
    # large_sample_file (~256KB)
    content = large_sample_file.read_bytes()
    chunks = chunk_file(large_sample_file, network_key)
    assert len(chunks) > 1
    
    # Check that they can be reassembled
    reconstructed = reassemble_chunks(chunks)
    assert reconstructed == content


def test_chunk_bytes_vs_file(sample_file, network_key):
    content = sample_file.read_bytes()
    chunks_file = chunk_file(sample_file, network_key)
    chunks_bytes = chunk_bytes(content, network_key)
    
    assert len(chunks_file) == len(chunks_bytes)
    for cf, cb in zip(chunks_file, chunks_bytes):
        assert cf.index == cb.index
        assert cf.offset == cb.offset
        assert cf.length == cb.length
        assert cf.data == cb.data
        assert cf.chunk_id == cb.chunk_id
        assert cf.integrity_hash == cb.integrity_hash


def test_deduplication(network_key):
    content = b"Deduplication test " * 1000
    chunks1 = chunk_bytes(content, network_key)
    chunks2 = chunk_bytes(content, network_key)
    
    assert len(chunks1) == len(chunks2)
    for c1, c2 in zip(chunks1, chunks2):
        assert c1.chunk_id == c2.chunk_id


def test_modified_file(network_key):
    content1 = b"A" * 20000 + b"B" * 20000 + b"C" * 20000
    content2 = b"A" * 20000 + b"X" * 100 + b"B" * 19900 + b"C" * 20000
    
    chunks1 = chunk_bytes(
        content1, network_key, min_size=4096, avg_size=8192, max_size=16384
    )
    chunks2 = chunk_bytes(
        content2, network_key, min_size=4096, avg_size=8192, max_size=16384
    )
    
    # Check that some chunk IDs are identical (dedup works for unchanged regions)
    ids1 = {c.chunk_id for c in chunks1}
    ids2 = {c.chunk_id for c in chunks2}
    intersection = ids1.intersection(ids2)
    assert len(intersection) > 0
    assert ids1 != ids2


def test_different_keys_different_ids(sample_file):
    key1 = b"\x01" * 32
    key2 = b"\x02" * 32
    
    chunks1 = chunk_file(sample_file, key1)
    chunks2 = chunk_file(sample_file, key2)
    
    assert len(chunks1) == len(chunks2)
    for c1, c2 in zip(chunks1, chunks2):
        assert c1.chunk_id != c2.chunk_id


def test_empty_file(tmp_path, network_key):
    filepath = tmp_path / "empty.txt"
    filepath.write_bytes(b"")
    chunks = chunk_file(filepath, network_key)
    assert len(chunks) == 0
    
    chunks_b = chunk_bytes(b"", network_key)
    assert len(chunks_b) == 0


def test_compute_file_id(tmp_path):
    filepath = tmp_path / "file_id_test.bin"
    content = os.urandom(10000)
    filepath.write_bytes(content)
    
    key1 = b"\x01" * 32
    key2 = b"\x02" * 32
    
    fid1 = compute_file_id(filepath, key1)
    fid1_again = compute_file_id(filepath, key1)
    fid2 = compute_file_id(filepath, key2)
    
    assert fid1 == fid1_again
    assert fid1 != fid2


def test_custom_chunk_sizes(sample_file, network_key):
    chunks = chunk_file(
        sample_file,
        network_key,
        min_size=1024,
        avg_size=2048,
        max_size=4096,
    )
    for c in chunks:
        # Note: the last chunk might be smaller if there is less data remaining,
        # but all prior chunks must respect the minimum boundary.
        if c.index < len(chunks) - 1:
            assert c.length >= 1024
            assert c.length <= 4096
