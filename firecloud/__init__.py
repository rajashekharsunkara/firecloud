"""Private, encrypted, distributed storage across your own machines.

Usage:
    from firecloud import Node, Network

    net = Network.create(passphrase="your-passphrase")
    node = Node(network=net, storage_path="~/.firecloud/storage")
    node.start()
"""

__version__ = "0.2.1"

from firecloud.network import Network
from firecloud.node import Node

__all__ = ["Node", "Network", "__version__"]
