import os
import random
import pytest

from firecloud.fec import encode, decode, compute_n


def test_compute_n():
    assert compute_n(4) == 6
    assert compute_n(5) == 8
    assert compute_n(10, 2.0) == 20


@pytest.mark.parametrize(
    "data_size, k",
    [
        (0, 3),
        (1, 3),
        (100, 3),
        (1024, 4),
        (12345, 5),
        (1024 * 1024, 8),  # 1MB
    ],
)
def test_fec_round_trip(data_size, k):
    data = os.urandom(data_size)
    n = compute_n(k)
    
    shares = encode(data, k, n)
    assert len(shares) == n
    
    # Pack shares with their indices
    indexed_shares = list(enumerate(shares))
    
    # 1. Decode with first k shares
    reconstructed = decode(indexed_shares[:k], k)
    assert reconstructed == data
    
    # 2. Decode with last k shares
    reconstructed = decode(indexed_shares[-k:], k)
    assert reconstructed == data
    
    # 3. Decode with random k shares
    random.seed(42)
    for _ in range(5):
        chosen = random.sample(indexed_shares, k)
        reconstructed = decode(chosen, k)
        assert reconstructed == data


def test_insufficient_shares_raises():
    data = b"Some test data for FEC"
    k = 4
    n = compute_n(k)
    shares = encode(data, k, n)
    indexed_shares = list(enumerate(shares))
    
    # Try decoding with k-1 shares
    with pytest.raises(ValueError, match="Insufficient shares"):
        decode(indexed_shares[:k-1], k)


def test_invalid_parameters():
    with pytest.raises(ValueError):
        encode(b"data", 0, 5)
        
    with pytest.raises(ValueError):
        encode(b"data", 5, 4)
