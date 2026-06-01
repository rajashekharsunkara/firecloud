"""FireCloud exceptions — typed errors for all failure modes."""


class FireCloudError(Exception):
    """Base exception for all FireCloud errors."""


class NetworkKeyError(FireCloudError):
    """Wrong passphrase or corrupt keyfile."""


class NodeAuthError(FireCloudError):
    """Peer rejected authentication — invalid network token."""


class ChunkNotFoundError(FireCloudError):
    """Chunk is missing from all known nodes and cannot be recovered."""


class ChunkCorruptError(FireCloudError):
    """Chunk failed integrity check after decryption — data was tampered with."""


class InsufficientPeersError(FireCloudError):
    """Not enough online peers to satisfy the requested replication/FEC level."""


class StorageFullError(FireCloudError):
    """No node has enough storage capacity for the requested operation."""


class FileNotFoundError(FireCloudError):
    """The given file_id does not exist in the network manifest."""


class TransportError(FireCloudError):
    """Network transport failure — connection refused, timeout, or protocol error."""


class DiscoveryError(FireCloudError):
    """mDNS discovery or peer configuration error."""
