import asyncio
import pytest
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


@pytest.mark.asyncio
async def test_retrieve_honors_manifest_strategy_after_cluster_shrink(tmp_path):
    """Erasure-coded files must be read back as erasure-coded even when the
    live peer count has dropped below the FEC threshold (the strategy must
    come from the manifest, not the current cluster size)."""
    net = Network.create("passphrase")

    node_a = MockNode("node-A", net, tmp_path)
    peers = [MockNode(f"node-{x}", net, tmp_path) for x in "BCDE"]

    servers = []
    client_a = NodeClient(node_a)
    for peer in peers:
        srv = NodeServer(peer, "127.0.0.1", 0)
        await srv.start()
        servers.append(srv)
        await client_a.connect("127.0.0.1", srv.server.sockets[0].getsockname()[1])
    await asyncio.sleep(0.2)

    # Upload with 5 total nodes -> erasure coding (K=3, N=5)
    d_upload = Distributor(
        peers=["node-B", "node-C", "node-D", "node-E"],
        local_node_id="node-A",
    )
    chunks = [
        SimpleChunk("id1", "hash1", 0, b"data1"),
        SimpleChunk("id2", "hash2", 1, b"data2"),
        SimpleChunk("id3", "hash3", 2, b"data3"),
    ]
    infos = await d_upload.distribute(chunks, client_a)
    assert d_upload.get_strategy() == "erasure_coding"
    assert len(infos) == 5

    # Cluster shrinks: only D and E remain (3 total nodes < threshold 5).
    await servers[0].stop()  # B
    await servers[1].stop()  # C
    await asyncio.sleep(0.2)

    d_download = Distributor(
        peers=list(node_a.connections.keys()),
        local_node_id="node-A",
        fec_enabled=True,
    )
    # The live-count heuristic would now pick the wrong strategy...
    assert d_download.get_strategy() == "replication"

    # ...but an explicit manifest-derived strategy reconstructs correctly.
    retrieved = await d_download.retrieve(
        infos, client_a, strategy="erasure_coding", k=3
    )
    assert retrieved == [b"data1", b"data2", b"data3"]

    for srv in servers[2:]:
        await srv.stop()


@pytest.mark.asyncio
async def test_corrupt_share_is_skipped_during_reconstruction(tmp_path):
    """A share whose bytes do not match its integrity hash must be treated
    as missing instead of poisoning the FEC reconstruction."""
    net = Network.create("passphrase")

    node_a = MockNode("node-A", net, tmp_path)
    peers = [MockNode(f"node-{x}", net, tmp_path) for x in "BCDE"]

    servers = []
    client_a = NodeClient(node_a)
    for peer in peers:
        srv = NodeServer(peer, "127.0.0.1", 0)
        await srv.start()
        servers.append(srv)
        await client_a.connect("127.0.0.1", srv.server.sockets[0].getsockname()[1])
    await asyncio.sleep(0.2)

    d = Distributor(
        peers=["node-B", "node-C", "node-D", "node-E"],
        local_node_id="node-A",
    )
    chunks = [
        SimpleChunk("id1", "hash1", 0, b"data1"),
        SimpleChunk("id2", "hash2", 1, b"data2"),
        SimpleChunk("id3", "hash3", 2, b"data3"),
    ]
    infos = await d.distribute(chunks, client_a)
    await asyncio.sleep(0.2)

    # Corrupt the first share in place on whichever node holds it.
    first = infos[0]
    holders = [node_a] + peers
    for holder in holders:
        if holder.chunk_store.has(first.chunk_id):
            holder.chunk_store.store(first.chunk_id, b"corrupted-bytes")

    retrieved = await d.retrieve(infos, client_a, strategy="erasure_coding", k=3)
    assert retrieved == [b"data1", b"data2", b"data3"]

    for srv in servers:
        await srv.stop()


@pytest.mark.asyncio
async def test_distribute_skips_full_peer_and_records_actual_placement(tmp_path):
    """Placement metadata must reflect where chunks actually landed:
    a peer that rejects the store (quota) must not appear in stored_on."""
    net = Network.create("passphrase")

    node_a = MockNode("node-A", net, tmp_path)
    node_b = MockNode("node-B", net, tmp_path)
    node_b.chunk_store._max_storage = 0  # B can store nothing

    server_b = NodeServer(node_b, "127.0.0.1", 0)
    await server_b.start()
    port_b = server_b.server.sockets[0].getsockname()[1]

    client_a = NodeClient(node_a)
    await client_a.connect("127.0.0.1", port_b)
    await asyncio.sleep(0.1)

    d = Distributor(peers=["node-B"], local_node_id="node-A")
    chunks = [SimpleChunk("a" * 64, "hash1", 0, b"data1")]

    infos = await d.distribute(chunks, client_a)

    assert infos[0].stored_on == ["node-A"]
    assert node_a.chunk_store.has("a" * 64)
    assert not node_b.chunk_store.has("a" * 64)

    # Retrieval still works from the surviving replica.
    retrieved = await d.retrieve(infos, client_a, strategy="replication")
    assert retrieved == [b"data1"]

    await server_b.stop()


async def _connect_five(tmp_path, store_local=True):
    """Bring up node-A connected to four peers; return (distributor, ctx)."""
    net = Network.create("passphrase")
    node_a = MockNode("node-A", net, tmp_path)
    peers = [MockNode(f"node-{c}", net, tmp_path) for c in "BCDE"]

    servers = []
    for peer in peers:
        srv = NodeServer(peer, "127.0.0.1", 0)
        await srv.start()
        servers.append(srv)

    client_a = NodeClient(node_a)
    for srv in servers:
        await client_a.connect("127.0.0.1", srv.server.sockets[0].getsockname()[1])
    await asyncio.sleep(0.2)

    d = Distributor(
        peers=[p.node_id for p in peers],
        local_node_id="node-A",
        fec_threshold=5,
        store_local=store_local,
    )
    return d, (node_a, peers, servers, client_a)


@pytest.mark.asyncio
async def test_erasure_coding_caps_k_for_large_files(tmp_path):
    """Files with more chunks than zfec's block limit still encode and decode.

    Regression: distributing >170 chunks set K = chunk count, so
    N = ceil(1.5*K) blew past zfec's 256-block cap and upload crashed.
    """
    d, (node_a, peers, servers, client_a) = await _connect_five(tmp_path)
    # 200 chunks would give N = 300 > 256 without the cap.
    chunks = [SimpleChunk(f"id{i}", f"h{i}", i, bytes([i % 251]) * 4) for i in range(200)]

    infos = await d.distribute(chunks, client_a)
    await asyncio.sleep(0.2)

    # K is capped at 170, so N = ceil(1.5 * 170) = 255 <= 256.
    assert len(infos) == 255
    retrieved = await d.retrieve(infos, client_a)
    assert retrieved == [c.data for c in chunks]

    for srv in servers:
        await srv.stop()


@pytest.mark.asyncio
async def test_store_local_false_keeps_shares_off_the_uploader(tmp_path):
    """A transient uploader (store_local=False) keeps shares only on peers."""
    d, (node_a, peers, servers, client_a) = await _connect_five(
        tmp_path, store_local=False
    )
    chunks = [SimpleChunk(f"id{i}", f"h{i}", i, f"data{i}".encode()) for i in range(3)]

    infos = await d.distribute(chunks, client_a)
    await asyncio.sleep(0.2)

    # Nothing lands on the local (about-to-exit) node.
    assert all("node-A" not in info.stored_on for info in infos)
    assert node_a.chunk_store.list_chunks() == []
    # The file is still fully retrievable from the durable peers.
    retrieved = await d.retrieve(infos, client_a)
    assert retrieved == [c.data for c in chunks]

    for srv in servers:
        await srv.stop()


@pytest.mark.asyncio
async def test_erasure_retrieve_at_exact_share_threshold(tmp_path):
    """Reconstruction works with exactly K shares left and fails with K-1."""
    net = Network.create("passphrase")
    node_a = MockNode("node-A", net, tmp_path)

    class FakeTransport:
        def __init__(self, node_obj):
            self.node = node_obj

    transport = FakeTransport(node_a)

    # Four phantom peers force the erasure-coding strategy; with no live
    # connections every share falls back to node-A's local store, which
    # lets the test delete individual shares surgically.
    d = Distributor(
        peers=["node-B", "node-C", "node-D", "node-E"],
        local_node_id="node-A",
    )
    chunks = [
        SimpleChunk("id1", "hash1", 0, b"data1"),
        SimpleChunk("id2", "hash2", 1, b"data2"),
        SimpleChunk("id3", "hash3", 2, b"data3"),
    ]
    infos = await d.distribute(chunks, transport)
    assert len(infos) == 5  # K=3, N=5
    assert all(i.stored_on == ["node-A"] for i in infos)

    # Drop shares until exactly K remain; K must still reconstruct.
    node_a.chunk_store.delete(infos[0].chunk_id)
    node_a.chunk_store.delete(infos[1].chunk_id)
    retrieved = await d.retrieve(infos, transport, strategy="erasure_coding")
    assert retrieved == [b"data1", b"data2", b"data3"]

    # One below the threshold cannot.
    node_a.chunk_store.delete(infos[2].chunk_id)
    with pytest.raises(ChunkNotFoundError):
        await d.retrieve(infos, transport, strategy="erasure_coding")
