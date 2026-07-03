"""Network key handling: create/load/save the passphrase-wrapped keystore
and derive the per-purpose sub-keys."""

import hashlib
from pathlib import Path

from firecloud.crypto import (
    generate_network_key,
    encrypt_keystore,
    decrypt_keystore,
    derive_auth_token,
    derive_encryption_key,
    derive_hmac_key,
)


class Network:
    """The network-wide key plus the sub-keys derived from it.

    Holding the key is what makes a node part of the network; the keystore
    file wraps it under a passphrase for storage.
    """

    def __init__(self, key: bytes, passphrase: str | None = None) -> None:
        self.key = key
        self.passphrase = passphrase
        # First 8 bytes of SHA-256(key), hex. Safe to show in logs.
        self.network_id = hashlib.sha256(key).digest()[:8].hex()

    @classmethod
    def create(cls, passphrase: str) -> "Network":
        """New network with a fresh random key."""
        key = generate_network_key()
        return cls(key, passphrase)

    @classmethod
    def load(cls, path: Path | str, passphrase: str) -> "Network":
        """Load and unwrap a keystore file."""
        path = Path(path)
        encrypted_key = path.read_bytes()
        key = decrypt_keystore(encrypted_key, passphrase)
        return cls(key, passphrase)

    def save(self, path: Path | str, passphrase: str | None = None) -> None:
        """Write the keystore, wrapped under the given (or stored) passphrase."""
        path = Path(path)
        enc_pass = passphrase or self.passphrase
        if not enc_pass:
            raise ValueError("Passphrase required to encrypt and save network key")
        
        encrypted_key = encrypt_keystore(self.key, enc_pass)
        path.write_bytes(encrypted_key)

    @property
    def auth_token(self) -> bytes:
        """Derive the network authentication token."""
        return derive_auth_token(self.key)

    @property
    def encryption_key(self) -> bytes:
        """Derive the symmetric key used for chunk encryption."""
        return derive_encryption_key(self.key)

    @property
    def hmac_key(self) -> bytes:
        """Derive the keyed HMAC addressing key used for chunk IDs."""
        return derive_hmac_key(self.key)
