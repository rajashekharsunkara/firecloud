"""mDNS peer discovery via zeroconf, with a static peer-list fallback."""

import asyncio
import json
from pathlib import Path
import socket
from typing import Callable

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf


def get_local_ip() -> str:
    """The IPv4 address this host routes outbound traffic from."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect doesn't send anything; it just picks a source address.
        s.connect(("10.254.254.254", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class FireCloudListener(ServiceListener):
    """mDNS Service Listener for FireCloud peers."""

    def __init__(self, discovery: "LANDiscovery") -> None:
        self.discovery = discovery

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info:
            self._process_info(info)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info:
            self._process_info(info)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        peer_node_id = name.split(".")[0]
        if self.discovery.on_removed_callback:
            self.discovery.on_removed_callback(peer_node_id)

    def _process_info(self, info: ServiceInfo) -> None:
        peer_node_id = info.name.split(".")[0]
        if peer_node_id == self.discovery.node_id:
            return

        props = info.properties
        net_id_bytes = props.get(b"network_id")
        if not net_id_bytes:
            return

        net_id = net_id_bytes.decode("utf-8")
        if net_id != self.discovery.network_id:
            return  # different network

        if not info.addresses:
            return

        ip = socket.inet_ntoa(info.addresses[0])
        port = info.port

        if self.discovery.on_found_callback:
            self.discovery.on_found_callback(peer_node_id, ip, port)


class LANDiscovery:
    """Handles mDNS service registration and browsing on the LAN."""

    def __init__(self, node_id: str, network_id: str, port: int) -> None:
        self.node_id = node_id
        self.network_id = network_id
        self.port = port
        self.zeroconf: Zeroconf | None = None
        self.browser: ServiceBrowser | None = None
        self.service_info: ServiceInfo | None = None
        self.on_found_callback: Callable[[str, str, int], None] | None = None
        self.on_removed_callback: Callable[[str], None] | None = None

    async def start(self) -> None:
        """Register our mDNS service and start browsing for peers."""
        # Zeroconf init blocks, so keep it off the event loop.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_start)

    def _sync_start(self) -> None:
        self.zeroconf = Zeroconf()
        local_ip = get_local_ip()

        self.service_info = ServiceInfo(
            "_firecloud._tcp.local.",
            f"{self.node_id}._firecloud._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=self.port,
            properties={
                b"version": b"0.2.1",
                b"network_id": self.network_id.encode("utf-8"),
            },
        )
        self.zeroconf.register_service(self.service_info)

        listener = FireCloudListener(self)
        self.browser = ServiceBrowser(
            self.zeroconf, "_firecloud._tcp.local.", listener
        )

    async def stop(self) -> None:
        """Stop browsing and unregister the service."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_stop)

    def _sync_stop(self) -> None:
        if self.browser:
            self.browser.cancel()
            self.browser = None
        if self.zeroconf:
            if self.service_info:
                self.zeroconf.unregister_service(self.service_info)
                self.service_info = None
            self.zeroconf.close()
            self.zeroconf = None

    def on_peer_found(self, callback: Callable[[str, str, int], None]) -> None:
        """Set the callback for when a peer is found."""
        self.on_found_callback = callback

    def on_peer_removed(self, callback: Callable[[str], None]) -> None:
        """Set the callback for when a peer is removed."""
        self.on_removed_callback = callback


class PeerConfig:
    """Manages static peer lists loaded from and saved to a config file."""

    def load(self, path: Path | str) -> list[tuple[str, int]]:
        """Load static peer endpoints from a JSON file."""
        path = Path(path)
        if not path.exists():
            return []
        try:
            with open(path, "r") as f:
                data = json.load(f)
                return [(item[0], int(item[1])) for item in data]
        except Exception:
            return []

    def save(self, path: Path | str, peers: list[tuple[str, int]]) -> None:
        """Save a list of static peer endpoints to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [[host, port] for host, port in peers]
        with open(path, "w") as f:
            json.dump(data, f, indent=4)
