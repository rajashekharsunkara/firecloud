"""FireCloud Network Key Management.

Handles network key generation, loading/saving passphrase-wrapped keystores,
and accessing derived sub-keys for encryption, HMAC, and authentication.
"""

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
    """Manages the network-wide key and derives sub-keys.

    The network key is protected on disk using a passphrase-derived key.
    """

    def __init__(self, key: bytes, passphrase: str | None = None) -> None:
        """Initialize the network with a key.

        Args:
            key: The 32-byte network key.
            passphrase: Optional passphrase associated with this network key.
        """
        self.key = key
        self.passphrase = passphrase
        # network_id is the first 8 bytes of SHA-256 of the network key, hex-encoded
        self.network_id = hashlib.sha256(key).digest()[:8].hex()

    @classmethod
    def create(cls, passphrase: str) -> "Network":
        """Create a new network with a fresh cryptographically random key.

        Args:
            passphrase: The passphrase to protect the new network key.
        """
        key = generate_network_key()
        return cls(key, passphrase)

    @classmethod
    def load(cls, path: Path | str, passphrase: str) -> "Network":
        """Load a network key from a passphrase-protected keystore file.

        Args:
            path: Path to the keystore file.
            passphrase: Passphrase to decrypt the keystore.
        """
        path = Path(path)
        encrypted_key = path.read_bytes()
        key = decrypt_keystore(encrypted_key, passphrase)
        return cls(key, passphrase)

    def save(self, path: Path | str, passphrase: str | None = None) -> None:
        """Save the network key to a passphrase-protected keystore file.

        Args:
            path: Path to write the keystore file.
            passphrase: Optional passphrase to use. Defaults to the passphrase
                associated with this instance if not provided.
        """
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
