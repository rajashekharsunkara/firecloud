from __future__ import annotations

import secrets

from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_KEYBYTES,
    crypto_aead_xchacha20poly1305_ietf_NPUBBYTES,
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
)
from nacl.exceptions import CryptoError

KEY_SIZE = crypto_aead_xchacha20poly1305_ietf_KEYBYTES
NONCE_SIZE = crypto_aead_xchacha20poly1305_ietf_NPUBBYTES
TAG_SIZE = 16
ENCRYPTION_OVERHEAD = NONCE_SIZE + TAG_SIZE


def generate_key() -> bytes:
    return secrets.token_bytes(KEY_SIZE)


def encrypt_xchacha20poly1305(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    if len(key) != KEY_SIZE:
        raise ValueError(f"Invalid key length; expected {KEY_SIZE} bytes")
    nonce = secrets.token_bytes(NONCE_SIZE)
    ciphertext = crypto_aead_xchacha20poly1305_ietf_encrypt(plaintext, aad, nonce, key)
    return nonce + ciphertext


def decrypt_xchacha20poly1305(key: bytes, payload: bytes, aad: bytes = b"") -> bytes:
    if len(key) != KEY_SIZE:
        raise ValueError(f"Invalid key length; expected {KEY_SIZE} bytes")
    if len(payload) <= NONCE_SIZE:
        raise ValueError("Encrypted payload too short")
    nonce = payload[:NONCE_SIZE]
    ciphertext = payload[NONCE_SIZE:]
    try:
        return crypto_aead_xchacha20poly1305_ietf_decrypt(ciphertext, aad, nonce, key)
    except CryptoError as exc:
        raise ValueError("Decryption failed or payload was tampered") from exc
