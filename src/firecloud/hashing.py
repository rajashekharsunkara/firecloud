from blake3 import blake3


def blake3_hex(data: bytes) -> str:
    return blake3(data).hexdigest()


def chunk_hash(data: bytes) -> str:
    return blake3_hex(data)
