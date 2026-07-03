"""zfec wrapper: encode bytes into N shares, any K of which reconstruct the
original. Padding and length restoration are handled here."""

import math
import struct
import zfec


def compute_n(k: int, expansion: float = 1.5) -> int:
    """Total share count N for threshold K: ceil(K * expansion)."""
    return math.ceil(k * expansion)


def encode(data: bytes, k: int, n: int) -> list[bytes]:
    """Encode *data* into N shares; any K reconstruct it.

    The original length is prefixed so padding can be stripped on decode.
    """
    if k <= 0 or n < k:
        raise ValueError("Invalid FEC parameters: K must be > 0 and N >= K")

    original_len = len(data)
    prepended = struct.pack("!Q", original_len) + data

    # zfec wants K equal-sized blocks.
    pad_len = (k - (len(prepended) % k)) % k
    padded = prepended + (b"\x00" * pad_len)

    block_size = len(padded) // k
    blocks = [padded[i * block_size : (i + 1) * block_size] for i in range(k)]

    encoder = zfec.Encoder(k, n)
    shares = encoder.encode(blocks)
    return shares


def decode(shares: list[tuple[int, bytes]], k: int) -> bytes:
    """Decode (blocknum, data) share tuples back to the original bytes."""
    if len(shares) < k:
        raise ValueError(f"Insufficient shares: need at least {k}, got {len(shares)}")

    k_shares = shares[:k]
    blocknums = [s[0] for s in k_shares]
    blocks = [s[1] for s in k_shares]

    # zfec's decoder needs N; the highest block number bounds it.
    max_blocknum = max(blocknums)
    n = max(max_blocknum + 1, k)

    decoder = zfec.Decoder(k, n)
    decoded_blocks = decoder.decode(blocks, blocknums)
    padded = b"".join(decoded_blocks)

    if len(padded) < 8:
        raise ValueError("Decoded data is too short to contain length prefix")

    original_len = struct.unpack("!Q", padded[:8])[0]
    return padded[8 : 8 + original_len]
