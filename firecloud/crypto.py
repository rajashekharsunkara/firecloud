"""FireCloud cryptographic engine.

Provides chunk-level authenticated encryption (XChaCha20-Poly1305),
convergent chunk addressing (HMAC-SHA-256), integrity verification
(SHA-256), passphrase-protected keystore (scrypt + AES-256-GCM),
and HKDF-based sub-key derivation.
"""

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
    """Encrypt *plaintext* with XChaCha20-Poly1305.

    Args:
        plaintext: Arbitrary-length data (may be empty).
        key: 32-byte symmetric key.

    Returns:
        ``nonce (24 B) || ciphertext || auth_tag (16 B)``
    """
    nonce = get_random_bytes(_XCHACHA_NONCE_LEN)
    cipher = ChaCha20_Poly1305.new(key=key, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return nonce + ciphertext + tag


def decrypt_chunk(encrypted: bytes, key: bytes) -> bytes:
    """Decrypt data produced by :func:`encrypt_chunk`.

    Args:
        encrypted: ``nonce (24 B) || ciphertext || auth_tag (16 B)``
        key: 32-byte symmetric key (must match encryption key).

    Returns:
        The original plaintext bytes.

    Raises:
        ChunkCorruptError: Authentication tag verification failed
            (wrong key, truncated data, or tampered ciphertext).
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
            "Chunk authentication failed — data may be corrupt or the key is wrong"
        ) from exc

    return plaintext


# ---------------------------------------------------------------------------
# Chunk addressing & integrity
# ---------------------------------------------------------------------------


def derive_chunk_id(plaintext: bytes, hmac_key: bytes) -> str:
    """Compute a keyed chunk address via HMAC-SHA-256.

    The result is deterministic for the same ``(plaintext, hmac_key)`` pair
    but unpredictable without knowledge of *hmac_key*, preventing offline
    content guessing attacks.

    Args:
        plaintext: The chunk data.
        hmac_key: 32-byte HMAC key derived from the network key.

    Returns:
        Hex-encoded HMAC-SHA-256 digest (64 hex chars).
    """
    return hmac.new(hmac_key, plaintext, hashlib.sha256).hexdigest()


def compute_integrity_hash(plaintext: bytes) -> str:
    """Compute a SHA-256 digest for post-decryption verification.

    Args:
        plaintext: The chunk data.

    Returns:
        Hex-encoded SHA-256 digest (64 hex chars).
    """
    return hashlib.sha256(plaintext).hexdigest()


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def generate_network_key() -> bytes:
    """Generate a fresh 32-byte (256-bit) random network key.

    Returns:
        Cryptographically-random 32-byte key.
    """
    return get_random_bytes(_KEY_LEN)


# ---------------------------------------------------------------------------
# Keystore (passphrase-protected key wrapping)
# ---------------------------------------------------------------------------


def encrypt_keystore(key: bytes, passphrase: str) -> bytes:
    """Encrypt a network key under a passphrase using scrypt + AES-256-GCM.

    Wire format::

        salt (16 B) || nonce (12 B) || ciphertext || tag (16 B)

    Args:
        key: The 32-byte network key to protect.
        passphrase: User-supplied passphrase.

    Returns:
        The encrypted keystore blob.
    """
    salt = get_random_bytes(_SCRYPT_SALT_LEN)
    wrapping_key = _derive_scrypt_key(passphrase, salt)

    nonce = get_random_bytes(_AES_GCM_NONCE_LEN)
    cipher = AES.new(wrapping_key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(key)

    return salt + nonce + ciphertext + tag


def decrypt_keystore(encrypted: bytes, passphrase: str) -> bytes:
    """Decrypt a keystore blob produced by :func:`encrypt_keystore`.

    Args:
        encrypted: The encrypted keystore blob.
        passphrase: User-supplied passphrase.

    Returns:
        The 32-byte network key.

    Raises:
        NetworkKeyError: Wrong passphrase or corrupt keystore data.
    """
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
            "Failed to decrypt keystore — wrong passphrase or corrupt data"
        ) from exc

    return plaintext


# ---------------------------------------------------------------------------
# HKDF sub-key derivation
# ---------------------------------------------------------------------------


def derive_auth_token(key: bytes) -> bytes:
    """Derive a 32-byte authentication token from *key* via HKDF-SHA256.

    Args:
        key: 32-byte network key (input keying material).

    Returns:
        32-byte derived token.
    """
    return _hkdf_derive(key, info=b"firecloud-auth-token")


def derive_encryption_key(key: bytes) -> bytes:
    """Derive a 32-byte encryption sub-key from *key* via HKDF-SHA256.

    Args:
        key: 32-byte network key (input keying material).

    Returns:
        32-byte derived key.
    """
    return _hkdf_derive(key, info=b"firecloud-encryption-key")


def derive_hmac_key(key: bytes) -> bytes:
    """Derive a 32-byte HMAC sub-key from *key* via HKDF-SHA256.

    Args:
        key: 32-byte network key (input keying material).

    Returns:
        32-byte derived key.
    """
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
