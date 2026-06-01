"""FireCloud Forward Error Correction (FEC) Engine.

Wraps zfec to encode arbitrary bytes into N shares where any K can reconstruct
the original data. Handles padding and size restoration transparently.
"""

import math
import struct
import zfec


def compute_n(k: int, expansion: float = 1.5) -> int:
    """Compute the total number of shares N given the reconstruction threshold K.

    N is calculated as ceil(K * expansion).
    """
    return math.ceil(k * expansion)


def encode(data: bytes, k: int, n: int) -> list[bytes]:
    """Encode arbitrary bytes into N shares. Any K shares can reconstruct.

    The original length is prefixed to the data so that padding can be
    correctly stripped during decoding.

    Args:
        data: The input bytes to encode.
        k: The threshold number of shares needed for decoding.
        n: The total number of shares to generate.

    Returns:
        A list of N shares (each as bytes).
    """
    if k <= 0 or n < k:
        raise ValueError("Invalid FEC parameters: K must be > 0 and N >= K")

    original_len = len(data)
    # Prefix the original length (8 bytes) to the data
    prepended = struct.pack("!Q", original_len) + data

    # Pad prepended data so its length is a multiple of K
    pad_len = (k - (len(prepended) % k)) % k
    padded = prepended + (b"\x00" * pad_len)

    # Split into K equal blocks
    block_size = len(padded) // k
    blocks = [padded[i * block_size : (i + 1) * block_size] for i in range(k)]

    # Run zfec encoder
    encoder = zfec.Encoder(k, n)
    shares = encoder.encode(blocks)
    return shares


def decode(shares: list[tuple[int, bytes]], k: int) -> bytes:
    """Decode K shares back to the original bytes.

    Args:
        shares: A list of tuples containing (blocknum, share_data).
        k: The threshold number of shares required.

    Returns:
        The reconstructed original bytes.
    """
    if len(shares) < k:
        raise ValueError(f"Insufficient shares: need at least {k}, got {len(shares)}")

    # Take the first K shares
    k_shares = shares[:k]
    blocknums = [s[0] for s in k_shares]
    blocks = [s[1] for s in k_shares]

    # Instantiate decoder. We need m (which is N).
    # We can infer N from the maximum block number in our shares.
    max_blocknum = max(blocknums)
    n = max(max_blocknum + 1, k)

    decoder = zfec.Decoder(k, n)
    decoded_blocks = decoder.decode(blocks, blocknums)
    padded = b"".join(decoded_blocks)

    # Extract original length and strip padding
    if len(padded) < 8:
        raise ValueError("Decoded data is too short to contain length prefix")

    original_len = struct.unpack("!Q", padded[:8])[0]
    return padded[8 : 8 + original_len]
