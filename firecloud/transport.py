"""FireCloud Transport Engine.

Handles secure peer-to-peer communication using asyncio TCP over TLS,
implementing a custom binary protocol, handshake, multiplexed chunk transfer,
manifest synchronization, and heartbeat monitoring.
"""

import asyncio
from datetime import datetime, timezone, timedelta
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
MSG_SYNC_MANIFEST = 0x20
MSG_HEARTBEAT = 0x30
MSG_PEER_LIST = 0x40

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
        msg_type_byte = await reader.readexactly(1)
        msg_type = msg_type_byte[0]
        payload = await reader.readexactly(length)
        return msg_type, payload
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
        self.pending_retrieve: asyncio.Future[bytes | None] | None = None
        self.last_seen = datetime.now(timezone.utc)
        self.read_task = asyncio.create_task(self._read_loop())

    async def send_message(self, msg_type: int, payload: bytes) -> None:
        """Send a message to the peer."""
        async with self.write_lock:
            await write_msg(self.writer, msg_type, payload)

    async def retrieve_chunk(self, chunk_id: str) -> bytes | None:
        """Request and retrieve a chunk from this peer."""
        async with self.retrieve_lock:
            loop = asyncio.get_running_loop()
            self.pending_retrieve = loop.create_future()
            try:
                await self.send_message(MSG_RETRIEVE_CHUNK, chunk_id.encode("utf-8"))
                # Wait for the background loop to resolve the future
                return await self.pending_retrieve
            finally:
                self.pending_retrieve = None

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
                    if self.pending_retrieve and not self.pending_retrieve.done():
                        self.pending_retrieve.set_result(payload)

                elif msg_type == MSG_CHUNK_NOT_FOUND:
                    if self.pending_retrieve and not self.pending_retrieve.done():
                        self.pending_retrieve.set_result(None)

                elif msg_type == MSG_STORE_CHUNK:
                    if len(payload) < 64:
                        continue
                    chunk_id = payload[:64].decode("utf-8")
                    chunk_data = payload[64:]
                    try:
                        self.node.chunk_store.store(chunk_id, chunk_data)
                    except Exception as e:
                        logger.error(f"Failed to store chunk {chunk_id}: {e}")

                elif msg_type == MSG_RETRIEVE_CHUNK:
                    chunk_id = payload.decode("utf-8")
                    try:
                        chunk_data = self.node.chunk_store.retrieve(chunk_id)
                        await self.send_message(MSG_CHUNK_DATA, chunk_data)
                    except ChunkNotFoundError:
                        await self.send_message(
                            MSG_CHUNK_NOT_FOUND, chunk_id.encode("utf-8")
                        )
                    except Exception as e:
                        logger.error(f"Failed to retrieve chunk {chunk_id}: {e}")
                        await self.send_message(
                            MSG_CHUNK_NOT_FOUND, chunk_id.encode("utf-8")
                        )

                elif msg_type == MSG_SYNC_MANIFEST:
                    try:
                        remote_entries_dicts = json.loads(payload.decode("utf-8"))
                        from firecloud.manifest import FileEntry, ChunkInfo
                        remote_entries = []
                        for edict in remote_entries_dicts:
                            d = dict(edict)
                            chunks = [ChunkInfo(**ci) for ci in d.pop("chunks", [])]
                            remote_entries.append(FileEntry(**d, chunks=chunks))
                        self.node.manifest.merge(remote_entries)
                    except Exception as e:
                        logger.error(f"Failed to merge remote manifest: {e}")

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
        """Close the connection."""
        self.read_task.cancel()
        if self.pending_retrieve and not self.pending_retrieve.done():
            self.pending_retrieve.set_exception(
                TransportError("Connection closed while waiting for chunk retrieval")
            )
        try:
            self.writer.close()
            await asyncio.wait_for(self.writer.wait_closed(), timeout=0.5)
        except (Exception, asyncio.CancelledError):
            pass
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
            # Perform server handshake
            msg_type, payload = await read_msg(reader)
            if msg_type != MSG_AUTH:
                writer.close()
                await writer.wait_closed()
                return

            if len(payload) < 32:
                writer.close()
                await writer.wait_closed()
                return

            token = payload[:32]
            peer_node_id = payload[32:].decode("utf-8")

            if token != self.node.network.auth_token:
                writer.close()
                await writer.wait_closed()
                raise NodeAuthError("Peer authentication failed: invalid token")

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
            # Send AUTH message: auth_token (32 bytes) + our node_id (UTF-8 bytes)
            auth_payload = self.node.network.auth_token + self.node.node_id.encode("utf-8")
            await write_msg(writer, MSG_AUTH, auth_payload)

            # Receive AUTH_OK
            msg_type, payload = await read_msg(reader)
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
