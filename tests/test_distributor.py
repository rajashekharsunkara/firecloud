import asyncio
import os
import pytest
import tempfile
from pathlib import Path
from dataclasses import dataclass

from firecloud.network import Network
from firecloud.exceptions import ChunkNotFoundError
from firecloud.distributor import Distributor
from firecloud.transport import NodeServer, NodeClient
from tests.test_transport import MockNode


@dataclass
class SimpleChunk:
    chunk_id: str
    integrity_hash: str
    index: int
    data: bytes


def test_strategy_selection():
    # 0 peers -> local
    d = Distributor(peers=[], local_node_id="local")
    assert d.get_strategy() == "local"
    
    # 1-3 peers -> replication
    d = Distributor(peers=["peer1"], local_node_id="local")
    assert d.get_strategy() == "replication"
    
    d = Distributor(peers=["peer1", "peer2", "peer3"], local_node_id="local")
    assert d.get_strategy() == "replication"
    
    # 4+ peers -> erasure_coding
    d = Distributor(peers=["peer1", "peer2", "peer3", "peer4"], local_node_id="local")
    assert d.get_strategy() == "erasure_coding"
    
    # disabled FEC -> replication even with 4+ peers
    d = Distributor(peers=["peer1", "peer2", "peer3", "peer4"], local_node_id="local", fec_enabled=False)
    assert d.get_strategy() == "replication"


@pytest.mark.asyncio
async def test_distribute_retrieve_local(tmp_path):
    net = Network.create("passphrase")
    node = MockNode("node-A", net, tmp_path)
    
    # Mock transport object
    class FakeTransport:
        def __init__(self, node_obj):
            self.node = node_obj
            self.connections = {}
            
    transport = FakeTransport(node)
    
    d = Distributor(peers=[], local_node_id="node-A")
    chunks = [
        SimpleChunk("a" * 64, "hash1", 0, b"data1"),
        SimpleChunk("b" * 64, "hash2", 1, b"data2"),
    ]
    
    # Distribute
    infos = await d.distribute(chunks, transport)
    assert len(infos) == 2
    assert infos[0].stored_on == ["node-A"]
    assert node.chunk_store.has("a" * 64)
    
    # Retrieve
    retrieved = await d.retrieve(infos, transport)
    assert retrieved == [b"data1", b"data2"]


@pytest.mark.asyncio
async def test_distribute_retrieve_replication(tmp_path):
    net = Network.create("passphrase")
    
    node_a = MockNode("node-A", net, tmp_path)
    node_b = MockNode("node-B", net, tmp_path)
    
    server_b = NodeServer(node_b, "127.0.0.1", 0)
    await server_b.start()
    port_b = server_b.server.sockets[0].getsockname()[1]
    
    client_a = NodeClient(node_a)
    await client_a.connect("127.0.0.1", port_b)
    await asyncio.sleep(0.1)
    
    # B is registered in A's connections
    assert "node-B" in node_a.connections
    
    d = Distributor(peers=["node-B"], local_node_id="node-A")
    chunks = [
        SimpleChunk("a" * 64, "hash1", 0, b"data1"),
        SimpleChunk("b" * 64, "hash2", 1, b"data2"),
    ]
    
    infos = await d.distribute(chunks, client_a)
    await asyncio.sleep(0.1)
    
    # Replication factor = 2, both node-A and node-B should have stored
    assert len(infos) == 2
    assert set(infos[0].stored_on) == {"node-A", "node-B"}
    assert node_a.chunk_store.has("a" * 64)
    assert node_b.chunk_store.has("a" * 64)
    
    retrieved = await d.retrieve(infos, client_a)
    assert retrieved == [b"data1", b"data2"]
    
    await server_b.stop()


@pytest.mark.asyncio
async def test_distribute_retrieve_erasure_coding(tmp_path):
    net = Network.create("passphrase")
    
    # We need 5 nodes total for FEC (threshold = 5)
    node_a = MockNode("node-A", net, tmp_path)
    node_b = MockNode("node-B", net, tmp_path)
    node_c = MockNode("node-C", net, tmp_path)
    node_d = MockNode("node-D", net, tmp_path)
    node_e = MockNode("node-E", net, tmp_path)
    
    servers = []
    ports = {}
    
    for peer in [node_b, node_c, node_d, node_e]:
        srv = NodeServer(peer, "127.0.0.1", 0)
        await srv.start()
        servers.append(srv)
        ports[peer.node_id] = srv.server.sockets[0].getsockname()[1]
        
    client_a = NodeClient(node_a)
    for peer_id, port in ports.items():
        await client_a.connect("127.0.0.1", port)
        
    await asyncio.sleep(0.2)
    
    d = Distributor(
        peers=["node-B", "node-C", "node-D", "node-E"],
        local_node_id="node-A",
        fec_threshold=5,
    )
    
    chunks = [
        SimpleChunk("id1", "hash1", 0, b"data1"),
        SimpleChunk("id2", "hash2", 1, b"data2"),
        SimpleChunk("id3", "hash3", 2, b"data3"),
    ]
    
    infos = await d.distribute(chunks, client_a)
    await asyncio.sleep(0.2)
    
    # Total shares N = ceil(3 * 1.5) = 5 shares
    assert len(infos) == 5
    
    # Verify we can retrieve them normally
    retrieved = await d.retrieve(infos, client_a)
    assert retrieved == [b"data1", b"data2", b"data3"]
    
    # Simulate node failures: stop server B and server C
    # This leaves node-A (local), node-D, node-E online.
    # Total active nodes: 3 (which is exactly K = 3).
    # We close connections to node-B and node-C
    await servers[0].stop()  # Stop B
    await servers[1].stop()  # Stop C
    await asyncio.sleep(0.2)
    
    # We should still be able to retrieve and reconstruct!
    retrieved_fec = await d.retrieve(infos, client_a)
    assert retrieved_fec == [b"data1", b"data2", b"data3"]
    
    # Now stop Node D (leaving only 2 nodes online: A and E, which is < K = 3)
    await servers[2].stop()
    await asyncio.sleep(0.2)
    
    with pytest.raises(ChunkNotFoundError):
        await d.retrieve(infos, client_a)
        
    for srv in servers:
        await srv.stop()
