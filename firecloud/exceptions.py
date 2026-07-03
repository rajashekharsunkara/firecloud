"""Exception types."""


class FireCloudError(Exception):
    """Base class for all FireCloud errors."""


class NetworkKeyError(FireCloudError):
    """Wrong passphrase or corrupt keyfile."""


class NodeAuthError(FireCloudError):
    """Peer rejected the auth token."""


class ChunkNotFoundError(FireCloudError):
    """Chunk missing from every known node."""


class ChunkCorruptError(FireCloudError):
    """Integrity check failed after decryption."""


class InsufficientPeersError(FireCloudError):
    """Not enough online peers for the requested replication/FEC level."""


class StorageFullError(FireCloudError):
    """No node has enough capacity for the operation."""


class FileNotFoundError(FireCloudError):
    """file_id not present in the network manifest."""


class TransportError(FireCloudError):
    """Connection refused, timeout, or protocol error."""


class DiscoveryError(FireCloudError):
    """mDNS discovery or peer configuration error."""
