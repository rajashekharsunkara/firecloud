"""Local network discovery using mDNS/DNS-SD.

This module implements peer discovery on local networks using:
1. mDNS (Multicast DNS) for service announcement
2. DNS-SD (DNS Service Discovery) for service browsing

Allows FireCloud nodes to discover each other on the same LAN
without any central server.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

# mDNS constants
MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
MDNS_TTL = 120  # Seconds
ANNOUNCE_INTERVAL = 30  # Seconds
STARTUP_BURST_COUNT = 3
STARTUP_BURST_INTERVAL = 1  # Seconds
BOOTSTRAP_REFRESH_INTERVAL = 30  # Seconds

# FireCloud service type
SERVICE_TYPE = "_firecloud._tcp.local."
SERVICE_NAME = "FireCloud Node"
logger = logging.getLogger(__name__)


@dataclass
class DiscoveredNode:
    """A discovered network node."""
    device_id: str
    hostname: str
    ip_address: str
    port: int
    node_type: str  # 'storage' or 'consumer'
    public_key: str
    available_storage: int  # Bytes (for storage nodes)
    protocol_version: str
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    is_online: bool = True
    
    @property
    def endpoint(self) -> str:
        """Get HTTP endpoint URL."""
        return f"http://{self.ip_address}:{self.port}"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "port": self.port,
            "node_type": self.node_type,
            "public_key": self.public_key,
            "available_storage": self.available_storage,
            "protocol_version": self.protocol_version,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "is_online": self.is_online,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveredNode":
        return cls(
            device_id=data["device_id"],
            hostname=data["hostname"],
            ip_address=data["ip_address"],
            port=data["port"],
            node_type=data["node_type"],
            public_key=data["public_key"],
            available_storage=data.get("available_storage", 0),
            protocol_version=data.get("protocol_version", "1.0"),
            first_seen=data.get("first_seen", time.time()),
            last_seen=data.get("last_seen", time.time()),
            is_online=data.get("is_online", True),
        )


def _get_local_ip() -> str:
    """Get the local IP address."""
    try:
        # Create a socket to determine local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_hostname() -> str:
    """Get the local hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


class SimpleMDNS:
    """Simple mDNS implementation for service announcement and discovery.
    
    Note: This is a simplified implementation. For production, consider
    using zeroconf or avahi libraries.
    """
    
    def __init__(
        self,
        device_id: str,
        port: int,
        node_type: str,
        public_key: str,
        available_storage: int = 0,
        protocol_version: str = "1.0",
    ) -> None:
        self.device_id = device_id
        self.port = port
        self.node_type = node_type
        self.public_key = public_key
        self.available_storage = available_storage
        self.protocol_version = protocol_version
        
        self.local_ip = _get_local_ip()
        self.hostname = _get_hostname()
        
        self._discovered: dict[str, DiscoveredNode] = {}
        self._running = False
        self._stop_event = Event()
        
        self._announce_thread: Thread | None = None
        self._listen_thread: Thread | None = None
        
        self._on_node_discovered: Callable[[DiscoveredNode], None] | None = None
        self._on_node_lost: Callable[[DiscoveredNode], None] | None = None
    
    def _create_announcement_packet(self) -> bytes:
        """Create an mDNS announcement packet."""
        # Simplified: we'll use a JSON payload in a UDP packet
        # Real mDNS would use proper DNS record format
        data = {
            "service": SERVICE_TYPE,
            "device_id": self.device_id,
            "hostname": self.hostname,
            "ip": self.local_ip,
            "port": self.port,
            "node_type": self.node_type,
            "public_key": self.public_key,
            "available_storage": self.available_storage,
            "protocol_version": self.protocol_version,
            "timestamp": time.time(),
        }
        return json.dumps(data).encode()
    
    def _parse_announcement(self, data: bytes, addr: tuple[str, int]) -> DiscoveredNode | None:
        """Parse an mDNS announcement."""
        try:
            payload = json.loads(data.decode())
            
            if payload.get("service") != SERVICE_TYPE:
                return None
            
            # Don't discover ourselves
            if payload.get("device_id") == self.device_id:
                return None
            
            return DiscoveredNode(
                device_id=payload["device_id"],
                hostname=payload.get("hostname", "unknown"),
                ip_address=payload.get("ip", addr[0]),
                port=payload["port"],
                node_type=payload["node_type"],
                public_key=payload["public_key"],
                available_storage=payload.get("available_storage", 0),
                protocol_version=payload.get("protocol_version", "1.0"),
            )
        except Exception:
            return None
    
    def _announce_loop(self) -> None:
        """Background thread for periodic announcements."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(1.0)
        announcement_count = 0

        while self._running:
            try:
                packet = self._create_announcement_packet()
                sock.sendto(packet, (MDNS_ADDR, MDNS_PORT))
            except Exception:
                pass

            announcement_count += 1
            wait_seconds = (
                STARTUP_BURST_INTERVAL
                if announcement_count < STARTUP_BURST_COUNT
                else ANNOUNCE_INTERVAL
            )

            # Wait for next announcement (faster startup burst, then steady interval)
            self._stop_event.wait(wait_seconds)
        
        sock.close()
    
    def _listen_loop(self) -> None:
        """Background thread for listening to announcements."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # Not available on all platforms
        
        sock.bind(("", MDNS_PORT))
        
        # Join multicast group
        mreq = struct.pack("4sl", socket.inet_aton(MDNS_ADDR), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(1.0)
        
        while self._running:
            try:
                data, addr = sock.recvfrom(4096)
                node = self._parse_announcement(data, addr)
                
                if node:
                    is_new = node.device_id not in self._discovered
                    self._discovered[node.device_id] = node
                    
                    if is_new and self._on_node_discovered:
                        self._on_node_discovered(node)
                        
            except socket.timeout:
                pass
            except Exception:
                pass
            
            # Check for lost nodes (no announcement in 2 minutes)
            self._check_lost_nodes()
        
        sock.close()
    
    def _check_lost_nodes(self) -> None:
        """Check for nodes that haven't announced recently."""
        now = time.time()
        lost = []
        
        for device_id, node in self._discovered.items():
            if now - node.last_seen > MDNS_TTL * 2:
                node.is_online = False
                lost.append(node)
        
        for node in lost:
            if self._on_node_lost:
                self._on_node_lost(node)
    
    def start(self) -> None:
        """Start mDNS service."""
        if self._running:
            return
        
        self._running = True
        self._stop_event.clear()
        
        self._announce_thread = Thread(target=self._announce_loop, daemon=True)
        self._listen_thread = Thread(target=self._listen_loop, daemon=True)
        
        self._announce_thread.start()
        self._listen_thread.start()
    
    def stop(self) -> None:
        """Stop mDNS service."""
        self._running = False
        self._stop_event.set()
        
        if self._announce_thread:
            self._announce_thread.join(timeout=2)
        if self._listen_thread:
            self._listen_thread.join(timeout=2)
    
    def get_discovered_nodes(self, online_only: bool = True) -> list[DiscoveredNode]:
        """Get list of discovered nodes."""
        nodes = list(self._discovered.values())
        if online_only:
            nodes = [n for n in nodes if n.is_online]
        return nodes
    
    def get_storage_nodes(self) -> list[DiscoveredNode]:
        """Get discovered storage nodes."""
        return [
            n for n in self._discovered.values()
            if n.is_online and n.node_type == "storage"
        ]
    
    def get_node(self, device_id: str) -> DiscoveredNode | None:
        """Get a specific node by ID."""
        return self._discovered.get(device_id)
    
    def on_node_discovered(self, callback: Callable[[DiscoveredNode], None]) -> None:
        """Set callback for when a new node is discovered."""
        self._on_node_discovered = callback
    
    def on_node_lost(self, callback: Callable[[DiscoveredNode], None]) -> None:
        """Set callback for when a node goes offline."""
        self._on_node_lost = callback
    
    def force_announce(self) -> None:
        """Force an immediate announcement."""
        if self._running:
            self._stop_event.set()
            time.sleep(0.1)
            self._stop_event.clear()


class NetworkManager:
    """Manages network discovery and peer connections."""
    
    def __init__(
        self,
        device_id: str,
        port: int,
        node_type: str,
        public_key: str,
        available_storage: int = 0,
        bootstrap_peers: list[str] | None = None,
        bootstrap_refresh_interval: int = BOOTSTRAP_REFRESH_INTERVAL,
    ) -> None:
        self.device_id = device_id
        self.port = port
        self.node_type = node_type
        self.public_key = public_key
        self.available_storage = available_storage
        
        self.mdns = SimpleMDNS(
            device_id=device_id,
            port=port,
            node_type=node_type,
            public_key=public_key,
            available_storage=available_storage,
        )
        self.bootstrap_peers = self._normalize_bootstrap_peers(bootstrap_peers or [])
        self.bootstrap_refresh_interval = max(5, int(bootstrap_refresh_interval))
        
        # Connection pool
        self._connections: dict[str, Any] = {}
        self._peer_lock = Lock()
        self._refresh_thread: Thread | None = None
        self._refresh_stop_event = Event()
        self._last_refresh = 0.0
        self._last_refresh_error: str | None = None
        
        # Event handlers
        self._handlers: dict[str, list[Callable]] = {
            "node_discovered": [],
            "node_lost": [],
            "message_received": [],
        }
        
        # Setup mDNS callbacks
        self.mdns.on_node_discovered(self._handle_node_discovered)
        self.mdns.on_node_lost(self._handle_node_lost)

    def _normalize_bootstrap_peers(self, bootstrap_peers: list[str]) -> list[str]:
        normalized: list[str] = []
        local_endpoint = f"http://{self.mdns.local_ip}:{self.port}"
        for endpoint in bootstrap_peers:
            trimmed = endpoint.strip().rstrip("/")
            if not trimmed:
                continue
            parsed = urlparse(trimmed)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            normalized.append(trimmed)
        deduped = list(dict.fromkeys(normalized))
        return [peer for peer in deduped if peer != local_endpoint]

    @staticmethod
    def _sanitize_peer_payload(payload: dict[str, Any]) -> DiscoveredNode | None:
        try:
            endpoint_value = payload.get("endpoint")
            if endpoint_value is not None:
                endpoint = str(endpoint_value).strip().rstrip("/")
                parsed = urlparse(endpoint)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    return None
                host = parsed.hostname or ""
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
            else:
                host = str(payload.get("ip_address", "")).strip()
                if not host:
                    return None
                port = int(payload.get("port", 0))
                if port <= 0:
                    return None
            if not host:
                return None
            return DiscoveredNode(
                device_id=str(payload["device_id"]).strip(),
                hostname=str(payload.get("hostname") or host),
                ip_address=host,
                port=port,
                node_type=str(payload.get("node_type") or "consumer"),
                public_key=str(payload.get("public_key") or ""),
                available_storage=int(payload.get("available_storage", 0)),
                protocol_version=str(payload.get("protocol_version") or "1.0"),
                first_seen=float(payload.get("first_seen", time.time())),
                last_seen=float(payload.get("last_seen", time.time())),
                is_online=bool(payload.get("is_online", True)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _fetch_peer_list_from_bootstrap(self, endpoint: str) -> list[DiscoveredNode]:
        url = f"{endpoint}/network/peers"
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            raise RuntimeError(str(exc)) from exc
        if response.status_code != 200:
            raise RuntimeError(f"status={response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("invalid json response") from exc
        if not isinstance(payload, list):
            raise RuntimeError("invalid response payload")

        discovered: list[DiscoveredNode] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            node = self._sanitize_peer_payload(item)
            if node is None or node.device_id == self.device_id:
                continue
            discovered.append(node)
        return discovered

    def _merge_bootstrap_peers(self, peers: list[DiscoveredNode]) -> int:
        imported = 0
        now = time.time()
        with self._peer_lock:
            for peer in peers:
                if peer.device_id == self.device_id:
                    continue
                peer.last_seen = now
                peer.is_online = True
                is_new = peer.device_id not in self.mdns._discovered
                self.mdns._discovered[peer.device_id] = peer
                imported += 1
                if is_new and self.mdns._on_node_discovered:
                    self.mdns._on_node_discovered(peer)
        return imported

    def refresh_peers(self) -> dict[str, Any]:
        if not self.bootstrap_peers:
            self._last_refresh = time.time()
            self._last_refresh_error = None
            return {
                "bootstrap_peers": [],
                "attempted": 0,
                "successful": 0,
                "imported": 0,
                "error": None,
            }

        imported = 0
        successful = 0
        errors: list[str] = []
        attempted_endpoints: list[str] = []

        for endpoint in self.bootstrap_peers:
            attempted_endpoints.append(endpoint)
            try:
                peers = self._fetch_peer_list_from_bootstrap(endpoint)
                imported += self._merge_bootstrap_peers(peers)
                successful += 1
            except RuntimeError as exc:
                errors.append(f"{endpoint}: {exc}")
                continue

        self._last_refresh = time.time()
        self._last_refresh_error = "; ".join(errors) if errors else None
        return {
            "bootstrap_peers": attempted_endpoints,
            "attempted": len(attempted_endpoints),
            "successful": successful,
            "imported": imported,
            "error": self._last_refresh_error,
        }

    def _refresh_loop(self) -> None:
        while not self._refresh_stop_event.is_set():
            result = self.refresh_peers()
            if result["error"]:
                logger.warning("Bootstrap refresh partial failure: %s", result["error"])
            self._refresh_stop_event.wait(self.bootstrap_refresh_interval)

    def bootstrap_status(self) -> dict[str, Any]:
        return {
            "bootstrap_peers": list(self.bootstrap_peers),
            "last_refresh": (
                datetime.fromtimestamp(self._last_refresh, tz=timezone.utc).isoformat()
                if self._last_refresh > 0
                else None
            ),
            "last_refresh_error": self._last_refresh_error,
            "refresh_interval_seconds": self.bootstrap_refresh_interval,
        }
    
    def _handle_node_discovered(self, node: DiscoveredNode) -> None:
        """Handle newly discovered node."""
        for handler in self._handlers["node_discovered"]:
            try:
                handler(node)
            except Exception:
                pass
    
    def _handle_node_lost(self, node: DiscoveredNode) -> None:
        """Handle lost node."""
        # Close any connections
        if node.device_id in self._connections:
            del self._connections[node.device_id]
        
        for handler in self._handlers["node_lost"]:
            try:
                handler(node)
            except Exception:
                pass
    
    def start(self) -> None:
        """Start network manager."""
        self.mdns.start()
        self._refresh_stop_event.clear()
        if self.bootstrap_peers:
            self.refresh_peers()
            self._refresh_thread = Thread(target=self._refresh_loop, daemon=True)
            self._refresh_thread.start()
    
    def stop(self) -> None:
        """Stop network manager."""
        self._refresh_stop_event.set()
        if self._refresh_thread:
            self._refresh_thread.join(timeout=2)
            self._refresh_thread = None
        self.mdns.stop()
        self._connections.clear()
    
    def on(self, event: str, handler: Callable) -> None:
        """Register an event handler."""
        if event in self._handlers:
            self._handlers[event].append(handler)
    
    def get_peers(self) -> list[DiscoveredNode]:
        """Get all discovered peers."""
        return self.mdns.get_discovered_nodes()
    
    def get_storage_peers(self) -> list[DiscoveredNode]:
        """Get storage node peers."""
        return self.mdns.get_storage_nodes()
    
    def get_peer(self, device_id: str) -> DiscoveredNode | None:
        """Get a specific peer."""
        return self.mdns.get_node(device_id)
    
    def update_storage(self, available_storage: int) -> None:
        """Update our available storage (for storage nodes)."""
        self.available_storage = available_storage
        self.mdns.available_storage = available_storage
        self.mdns.force_announce()
    
    def update_node_type(self, node_type: str) -> None:
        """Update our node type."""
        self.node_type = node_type
        self.mdns.node_type = node_type
        self.mdns.force_announce()
    
    def get_network_stats(self) -> dict[str, Any]:
        """Get network statistics."""
        peers = self.mdns.get_discovered_nodes(online_only=False)
        online = [p for p in peers if p.is_online]
        storage_nodes = [p for p in online if p.node_type == "storage"]
        
        total_storage = sum(n.available_storage for n in storage_nodes)
        
        return {
            "local_ip": self.mdns.local_ip,
            "local_port": self.port,
            "device_id": self.device_id,
            "node_type": self.node_type,
            "total_peers_seen": len(peers),
            "online_peers": len(online),
            "storage_nodes": len(storage_nodes),
            "total_network_storage": total_storage,
        }


# ============================================================================
# Async version using asyncio
# ============================================================================

class AsyncNetworkManager:
    """Async version of NetworkManager using asyncio."""
    
    def __init__(
        self,
        device_id: str,
        port: int,
        node_type: str,
        public_key: str,
        available_storage: int = 0,
    ) -> None:
        self.device_id = device_id
        self.port = port
        self.node_type = node_type
        self.public_key = public_key
        self.available_storage = available_storage
        
        self.local_ip = _get_local_ip()
        self.hostname = _get_hostname()
        
        self._discovered: dict[str, DiscoveredNode] = {}
        self._running = False
        self._announce_task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None
        
        self._on_node_discovered: Callable[[DiscoveredNode], None] | None = None
        self._on_node_lost: Callable[[DiscoveredNode], None] | None = None
    
    def _create_announcement(self) -> bytes:
        """Create announcement data."""
        data = {
            "service": SERVICE_TYPE,
            "device_id": self.device_id,
            "hostname": self.hostname,
            "ip": self.local_ip,
            "port": self.port,
            "node_type": self.node_type,
            "public_key": self.public_key,
            "available_storage": self.available_storage,
            "protocol_version": "1.0",
            "timestamp": time.time(),
        }
        return json.dumps(data).encode()
    
    async def _announce_loop(self) -> None:
        """Async announcement loop."""
        transport = None
        try:
            loop = asyncio.get_event_loop()
            
            # Create UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.setblocking(False)
            announcement_count = 0

            while self._running:
                try:
                    packet = self._create_announcement()
                    sock.sendto(packet, (MDNS_ADDR, MDNS_PORT))
                except Exception:
                    pass

                announcement_count += 1
                wait_seconds = (
                    STARTUP_BURST_INTERVAL
                    if announcement_count < STARTUP_BURST_COUNT
                    else ANNOUNCE_INTERVAL
                )
                await asyncio.sleep(wait_seconds)
        finally:
            if transport:
                transport.close()
    
    async def _listen_loop(self) -> None:
        """Async listening loop."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError:
                pass
            
            sock.bind(("", MDNS_PORT))
            sock.setblocking(False)
            
            # Join multicast
            mreq = struct.pack("4sl", socket.inet_aton(MDNS_ADDR), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            
            loop = asyncio.get_event_loop()
            
            while self._running:
                try:
                    data, addr = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: sock.recvfrom(4096)),
                        timeout=1.0
                    )
                    
                    await self._handle_announcement(data, addr)
                    
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    await asyncio.sleep(0.1)
                
                # Check for lost nodes
                await self._check_lost_nodes()
        finally:
            sock.close()
    
    async def _handle_announcement(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle received announcement."""
        try:
            payload = json.loads(data.decode())
            
            if payload.get("service") != SERVICE_TYPE:
                return
            
            if payload.get("device_id") == self.device_id:
                return
            
            node = DiscoveredNode(
                device_id=payload["device_id"],
                hostname=payload.get("hostname", "unknown"),
                ip_address=payload.get("ip", addr[0]),
                port=payload["port"],
                node_type=payload["node_type"],
                public_key=payload["public_key"],
                available_storage=payload.get("available_storage", 0),
                protocol_version=payload.get("protocol_version", "1.0"),
            )
            
            is_new = node.device_id not in self._discovered
            self._discovered[node.device_id] = node
            
            if is_new and self._on_node_discovered:
                self._on_node_discovered(node)
                
        except Exception:
            pass
    
    async def _check_lost_nodes(self) -> None:
        """Check for lost nodes."""
        now = time.time()
        
        for device_id, node in list(self._discovered.items()):
            if now - node.last_seen > MDNS_TTL * 2:
                if node.is_online:
                    node.is_online = False
                    if self._on_node_lost:
                        self._on_node_lost(node)
    
    async def start(self) -> None:
        """Start async network manager."""
        if self._running:
            return
        
        self._running = True
        self._announce_task = asyncio.create_task(self._announce_loop())
        self._listen_task = asyncio.create_task(self._listen_loop())
    
    async def stop(self) -> None:
        """Stop async network manager."""
        self._running = False
        
        if self._announce_task:
            self._announce_task.cancel()
            try:
                await self._announce_task
            except asyncio.CancelledError:
                pass
        
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
    
    def get_peers(self) -> list[DiscoveredNode]:
        """Get discovered peers."""
        return [n for n in self._discovered.values() if n.is_online]
    
    def on_node_discovered(self, callback: Callable[[DiscoveredNode], None]) -> None:
        """Set callback for node discovery."""
        self._on_node_discovered = callback
    
    def on_node_lost(self, callback: Callable[[DiscoveredNode], None]) -> None:
        """Set callback for node loss."""
        self._on_node_lost = callback
