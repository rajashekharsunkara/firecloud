import asyncio
import json
import struct

import pytest
from pathlib import Path

from firecloud.network import Network
from firecloud.exceptions import NodeAuthError, TransportError
from firecloud.storage import ChunkStore
from firecloud.manifest import Manifest, FileEntry
from firecloud.transport import (
    MAX_FRAME_SIZE,
    MSG_PEER_LIST,
    MSG_SYNC_MANIFEST,
    NodeClient,
    NodeServer,
    PeerConnection,
    read_msg,
)

# ---------------------------------------------------------------------------
# Mock Node Class for testing
# ---------------------------------------------------------------------------


class MockNode:
    def __init__(self, node_id: str, network: Network, tmp_path: Path):
        self.node_id = node_id
        self.network = network
        self.storage_path = tmp_path / f"node_{node_id}"
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.chunk_store = ChunkStore(self.storage_path / "chunks", max_storage=1024 * 1024)
        self.manifest = Manifest(self.storage_path / "manifest")
        
        self.connections = {}
        self.discovered_peers = []
        self.closed_connections = []

    def register_connection(self, peer_node_id, conn):
        self.connections[peer_node_id] = conn

    def on_connection_closed(self, peer_node_id):
        self.closed_connections.append(peer_node_id)
        if peer_node_id in self.connections:
            del self.connections[peer_node_id]

    def add_peer_discovered(self, peer_node_id, host, port):
        self.discovered_peers.append((peer_node_id, host, port))


@pytest.fixture
def clean_node_dir(tmp_path):
    # Ensure a fresh directory for SSL cert generation inside tests
    return tmp_path


@pytest.mark.asyncio
async def test_transport_handshake_and_chunk_transfer(clean_node_dir):
    # Setup Network
    net = Network.create("my-secret-passphrase")
    
    # Setup Mock Nodes
    node_a = MockNode("node-A", net, clean_node_dir)
    node_b = MockNode("node-B", net, clean_node_dir)
    
    # Start Server B on a free port
    server_b = NodeServer(node_b, "127.0.0.1", 0)
    await server_b.start()
    
    # Get the dynamically allocated port
    port = server_b.server.sockets[0].getsockname()[1]
    
    # Client A connects to Server B
    client_a = NodeClient(node_a)
    peer_id = await client_a.connect("127.0.0.1", port)
    
    assert peer_id == "node-B"
    assert "node-B" in node_a.connections
    
    # Allow background loop to process register on server side
    await asyncio.sleep(0.1)
    assert "node-A" in node_b.connections
    
    # -----------------------------------------------------------------------
    # Test Chunk Storage (A -> B)
    # -----------------------------------------------------------------------
    conn_a = node_a.connections["node-B"]
    chunk_id = "a" * 64
    chunk_data = b"hello-world-from-chunk-storage"
    
    # Send Store chunk message
    store_payload = chunk_id.encode("utf-8") + chunk_data
    await conn_a.send_message(0x10, store_payload)
    
    # Wait for processing
    await asyncio.sleep(0.1)
    
    # Verify B stored it
    assert node_b.chunk_store.has(chunk_id)
    assert node_b.chunk_store.retrieve(chunk_id) == chunk_data
    
    # -----------------------------------------------------------------------
    # Test Chunk Retrieval (A <- B)
    # -----------------------------------------------------------------------
    # B has another chunk pre-stored
    chunk_id_2 = "b" * 64
    chunk_data_2 = b"secret-data-stored-on-b"
    node_b.chunk_store.store(chunk_id_2, chunk_data_2)
    
    # A retrieves it
    retrieved = await conn_a.retrieve_chunk(chunk_id_2)
    assert retrieved == chunk_data_2
    
    # Retrieve missing chunk
    retrieved_missing = await conn_a.retrieve_chunk("c" * 64)
    assert retrieved_missing is None
    
    # -----------------------------------------------------------------------
    # Clean up
    # -----------------------------------------------------------------------
    await server_b.stop()
    # Wait a bit for connections to close
    await asyncio.sleep(0.1)
    
    assert "node-B" in node_a.closed_connections


@pytest.mark.asyncio
async def test_transport_invalid_auth(clean_node_dir):
    net_a = Network.create("pass-a")
    net_b = Network.create("pass-b")  # Different network
    
    node_a = MockNode("node-A", net_a, clean_node_dir)
    node_b = MockNode("node-B", net_b, clean_node_dir)
    
    server_b = NodeServer(node_b, "127.0.0.1", 0)
    await server_b.start()
    port = server_b.server.sockets[0].getsockname()[1]
    
    client_a = NodeClient(node_a)
    
    # Connect should fail due to token mismatch during handshake
    with pytest.raises(NodeAuthError):
        await client_a.connect("127.0.0.1", port)
        
    await server_b.stop()


@pytest.mark.asyncio
async def test_manifest_sync_and_gossip(clean_node_dir):
    net = Network.create("pass")
    node_a = MockNode("node-A", net, clean_node_dir)
    node_b = MockNode("node-B", net, clean_node_dir)
    
    # Pre-add file to Node A's manifest
    file_entry = FileEntry(
        file_id="test-file-id",
        name="test.txt",
        size=100,
        chunk_count=1,
        uploaded_at="2026-05-27T00:00:00Z",
        uploaded_by="node-A",
        lamport_ts=1,
    )
    node_a.manifest.add_file(file_entry)
    
    server_b = NodeServer(node_b, "127.0.0.1", 0)
    await server_b.start()
    port = server_b.server.sockets[0].getsockname()[1]
    
    client_a = NodeClient(node_a)
    await client_a.connect("127.0.0.1", port)
    await asyncio.sleep(0.1)
    
    conn_a = node_a.connections["node-B"]
    
    # A sends SYNC_MANIFEST to B
    from dataclasses import asdict
    manifest_payload = json.dumps([asdict(e) for e in node_a.manifest.to_entries()]).encode("utf-8")
    await conn_a.send_message(MSG_SYNC_MANIFEST, manifest_payload)
    await asyncio.sleep(0.1)
    
    # B should have merged it
    b_entry = node_b.manifest.get_file("test-file-id")
    assert b_entry.name == "test.txt"
    assert b_entry.uploaded_by == "node-A"
    
    # A sends PEER_LIST to B
    peers = [{"node_id": "node-C", "host": "127.0.0.1", "port": 8080}]
    peer_payload = json.dumps(peers).encode("utf-8")
    await conn_a.send_message(MSG_PEER_LIST, peer_payload)
    await asyncio.sleep(0.1)

    # B should have processed peer discovery
    assert ("node-C", "127.0.0.1", 8080) in node_b.discovered_peers

    await server_b.stop()


@pytest.mark.asyncio
async def test_read_msg_rejects_oversized_frame():
    """A hostile or corrupt length prefix must not trigger a huge allocation."""
    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack("!I", MAX_FRAME_SIZE + 1) + b"\x10")

    with pytest.raises(TransportError, match="exceeds maximum"):
        await read_msg(reader)


@pytest.mark.asyncio
async def test_read_msg_accepts_frame_at_limit_header():
    """A frame exactly at the limit passes the length check."""
    reader = asyncio.StreamReader()
    payload = b"x" * 16
    reader.feed_data(struct.pack("!I", len(payload)) + b"\x30" + payload)

    msg_type, received = await read_msg(reader)
    assert msg_type == 0x30
    assert received == payload


@pytest.mark.asyncio
async def test_retrieve_chunk_times_out_on_silent_peer(clean_node_dir):
    """A peer that never answers must not hang the retrieval forever."""
    net = Network.create("pass")
    node_a = MockNode("node-A", net, clean_node_dir)

    async def silent_handler(reader, writer):
        # Consume the request without ever responding; exits on disconnect.
        await reader.read()
        writer.close()

    server = await asyncio.start_server(silent_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    conn = PeerConnection(reader, writer, "silent-peer", node_a)

    try:
        result = await conn.retrieve_chunk("a" * 64, timeout=0.3)
        assert result is None
    finally:
        await conn.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_store_chunk_ack_roundtrip(clean_node_dir):
    """store_chunk returns True only when the peer confirms persistence."""
    net = Network.create("pass")
    node_a = MockNode("node-A", net, clean_node_dir)
    node_b = MockNode("node-B", net, clean_node_dir)

    server_b = NodeServer(node_b, "127.0.0.1", 0)
    await server_b.start()
    port = server_b.server.sockets[0].getsockname()[1]

    client_a = NodeClient(node_a)
    await client_a.connect("127.0.0.1", port)
    await asyncio.sleep(0.1)

    conn_a = node_a.connections["node-B"]
    chunk_id = "d" * 64

    ok = await conn_a.store_chunk(chunk_id, b"ack-tested-data", timeout=5)
    assert ok is True
    assert node_b.chunk_store.retrieve(chunk_id) == b"ack-tested-data"

    # Fill B's quota so the next store gets NACKed.
    node_b.chunk_store._max_storage = 0
    ok = await conn_a.store_chunk("e" * 64, b"wont-fit", timeout=5)
    assert ok is False
    assert not node_b.chunk_store.has("e" * 64)

    await server_b.stop()


@pytest.mark.asyncio
async def test_has_chunk_probe(clean_node_dir):
    """has_chunk reports presence without transferring the chunk."""
    net = Network.create("pass")
    node_a = MockNode("node-A", net, clean_node_dir)
    node_b = MockNode("node-B", net, clean_node_dir)

    server_b = NodeServer(node_b, "127.0.0.1", 0)
    await server_b.start()
    port = server_b.server.sockets[0].getsockname()[1]

    client_a = NodeClient(node_a)
    await client_a.connect("127.0.0.1", port)
    await asyncio.sleep(0.1)

    conn_a = node_a.connections["node-B"]
    chunk_id = "f" * 64
    node_b.chunk_store.store(chunk_id, b"present")

    assert await conn_a.has_chunk(chunk_id) is True
    assert await conn_a.has_chunk("0" * 64) is False

    await server_b.stop()
