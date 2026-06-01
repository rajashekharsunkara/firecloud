"""Comprehensive tests for firecloud.crypto."""

import os

import pytest

from firecloud.crypto import (
    compute_integrity_hash,
    decrypt_chunk,
    decrypt_keystore,
    derive_auth_token,
    derive_chunk_id,
    derive_encryption_key,
    derive_hmac_key,
    encrypt_chunk,
    encrypt_keystore,
    generate_network_key,
)
from firecloud.exceptions import ChunkCorruptError, NetworkKeyError


# ── Helpers ────────────────────────────────────────────────────────────────


def _random_key() -> bytes:
    return os.urandom(32)


# ── Encrypt / Decrypt round-trip ──────────────────────────────────────────


class TestEncryptDecryptRoundTrip:
    """encrypt_chunk -> decrypt_chunk should recover the original plaintext."""

    @pytest.mark.parametrize(
        "plaintext",
        [
            b"",                          # empty
            b"\x42",                      # single byte
            b"x" * 1024,                  # 1 KB
            os.urandom(64 * 1024),        # 64 KB random
        ],
        ids=["empty", "1-byte", "1KB", "64KB"],
    )
    def test_round_trip_various_sizes(self, plaintext: bytes) -> None:
        key = _random_key()
        encrypted = encrypt_chunk(plaintext, key)
        assert decrypt_chunk(encrypted, key) == plaintext

    def test_ciphertext_is_longer_than_plaintext(self) -> None:
        """Nonce (24) + tag (16) = 40 extra bytes."""
        key = _random_key()
        pt = b"hello"
        ct = encrypt_chunk(pt, key)
        assert len(ct) == len(pt) + 24 + 16

    def test_different_nonce_each_call(self) -> None:
        """Two encryptions of the same data must produce different ciphertexts."""
        key = _random_key()
        pt = b"deterministic?"
        c1 = encrypt_chunk(pt, key)
        c2 = encrypt_chunk(pt, key)
        assert c1 != c2  # nonce is random

    def test_decrypt_with_same_key_fixture(self, network_key: bytes) -> None:
        """Round-trip using the shared network_key fixture."""
        pt = b"fixture round-trip"
        ct = encrypt_chunk(pt, network_key)
        assert decrypt_chunk(ct, network_key) == pt


# ── Decrypt error cases ──────────────────────────────────────────────────


class TestDecryptErrors:
    """Decryption must raise ChunkCorruptError on any integrity violation."""

    def test_wrong_key_raises(self) -> None:
        key_a = _random_key()
        key_b = _random_key()
        ct = encrypt_chunk(b"secret", key_a)
        with pytest.raises(ChunkCorruptError):
            decrypt_chunk(ct, key_b)

    def test_tampered_ciphertext_raises(self) -> None:
        key = _random_key()
        ct = bytearray(encrypt_chunk(b"tamper me", key))
        # Flip a byte in the ciphertext region (after the 24-byte nonce)
        ct[30] ^= 0xFF
        with pytest.raises(ChunkCorruptError):
            decrypt_chunk(bytes(ct), key)

    def test_tampered_tag_raises(self) -> None:
        key = _random_key()
        ct = bytearray(encrypt_chunk(b"tag check", key))
        # Flip last byte (inside auth tag)
        ct[-1] ^= 0xFF
        with pytest.raises(ChunkCorruptError):
            decrypt_chunk(bytes(ct), key)

    def test_truncated_ciphertext_raises(self) -> None:
        key = _random_key()
        ct = encrypt_chunk(b"truncate", key)
        with pytest.raises(ChunkCorruptError):
            decrypt_chunk(ct[:10], key)  # way too short

    def test_empty_encrypted_raises(self) -> None:
        key = _random_key()
        with pytest.raises(ChunkCorruptError):
            decrypt_chunk(b"", key)


# ── HMAC chunk ID ─────────────────────────────────────────────────────────


class TestDeriveChunkId:
    """HMAC-SHA-256 based chunk addressing."""

    def test_deterministic(self) -> None:
        key = _random_key()
        data = b"same input"
        assert derive_chunk_id(data, key) == derive_chunk_id(data, key)

    def test_hex_length(self) -> None:
        cid = derive_chunk_id(b"x", _random_key())
        assert len(cid) == 64  # SHA-256 hex

    def test_key_dependence(self) -> None:
        data = b"content"
        id_a = derive_chunk_id(data, b"\x00" * 32)
        id_b = derive_chunk_id(data, b"\x01" * 32)
        assert id_a != id_b

    def test_data_dependence(self) -> None:
        key = _random_key()
        assert derive_chunk_id(b"alpha", key) != derive_chunk_id(b"bravo", key)

    def test_empty_data(self) -> None:
        cid = derive_chunk_id(b"", _random_key())
        assert isinstance(cid, str) and len(cid) == 64


# ── Integrity hash ───────────────────────────────────────────────────────


class TestComputeIntegrityHash:
    """SHA-256 integrity hash."""

    def test_deterministic(self) -> None:
        data = b"integrity"
        assert compute_integrity_hash(data) == compute_integrity_hash(data)

    def test_matches_hashlib(self) -> None:
        import hashlib

        data = b"verify"
        assert compute_integrity_hash(data) == hashlib.sha256(data).hexdigest()

    def test_different_data(self) -> None:
        assert compute_integrity_hash(b"a") != compute_integrity_hash(b"b")

    def test_empty_data(self) -> None:
        h = compute_integrity_hash(b"")
        assert len(h) == 64


# ── Network key generation ───────────────────────────────────────────────


class TestGenerateNetworkKey:
    """generate_network_key produces unique 32-byte keys."""

    def test_length(self) -> None:
        assert len(generate_network_key()) == 32

    def test_type(self) -> None:
        assert isinstance(generate_network_key(), bytes)

    def test_unique(self) -> None:
        keys = {generate_network_key() for _ in range(50)}
        assert len(keys) == 50  # all unique


# ── Keystore encrypt / decrypt ───────────────────────────────────────────


class TestKeystore:
    """Passphrase-based key wrapping with scrypt + AES-256-GCM."""

    def test_round_trip(self, test_passphrase: str) -> None:
        key = generate_network_key()
        blob = encrypt_keystore(key, test_passphrase)
        assert decrypt_keystore(blob, test_passphrase) == key

    def test_round_trip_fixture_key(
        self, network_key: bytes, test_passphrase: str
    ) -> None:
        blob = encrypt_keystore(network_key, test_passphrase)
        assert decrypt_keystore(blob, test_passphrase) == network_key

    def test_wrong_passphrase_raises(self, test_passphrase: str) -> None:
        key = generate_network_key()
        blob = encrypt_keystore(key, test_passphrase)
        with pytest.raises(NetworkKeyError):
            decrypt_keystore(blob, "wrong-passphrase")

    def test_tampered_blob_raises(self, test_passphrase: str) -> None:
        key = generate_network_key()
        blob = bytearray(encrypt_keystore(key, test_passphrase))
        blob[-1] ^= 0xFF
        with pytest.raises(NetworkKeyError):
            decrypt_keystore(bytes(blob), test_passphrase)

    def test_truncated_blob_raises(self, test_passphrase: str) -> None:
        key = generate_network_key()
        blob = encrypt_keystore(key, test_passphrase)
        with pytest.raises(NetworkKeyError):
            decrypt_keystore(blob[:10], test_passphrase)

    def test_blob_format_lengths(self, test_passphrase: str) -> None:
        """Blob = salt(16) + nonce(12) + ciphertext(32) + tag(16) = 76."""
        key = generate_network_key()
        blob = encrypt_keystore(key, test_passphrase)
        assert len(blob) == 16 + 12 + 32 + 16

    def test_different_encryptions_differ(self, test_passphrase: str) -> None:
        """Random salt + nonce means each call produces different output."""
        key = generate_network_key()
        b1 = encrypt_keystore(key, test_passphrase)
        b2 = encrypt_keystore(key, test_passphrase)
        assert b1 != b2


# ── HKDF sub-key derivation ─────────────────────────────────────────────


class TestHKDFDerivation:
    """HKDF-SHA256 sub-key derivation."""

    def test_auth_token_length(self, network_key: bytes) -> None:
        assert len(derive_auth_token(network_key)) == 32

    def test_encryption_key_length(self, network_key: bytes) -> None:
        assert len(derive_encryption_key(network_key)) == 32

    def test_hmac_key_length(self, network_key: bytes) -> None:
        assert len(derive_hmac_key(network_key)) == 32

    def test_auth_token_deterministic(self, network_key: bytes) -> None:
        assert derive_auth_token(network_key) == derive_auth_token(network_key)

    def test_encryption_key_deterministic(self, network_key: bytes) -> None:
        assert derive_encryption_key(network_key) == derive_encryption_key(network_key)

    def test_hmac_key_deterministic(self, network_key: bytes) -> None:
        assert derive_hmac_key(network_key) == derive_hmac_key(network_key)

    def test_different_info_produces_different_keys(
        self, network_key: bytes
    ) -> None:
        """Each derive_* function uses a different info string, so the
        three sub-keys must all be distinct."""
        auth = derive_auth_token(network_key)
        enc = derive_encryption_key(network_key)
        hk = derive_hmac_key(network_key)
        assert len({auth, enc, hk}) == 3

    def test_different_master_key_different_output(self) -> None:
        k1 = b"\x00" * 32
        k2 = b"\x01" * 32
        assert derive_auth_token(k1) != derive_auth_token(k2)
        assert derive_encryption_key(k1) != derive_encryption_key(k2)
        assert derive_hmac_key(k1) != derive_hmac_key(k2)


# ── Integration: full encrypt-with-derived-keys flow ─────────────────────


class TestIntegration:
    """End-to-end: derive keys from network_key, encrypt, derive chunk ID,
    verify integrity, decrypt."""

    def test_full_flow(self, network_key: bytes) -> None:
        enc_key = derive_encryption_key(network_key)
        hk = derive_hmac_key(network_key)
        data = b"integration test payload"

        # Encrypt
        ct = encrypt_chunk(data, enc_key)

        # Chunk ID (deterministic)
        cid = derive_chunk_id(data, hk)
        assert derive_chunk_id(data, hk) == cid

        # Integrity hash
        ihash = compute_integrity_hash(data)

        # Decrypt and verify
        recovered = decrypt_chunk(ct, enc_key)
        assert recovered == data
        assert compute_integrity_hash(recovered) == ihash
