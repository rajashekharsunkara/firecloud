"""Crypto primitives: XChaCha20-Poly1305 chunk encryption, HMAC-SHA-256
chunk addressing, the scrypt/AES-GCM keystore, and HKDF sub-keys."""

import hashlib
import hmac

from Crypto.Cipher import AES, ChaCha20_Poly1305
from Crypto.Random import get_random_bytes
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from firecloud.exceptions import ChunkCorruptError, NetworkKeyError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_XCHACHA_NONCE_LEN = 24  # XChaCha20-Poly1305 nonce
_AES_GCM_NONCE_LEN = 12  # AES-256-GCM nonce
_SCRYPT_SALT_LEN = 16
_KEY_LEN = 32  # 256-bit keys throughout

# Scrypt cost parameters (interactive-grade, fast for dev/test)
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1

# ---------------------------------------------------------------------------
# Chunk encryption / decryption
# ---------------------------------------------------------------------------


def encrypt_chunk(plaintext: bytes, key: bytes) -> bytes:
    """XChaCha20-Poly1305. Output: nonce (24B) || ciphertext || tag (16B)."""
    nonce = get_random_bytes(_XCHACHA_NONCE_LEN)
    cipher = ChaCha20_Poly1305.new(key=key, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return nonce + ciphertext + tag


def decrypt_chunk(encrypted: bytes, key: bytes) -> bytes:
    """Inverse of encrypt_chunk.

    Raises ChunkCorruptError when the tag check fails: wrong key,
    truncation, or tampering.
    """
    if len(encrypted) < _XCHACHA_NONCE_LEN + 16:
        raise ChunkCorruptError(
            "Encrypted payload too short to contain nonce + auth tag"
        )

    nonce = encrypted[:_XCHACHA_NONCE_LEN]
    ciphertext_and_tag = encrypted[_XCHACHA_NONCE_LEN:]
    ciphertext = ciphertext_and_tag[:-16]
    tag = ciphertext_and_tag[-16:]

    try:
        cipher = ChaCha20_Poly1305.new(key=key, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    except (ValueError, KeyError) as exc:
        raise ChunkCorruptError(
            "Chunk authentication failed: corrupt data or wrong key"
        ) from exc

    return plaintext


# ---------------------------------------------------------------------------
# Chunk addressing & integrity
# ---------------------------------------------------------------------------


def derive_chunk_id(plaintext: bytes, hmac_key: bytes) -> str:
    """Keyed chunk address: HMAC-SHA-256 hex digest.

    Deterministic per (plaintext, key) but useless to anyone without the
    key, so an outsider can't confirm content by hashing guesses.
    """
    return hmac.new(hmac_key, plaintext, hashlib.sha256).hexdigest()


def compute_integrity_hash(plaintext: bytes) -> str:
    """SHA-256 digest for post-decryption verification."""
    return hashlib.sha256(plaintext).hexdigest()


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def generate_network_key() -> bytes:
    """Fresh random 32-byte network key."""
    return get_random_bytes(_KEY_LEN)


# ---------------------------------------------------------------------------
# Keystore (passphrase-protected key wrapping)
# ---------------------------------------------------------------------------


def encrypt_keystore(key: bytes, passphrase: str) -> bytes:
    """Wrap the network key under a passphrase (scrypt + AES-256-GCM).

    Blob layout: salt (16B) || nonce (12B) || ciphertext || tag (16B).
    """
    salt = get_random_bytes(_SCRYPT_SALT_LEN)
    wrapping_key = _derive_scrypt_key(passphrase, salt)

    nonce = get_random_bytes(_AES_GCM_NONCE_LEN)
    cipher = AES.new(wrapping_key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(key)

    return salt + nonce + ciphertext + tag


def decrypt_keystore(encrypted: bytes, passphrase: str) -> bytes:
    """Unwrap a keystore blob; NetworkKeyError on bad passphrase or data."""
    min_len = _SCRYPT_SALT_LEN + _AES_GCM_NONCE_LEN + 16  # salt+nonce+tag
    if len(encrypted) < min_len:
        raise NetworkKeyError("Keystore data is too short or corrupt")

    salt = encrypted[:_SCRYPT_SALT_LEN]
    nonce = encrypted[_SCRYPT_SALT_LEN : _SCRYPT_SALT_LEN + _AES_GCM_NONCE_LEN]
    ciphertext_and_tag = encrypted[_SCRYPT_SALT_LEN + _AES_GCM_NONCE_LEN :]
    ciphertext = ciphertext_and_tag[:-16]
    tag = ciphertext_and_tag[-16:]

    wrapping_key = _derive_scrypt_key(passphrase, salt)

    try:
        cipher = AES.new(wrapping_key, AES.MODE_GCM, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    except (ValueError, KeyError) as exc:
        raise NetworkKeyError(
            "Failed to decrypt keystore: wrong passphrase or corrupt data"
        ) from exc

    return plaintext


# ---------------------------------------------------------------------------
# HKDF sub-key derivation
# ---------------------------------------------------------------------------


def derive_auth_token(key: bytes) -> bytes:
    """32-byte auth token from HKDF-SHA256."""
    return _hkdf_derive(key, info=b"firecloud-auth-token")


def derive_encryption_key(key: bytes) -> bytes:
    """32-byte encryption sub-key from HKDF-SHA256."""
    return _hkdf_derive(key, info=b"firecloud-encryption-key")


def derive_hmac_key(key: bytes) -> bytes:
    """32-byte HMAC sub-key from HKDF-SHA256."""
    return _hkdf_derive(key, info=b"firecloud-hmac-key")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _hkdf_derive(ikm: bytes, *, info: bytes, length: int = _KEY_LEN) -> bytes:
    """Run HKDF-SHA256 (no salt) with the given *info* label."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=info,
    )
    return hkdf.derive(ikm)


def _derive_scrypt_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte wrapping key from *passphrase* using scrypt."""
    kdf = Scrypt(
        salt=salt,
        length=_KEY_LEN,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
    )
    return kdf.derive(passphrase.encode("utf-8"))
