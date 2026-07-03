"""Peer-to-peer transport: asyncio TCP over TLS with a small binary protocol.

Covers the auth handshake, chunk transfer, manifest sync, peer gossip, and
heartbeats.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import hmac
import json
import logging
from pathlib import Path
import ssl
import struct
import tempfile

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from firecloud.exceptions import TransportError, NodeAuthError, ChunkNotFoundError

logger = logging.getLogger("firecloud.transport")

# Protocol constants
MSG_AUTH = 0x01
MSG_AUTH_OK = 0x02
MSG_STORE_CHUNK = 0x10
MSG_RETRIEVE_CHUNK = 0x11
MSG_CHUNK_DATA = 0x12
MSG_CHUNK_NOT_FOUND = 0x13
MSG_STORE_OK = 0x14
MSG_STORE_FAIL = 0x15
MSG_HAS_CHUNK = 0x16
MSG_HAS_CHUNK_RESP = 0x17
MSG_SYNC_MANIFEST = 0x20
MSG_MANIFEST_REQ = 0x21
MSG_MANIFEST_RESP = 0x22
MSG_HEARTBEAT = 0x30
MSG_PEER_LIST = 0x40
MSG_PEER_REQ = 0x41
MSG_PEER_RESP = 0x42

# Chunks top out around 64 KiB; only manifest syncs get into the megabytes.
# The cap stops a corrupt or hostile length prefix from allocating gigabytes.
MAX_FRAME_SIZE = 64 * 1024 * 1024

HANDSHAKE_TIMEOUT = 10.0
REQUEST_TIMEOUT = 30.0

# Chunk IDs are HMAC-SHA-256 hex digests, 64 bytes on the wire.
_CHUNK_ID_LEN = 64

# ---------------------------------------------------------------------------
# TLS & Certificate Helpers
# ---------------------------------------------------------------------------


def generate_self_signed_cert() -> tuple[bytes, bytes]:
    """Generate a self-signed cert and key for TLS node authentication."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "firecloud-node"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .sign(private_key, hashes.SHA256())
    )
    cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
    key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_bytes, key_bytes


def get_ssl_context(is_server: bool, node_dir: Path | None = None) -> ssl.SSLContext:
    """Get or create the SSLContext for secure connections."""
    if node_dir is None:
        node_dir = Path(tempfile.gettempdir()) / "firecloud_ssl"
    node_dir.mkdir(parents=True, exist_ok=True)
    
    cert_path = node_dir / "node.crt"
    key_path = node_dir / "node.key"
    
    if not cert_path.exists() or not key_path.exists():
        cert_bytes, key_bytes = generate_self_signed_cert()
        cert_path.write_bytes(cert_bytes)
        key_path.write_bytes(key_bytes)
        
    if is_server:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    else:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
    return context


# ---------------------------------------------------------------------------
# Binary framing helpers
# ---------------------------------------------------------------------------


async def read_msg(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read a structured message from the stream.

    Format: [4 bytes length][1 byte type][payload]
    """
    try:
        header = await reader.readexactly(4)
        length = struct.unpack("!I", header)[0]
        if length > MAX_FRAME_SIZE:
            raise TransportError(
                f"Frame length {length} exceeds maximum of {MAX_FRAME_SIZE} bytes"
            )
        msg_type_byte = await reader.readexactly(1)
        msg_type = msg_type_byte[0]
        payload = await reader.readexactly(length)
        return msg_type, payload
    except TransportError:
        raise
    except asyncio.IncompleteReadError as exc:
        raise TransportError("Connection closed prematurely during read") from exc
    except Exception as exc:
        raise TransportError(f"Error reading message from socket: {exc}") from exc


async def write_msg(writer: asyncio.StreamWriter, msg_type: int, payload: bytes) -> None:
    """Write a structured message to the stream."""
    try:
        header = struct.pack("!IB", len(payload), msg_type)
        writer.write(header + payload)
        await writer.drain()
    except Exception as exc:
        raise TransportError(f"Error writing message to socket: {exc}") from exc


# ---------------------------------------------------------------------------
# Peer Connection Handler
# ---------------------------------------------------------------------------


class PeerConnection:
    """Handles an active bidirectional connection with a remote peer."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer_node_id: str,
        node,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.peer_node_id = peer_node_id
        self.node = node
        self.write_lock = asyncio.Lock()
        self.retrieve_lock = asyncio.Lock()
        # In-flight requests keyed by (kind, chunk_id), so a late or
        # duplicate response can't land on the wrong request.
        self._pending: dict[tuple[str, str], asyncio.Future] = {}
        self._closed = False
        self.last_seen = datetime.now(timezone.utc)
        self.read_task = asyncio.create_task(self._read_loop())

    async def send_message(self, msg_type: int, payload: bytes) -> None:
        """Send a message to the peer."""
        async with self.write_lock:
            await write_msg(self.writer, msg_type, payload)

    async def _request(
        self,
        kind: str,
        msg_type: int,
        chunk_id: str,
        payload: bytes,
        timeout: float,
    ):
        """Send a request and wait for the matching response.

        Returns None when the peer doesn't answer in time or the connection
        drops; callers treat that as a miss and try other peers.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        key = (kind, chunk_id)
        self._pending[key] = future
        try:
            await self.send_message(msg_type, payload)
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            logger.warning(
                f"Peer {self.peer_node_id} did not answer {kind} for chunk "
                f"{chunk_id[:16]}... within {timeout}s"
            )
            return None
        except (TransportError, OSError) as exc:
            logger.debug(
                f"{kind} for chunk {chunk_id[:16]}... failed on peer "
                f"{self.peer_node_id}: {exc}"
            )
            return None
        finally:
            self._pending.pop(key, None)

    async def retrieve_chunk(
        self, chunk_id: str, timeout: float = REQUEST_TIMEOUT
    ) -> bytes | None:
        """Fetch a chunk from this peer; None if missing or unresponsive."""
        async with self.retrieve_lock:
            return await self._request(
                "retrieve",
                MSG_RETRIEVE_CHUNK,
                chunk_id,
                chunk_id.encode("utf-8"),
                timeout,
            )

    async def store_chunk(
        self, chunk_id: str, data: bytes, timeout: float = REQUEST_TIMEOUT
    ) -> bool:
        """Store a chunk on this peer.

        True only once the peer acks the write, so placement records stay
        accurate.
        """
        result = await self._request(
            "store",
            MSG_STORE_CHUNK,
            chunk_id,
            chunk_id.encode("utf-8") + data,
            timeout,
        )
        return bool(result)

    async def has_chunk(self, chunk_id: str, timeout: float = 10.0) -> bool:
        """Ask the peer whether it holds a chunk, without transferring it."""
        result = await self._request(
            "has",
            MSG_HAS_CHUNK,
            chunk_id,
            chunk_id.encode("utf-8"),
            timeout,
        )
        return bool(result)

    async def request_manifest(
        self, timeout: float = REQUEST_TIMEOUT
    ) -> list | None:
        """Ask the peer for its manifest entries (list of dicts, or None).

        Lets a freshly connected node pick up the file catalog right away
        instead of waiting for the next periodic push.
        """
        return await self._request(
            "manifest", MSG_MANIFEST_REQ, "", b"", timeout
        )

    async def request_peers(self, timeout: float = 10.0) -> list | None:
        """Ask the peer which node addresses it knows."""
        return await self._request(
            "peers", MSG_PEER_REQ, "", b"", timeout
        )

    def _resolve(self, kind: str, chunk_id: str, value) -> None:
        """Resolve a pending request future, ignoring stale responses."""
        future = self._pending.get((kind, chunk_id))
        if future is not None and not future.done():
            future.set_result(value)

    async def _read_loop(self) -> None:
        """Background loop that processes incoming messages from the peer."""
        try:
            while True:
                msg_type, payload = await read_msg(self.reader)
                self.last_seen = datetime.now(timezone.utc)

                if msg_type == MSG_HEARTBEAT:
                    # Heartbeat received, last_seen is updated.
                    pass

                elif msg_type == MSG_CHUNK_DATA:
                    if len(payload) < _CHUNK_ID_LEN:
                        continue
                    chunk_id = payload[:_CHUNK_ID_LEN].decode("utf-8")
                    self._resolve("retrieve", chunk_id, payload[_CHUNK_ID_LEN:])

                elif msg_type == MSG_CHUNK_NOT_FOUND:
                    self._resolve("retrieve", payload.decode("utf-8"), None)

                elif msg_type == MSG_STORE_OK:
                    self._resolve("store", payload.decode("utf-8"), True)

                elif msg_type == MSG_STORE_FAIL:
                    self._resolve("store", payload.decode("utf-8"), False)

                elif msg_type == MSG_STORE_CHUNK:
                    if len(payload) < _CHUNK_ID_LEN:
                        continue
                    chunk_id = payload[:_CHUNK_ID_LEN].decode("utf-8")
                    chunk_data = payload[_CHUNK_ID_LEN:]
                    try:
                        self.node.chunk_store.store(chunk_id, chunk_data)
                        await self.send_message(
                            MSG_STORE_OK, chunk_id.encode("utf-8")
                        )
                    except Exception as e:
                        logger.error(f"Failed to store chunk {chunk_id}: {e}")
                        await self.send_message(
                            MSG_STORE_FAIL, chunk_id.encode("utf-8")
                        )

                elif msg_type == MSG_RETRIEVE_CHUNK:
                    chunk_id = payload.decode("utf-8")
                    try:
                        chunk_data = self.node.chunk_store.retrieve(chunk_id)
                        await self.send_message(
                            MSG_CHUNK_DATA, chunk_id.encode("utf-8") + chunk_data
                        )
                    except ChunkNotFoundError:
                        await self.send_message(
                            MSG_CHUNK_NOT_FOUND, chunk_id.encode("utf-8")
                        )
                    except Exception as e:
                        logger.error(f"Failed to retrieve chunk {chunk_id}: {e}")
                        await self.send_message(
                            MSG_CHUNK_NOT_FOUND, chunk_id.encode("utf-8")
                        )

                elif msg_type == MSG_HAS_CHUNK:
                    chunk_id = payload.decode("utf-8")
                    present = self.node.chunk_store.has(chunk_id)
                    await self.send_message(
                        MSG_HAS_CHUNK_RESP,
                        chunk_id.encode("utf-8") + (b"\x01" if present else b"\x00"),
                    )

                elif msg_type == MSG_HAS_CHUNK_RESP:
                    if len(payload) < _CHUNK_ID_LEN + 1:
                        continue
                    chunk_id = payload[:_CHUNK_ID_LEN].decode("utf-8")
                    self._resolve(
                        "has", chunk_id, payload[_CHUNK_ID_LEN] == 1
                    )

                elif msg_type == MSG_SYNC_MANIFEST:
                    try:
                        remote_entries_dicts = json.loads(payload.decode("utf-8"))
                        from firecloud.manifest import entry_from_dict
                        remote_entries = [
                            entry_from_dict(edict) for edict in remote_entries_dicts
                        ]
                        self.node.manifest.merge(remote_entries)
                    except Exception as e:
                        logger.error(f"Failed to merge remote manifest: {e}")

                elif msg_type == MSG_MANIFEST_REQ:
                    try:
                        from dataclasses import asdict
                        entries = [
                            asdict(e) for e in self.node.manifest.to_entries()
                        ]
                        await self.send_message(
                            MSG_MANIFEST_RESP,
                            json.dumps(entries).encode("utf-8"),
                        )
                    except Exception as e:
                        logger.debug(f"Failed to answer manifest request: {e}")
                        await self.send_message(MSG_MANIFEST_RESP, b"[]")

                elif msg_type == MSG_MANIFEST_RESP:
                    try:
                        entries = json.loads(payload.decode("utf-8"))
                    except Exception:
                        entries = []
                    self._resolve("manifest", "", entries)

                elif msg_type == MSG_PEER_REQ:
                    try:
                        known = getattr(self.node, "_known_peers", {})
                        peers = [
                            {"node_id": nid, "host": h, "port": p}
                            for nid, (h, p) in known.items()
                        ]
                        await self.send_message(
                            MSG_PEER_RESP, json.dumps(peers).encode("utf-8")
                        )
                    except Exception as e:
                        logger.debug(f"Failed to answer peer request: {e}")
                        await self.send_message(MSG_PEER_RESP, b"[]")

                elif msg_type == MSG_PEER_RESP:
                    try:
                        peers = json.loads(payload.decode("utf-8"))
                    except Exception:
                        peers = []
                    for p in peers:
                        try:
                            if p["node_id"] != self.node.node_id:
                                self.node.add_peer_discovered(
                                    p["node_id"], p["host"], p["port"]
                                )
                        except Exception:
                            pass
                    self._resolve("peers", "", peers)

                elif msg_type == MSG_PEER_LIST:
                    try:
                        peers = json.loads(payload.decode("utf-8"))
                        for p in peers:
                            if p["node_id"] != self.node.node_id:
                                self.node.add_peer_discovered(
                                    p["node_id"], p["host"], p["port"]
                                )
                    except Exception as e:
                        logger.error(f"Failed to process peer list: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Connection with peer {self.peer_node_id} dropped: {e}")
        finally:
            await self.close()

    async def close(self) -> None:
        """Close the connection (idempotent)."""
        if self._closed:
            return
        self._closed = True
        self.read_task.cancel()
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(
                    TransportError("Connection closed while waiting for peer response")
                )
        self._pending.clear()
        try:
            self.writer.close()
            await asyncio.wait_for(self.writer.wait_closed(), timeout=0.5)
        except (Exception, asyncio.CancelledError):
            pass
        # Deregister only if we still own the slot; a reconnect may have
        # already replaced us in node.connections.
        connections = getattr(self.node, "connections", None)
        current = connections.get(self.peer_node_id) if connections is not None else None
        if current is None or current is self:
            self.node.on_connection_closed(self.peer_node_id)


# ---------------------------------------------------------------------------
# Node Server
# ---------------------------------------------------------------------------


class NodeServer:
    """Listens for secure TCP connections from remote peers."""

    def __init__(self, node, host: str, port: int) -> None:
        self.node = node
        self.host = host
        self.port = port
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Start the TCP server."""
        ssl_context = get_ssl_context(is_server=True, node_dir=self.node.storage_path / "ssl")
        self.server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
            ssl=ssl_context,
        )
        logger.info(f"Node server listening on {self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the TCP server."""
        if self.server:
            self.server.close()
            # Close all active connections on the node to unblock wait_closed
            if hasattr(self.node, "connections"):
                for conn in list(self.node.connections.values()):
                    await conn.close()
            try:
                await asyncio.wait_for(self.server.wait_closed(), timeout=1.0)
            except Exception:
                pass
            self.server = None
            logger.info("Node server stopped")

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle an incoming connection from a peer."""
        try:
            # Bounded handshake; an idle client can't hold the slot open.
            msg_type, payload = await asyncio.wait_for(
                read_msg(reader), timeout=HANDSHAKE_TIMEOUT
            )
            if msg_type != MSG_AUTH:
                writer.close()
                await writer.wait_closed()
                return

            if len(payload) < 32:
                writer.close()
                await writer.wait_closed()
                return

            token = payload[:32]

            if not hmac.compare_digest(token, self.node.network.auth_token):
                writer.close()
                await writer.wait_closed()
                raise NodeAuthError("Peer authentication failed: invalid token")

            # After the token: JSON with the peer's node_id and listen port.
            # A bare node_id (older clients) is accepted too.
            peer_port = 0
            try:
                meta = json.loads(payload[32:].decode("utf-8"))
                peer_node_id = meta["node_id"]
                peer_port = int(meta.get("port", 0))
            except Exception:
                peer_node_id = payload[32:].decode("utf-8")

            # Record observed IP + advertised port so we can gossip a
            # reachable address for this peer.
            if peer_port:
                peername = writer.get_extra_info("peername")
                observed_host = peername[0] if peername else "127.0.0.1"
                try:
                    self.node.add_peer_discovered(
                        peer_node_id, observed_host, peer_port
                    )
                except Exception:
                    pass

            # Authentication successful. Send AUTH_OK with our node ID.
            await write_msg(writer, MSG_AUTH_OK, self.node.node_id.encode("utf-8"))

            conn = PeerConnection(reader, writer, peer_node_id, self.node)
            self.node.register_connection(peer_node_id, conn)

        except Exception as e:
            logger.debug(f"Error handling incoming connection: {e}")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Node Client
# ---------------------------------------------------------------------------


class NodeClient:
    """Establishes secure TCP connections to remote peers."""

    def __init__(self, node) -> None:
        self.node = node

    async def connect(self, host: str, port: int) -> str:
        """Connect to a peer, authenticate, and register the connection."""
        ssl_context = get_ssl_context(is_server=False, node_dir=self.node.storage_path / "ssl")
        try:
            reader, writer = await asyncio.open_connection(host, port, ssl=ssl_context)
        except Exception as exc:
            raise TransportError(f"Failed to connect to {host}:{port}: {exc}") from exc

        try:
            # AUTH = 32-byte token + JSON with our node_id and listen port.
            meta = json.dumps({
                "node_id": self.node.node_id,
                "port": getattr(self.node, "port", 0),
            }).encode("utf-8")
            auth_payload = self.node.network.auth_token + meta
            await write_msg(writer, MSG_AUTH, auth_payload)

            # Receive AUTH_OK
            msg_type, payload = await asyncio.wait_for(
                read_msg(reader), timeout=HANDSHAKE_TIMEOUT
            )
            if msg_type != MSG_AUTH_OK:
                writer.close()
                await writer.wait_closed()
                raise NodeAuthError("Handshake failed: expected AUTH_OK")

            peer_node_id = payload.decode("utf-8")
            conn = PeerConnection(reader, writer, peer_node_id, self.node)
            self.node.register_connection(peer_node_id, conn)
            return peer_node_id

        except Exception as e:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            if isinstance(e, NodeAuthError):
                raise e
            if isinstance(e, TransportError):
                raise NodeAuthError("Handshake failed: peer rejected connection or auth token mismatch") from e
            raise TransportError(f"Handshake failed with {host}:{port}: {e}") from e
