"""Tests for firecloud.node: single-node and multi-node orchestration."""

import asyncio
import os
from pathlib import Path

import pytest

from firecloud import fec
from firecloud.crypto import compute_integrity_hash, derive_chunk_id
from firecloud.exceptions import (
    FileNotFoundError as ManifestFileNotFoundError,
)
from firecloud.manifest import ChunkInfo, FileEntry
from firecloud.network import Network
from firecloud.node import Node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(tmp_path: Path, *, port: int = 0, passphrase: str = "test") -> Node:
    """Create a Node with a fresh network for testing.

    Using port=0 lets the OS pick a free port.
    """
    net = Network.create(passphrase)
    storage = tmp_path / "storage"
    return Node(
        network=net,
        storage_path=storage,
        port=port,
        enable_discovery=False,  # No mDNS in tests
    )


def _make_test_file(tmp_path: Path, name: str = "hello.txt", size: int = 20_000) -> Path:
    """Create a deterministic test file of *size* bytes."""
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(os.urandom(size))
    return p


# ---------------------------------------------------------------------------
# Node lifecycle tests
# ---------------------------------------------------------------------------


class TestNodeLifecycle:
    """Node start / stop / status."""

    async def test_start_stop(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        assert node._running is True
        assert node._server is not None
        await node.stop()
        assert node._running is False

    async def test_double_start_is_noop(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        await node.start()  # should not raise
        assert node._running is True
        await node.stop()

    async def test_double_stop_is_noop(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        await node.stop()
        await node.stop()  # should not raise
        assert node._running is False

    async def test_status(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            s = node.status()
            assert s["node_id"] == node.node_id
            assert s["running"] is True
            assert s["peers_connected"] == 0
            assert s["files_stored"] == 0
        finally:
            await node.stop()


# ---------------------------------------------------------------------------
# Single-node upload / download round-trip
# ---------------------------------------------------------------------------


class TestSingleNodeUploadDownload:
    """Upload and download without any peers (local-only strategy)."""

    async def test_upload_download_roundtrip(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            src = _make_test_file(tmp_path / "src")
            original_bytes = src.read_bytes()

            file_id = await node.upload(src)
            assert isinstance(file_id, str)
            assert len(file_id) == 64  # HMAC-SHA-256 hex

            dest = tmp_path / "out" / "restored.txt"
            await node.download(file_id, dest)
            assert dest.read_bytes() == original_bytes
        finally:
            await node.stop()

    async def test_upload_small_file(self, tmp_path):
        """Files smaller than min_size should still round-trip."""
        node = _make_node(tmp_path)
        await node.start()
        try:
            src = _make_test_file(tmp_path / "src", size=100)
            original = src.read_bytes()

            file_id = await node.upload(src)
            dest = tmp_path / "out" / "small.txt"
            await node.download(file_id, dest)
            assert dest.read_bytes() == original
        finally:
            await node.stop()

    async def test_upload_large_file(self, tmp_path):
        """A 256 KB file producing many chunks."""
        node = _make_node(tmp_path)
        await node.start()
        try:
            src = _make_test_file(tmp_path / "src", size=256 * 1024)
            original = src.read_bytes()

            file_id = await node.upload(src)
            dest = tmp_path / "out" / "large.bin"
            await node.download(file_id, dest)
            assert dest.read_bytes() == original
        finally:
            await node.stop()

    async def test_upload_nonexistent_file_raises(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            with pytest.raises(FileNotFoundError):
                await node.upload(tmp_path / "does_not_exist.txt")
        finally:
            await node.stop()

    async def test_download_unknown_file_id_raises(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            with pytest.raises(ManifestFileNotFoundError):
                await node.download("0" * 64, tmp_path / "nope.txt")
        finally:
            await node.stop()


# ---------------------------------------------------------------------------
# File listing and deletion
# ---------------------------------------------------------------------------


class TestFileListAndDelete:

    async def test_list_files_after_upload(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            src = _make_test_file(tmp_path / "src", name="data.bin")
            file_id = await node.upload(src)
            files = node.list_files()
            assert len(files) == 1
            assert files[0]["file_id"] == file_id
            assert files[0]["name"] == "data.bin"
        finally:
            await node.stop()

    async def test_delete_file(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            src = _make_test_file(tmp_path / "src")
            file_id = await node.upload(src)
            assert len(node.list_files()) == 1

            await node.delete(file_id)
            assert len(node.list_files()) == 0
        finally:
            await node.stop()

    async def test_delete_nonexistent_raises(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            with pytest.raises(ManifestFileNotFoundError):
                await node.delete("0" * 64)
        finally:
            await node.stop()

    async def test_download_deleted_file_raises(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            src = _make_test_file(tmp_path / "src")
            file_id = await node.upload(src)
            await node.delete(file_id)
            with pytest.raises(ManifestFileNotFoundError):
                await node.download(file_id, tmp_path / "nope.txt")
        finally:
            await node.stop()


# ---------------------------------------------------------------------------
# Peers and connections
# ---------------------------------------------------------------------------


class TestPeers:

    async def test_peers_empty_initially(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            assert node.peers() == []
        finally:
            await node.stop()

    async def test_register_and_close_connection(self, tmp_path):
        node = _make_node(tmp_path)
        node.register_connection("peer-abc", "fake_conn")
        assert "peer-abc" in node.connections
        node.on_connection_closed("peer-abc")
        assert "peer-abc" not in node.connections

    async def test_add_peer_discovered(self, tmp_path):
        node = _make_node(tmp_path)
        node.add_peer_discovered("peer-xyz", "192.168.1.5", 7474)
        assert node._known_peers["peer-xyz"] == ("192.168.1.5", 7474)

    async def test_add_self_discovered_is_noop(self, tmp_path):
        node = _make_node(tmp_path)
        node.add_peer_discovered(node.node_id, "127.0.0.1", 7474)
        assert node.node_id not in node._known_peers


# ---------------------------------------------------------------------------
# Two-node transport round-trip
# ---------------------------------------------------------------------------


class TestTwoNodeTransport:
    """Start two nodes on the same network and exchange files."""

    async def test_two_node_upload_download(self, tmp_path):
        """Upload on node A, download on node B."""
        net = Network.create("shared-secret")

        node_a = Node(
            network=net,
            storage_path=tmp_path / "a",
            port=0,
            enable_discovery=False,
            node_id="node-a",
        )
        node_b = Node(
            network=net,
            storage_path=tmp_path / "b",
            port=0,
            enable_discovery=False,
            node_id="node-b",
        )

        await node_a.start()
        await node_b.start()

        try:
            # Determine actual port of A
            a_port = node_a._server.server.sockets[0].getsockname()[1]

            # B connects to A
            await node_b.connect(f"127.0.0.1:{a_port}")
            # Give handshake a moment
            await asyncio.sleep(0.3)

            # Upload on A
            src = _make_test_file(tmp_path / "src", size=20_000)
            original = src.read_bytes()
            file_id = await node_a.upload(src)

            # B should now have the manifest (synced on upload)
            # Give manifest sync a moment
            await asyncio.sleep(0.3)

            # Download on B
            dest = tmp_path / "out" / "from_b.bin"
            await node_b.download(file_id, dest)
            assert dest.read_bytes() == original

        finally:
            await node_b.stop()
            await node_a.stop()

    async def test_wrong_network_rejected(self, tmp_path):
        """A node from a different network cannot connect."""
        net_a = Network.create("secret-a")
        net_b = Network.create("secret-b")

        node_a = Node(
            network=net_a,
            storage_path=tmp_path / "a",
            port=0,
            enable_discovery=False,
        )
        node_b = Node(
            network=net_b,
            storage_path=tmp_path / "b",
            port=0,
            enable_discovery=False,
        )

        await node_a.start()
        await node_b.start()

        try:
            a_port = node_a._server.server.sockets[0].getsockname()[1]
            with pytest.raises(Exception):
                await node_b.connect(f"127.0.0.1:{a_port}")
        finally:
            await node_b.stop()
            await node_a.stop()


class TestMeshJoin:
    """Node.join fans out into a mesh via one bootstrap peer and pulls the catalog."""

    async def test_join_meshes_and_pulls_catalog(self, tmp_path):
        net = Network.create("mesh-secret")

        def mk(nid):
            return Node(network=net, storage_path=tmp_path / nid, port=0,
                        enable_discovery=False, node_id=nid)

        hub, b, c = mk("hub"), mk("spoke-b"), mk("spoke-c")
        d = None
        for n in (hub, b, c):
            await n.start()
        try:
            hub_addr = f"127.0.0.1:{hub.port}"
            # Spokes join via the hub; the hub learns their real listen ports.
            await b.join(hub_addr)
            await c.join(hub_addr)
            await asyncio.sleep(0.3)

            # A file uploaded on spoke-b gives the catalog something to pull.
            src = _make_test_file(tmp_path / "src", size=20_000)
            file_id = await b.upload(src)
            await asyncio.sleep(0.2)

            # A brand-new node joins through the hub only...
            d = mk("newcomer")
            await d.start()
            await d.join(hub_addr)
            await asyncio.sleep(0.3)

            # ...yet ends up connected to the whole cluster...
            assert {"hub", "spoke-b", "spoke-c"} <= set(d.connections.keys())
            # ...and has learned a file it never saw uploaded.
            assert d.manifest.get_file(file_id).name == src.name
        finally:
            for n in (hub, b, c, d):
                if n is not None:
                    await n.stop()


# ---------------------------------------------------------------------------
# Manifest entry tracking
# ---------------------------------------------------------------------------


class TestManifestIntegration:

    async def test_manifest_persists_across_restart(self, tmp_path):
        """Manifest survives a node restart."""
        net = Network.create("test")

        node = Node(
            network=net,
            storage_path=tmp_path / "store",
            port=0,
            enable_discovery=False,
        )
        await node.start()

        src = _make_test_file(tmp_path / "src")
        file_id = await node.upload(src)
        await node.stop()

        # Create a new Node pointing at the same storage
        node2 = Node(
            network=net,
            storage_path=tmp_path / "store",
            port=0,
            enable_discovery=False,
        )
        await node2.start()
        try:
            files = node2.list_files()
            assert len(files) == 1
            assert files[0]["file_id"] == file_id

            # Can download the file from local storage
            dest = tmp_path / "out" / "restored.txt"
            await node2.download(file_id, dest)
            assert dest.read_bytes() == src.read_bytes()
        finally:
            await node2.stop()


# ---------------------------------------------------------------------------
# Node removal and re-replication
# ---------------------------------------------------------------------------


class TestNodeRemovalAndRereplication:

    async def test_node_removal_and_rereplication(self, tmp_path):
        """Upload a file on 3 nodes (replication factor 2), stop a node, verify re-replication to candidate node."""
        net = Network.create("rerep-test")

        node_a = Node(network=net, storage_path=tmp_path / "a", port=0, enable_discovery=False, node_id="node-a")
        node_b = Node(network=net, storage_path=tmp_path / "b", port=0, enable_discovery=False, node_id="node-b")
        node_c = Node(network=net, storage_path=tmp_path / "c", port=0, enable_discovery=False, node_id="node-c")

        await node_a.start()
        await node_b.start()
        await node_c.start()

        try:
            b_port = node_b._server.server.sockets[0].getsockname()[1]
            c_port = node_c._server.server.sockets[0].getsockname()[1]

            await node_a.connect(f"127.0.0.1:{b_port}")
            await node_a.connect(f"127.0.0.1:{c_port}")
            await node_b.connect(f"127.0.0.1:{c_port}")
            await asyncio.sleep(0.5)

            src = _make_test_file(tmp_path / "src", size=5000)
            file_id = await node_a.upload(src)
            await asyncio.sleep(0.5)

            entry = node_a.manifest.get_file(file_id)
            chunk_info = entry.chunks[0]
            # Verify stored on A and B
            assert "node-a" in chunk_info.stored_on
            assert "node-b" in chunk_info.stored_on
            assert "node-c" not in chunk_info.stored_on

            # Now explicitly remove Node B. Node A should trigger re-replication of the chunk to Node C.
            await node_a.remove_node("node-b")
            await asyncio.sleep(0.5)

            # Get manifest again. The chunk should now be stored on A and C!
            entry2 = node_a.manifest.get_file(file_id)
            chunk_info2 = entry2.chunks[0]
            assert "node-b" not in chunk_info2.stored_on
            assert "node-a" in chunk_info2.stored_on
            assert "node-c" in chunk_info2.stored_on

            # Node C should have the chunk in its local store now!
            assert node_c.chunk_store.has(chunk_info.chunk_id)

        finally:
            await node_c.stop()
            await node_b.stop()
            await node_a.stop()


# ---------------------------------------------------------------------------
# FEC download after cluster shrink (regression: strategy from manifest)
# ---------------------------------------------------------------------------


class TestFecDownloadAfterClusterShrink:

    async def test_fec_file_downloads_with_fewer_live_peers(self, tmp_path):
        """A file uploaded with erasure coding (5 nodes) must still download
        after a peer goes offline (4 live nodes, below the FEC threshold).

        Before the fix, download re-derived the strategy from the live peer
        count and tried to read the FEC shares as replicated chunks.
        """
        net = Network.create("fec-shrink-test")

        nodes = {
            name: Node(
                network=net,
                storage_path=tmp_path / name,
                port=0,
                enable_discovery=False,
                node_id=name,
            )
            for name in ("node-a", "node-b", "node-c", "node-d", "node-e")
        }
        for node in nodes.values():
            await node.start()

        try:
            node_a = nodes["node-a"]
            for name, node in nodes.items():
                if name == "node-a":
                    continue
                port = node._server.server.sockets[0].getsockname()[1]
                await node_a.connect(f"127.0.0.1:{port}")
            await asyncio.sleep(0.3)
            assert len(node_a.connections) == 4

            src = _make_test_file(tmp_path / "src", size=256 * 1024)
            original = src.read_bytes()
            file_id = await node_a.upload(src)

            entry = node_a.manifest.get_file(file_id)
            assert entry.fec_enabled, "5-node upload should use erasure coding"

            # One peer goes offline -> 4 live nodes, below the FEC threshold.
            await nodes["node-e"].stop()
            await asyncio.sleep(0.3)
            assert len(node_a.connections) == 3

            dest = tmp_path / "out" / "restored.bin"
            await node_a.download(file_id, dest)
            assert dest.read_bytes() == original

        finally:
            for node in nodes.values():
                await node.stop()


# ---------------------------------------------------------------------------
# verify_file / verify_all
# ---------------------------------------------------------------------------


class TestVerify:

    async def test_verify_healthy_file(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            src = _make_test_file(tmp_path / "src")
            file_id = await node.upload(src)

            report = await node.verify_file(file_id)
            assert report["status"] == "healthy"
            assert report["available_chunks"] == report["total_chunks"]
            assert all(c["locations"] for c in report["chunks"])
        finally:
            await node.stop()

    async def test_verify_detects_missing_chunk(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            src = _make_test_file(tmp_path / "src")
            file_id = await node.upload(src)

            entry = node.manifest.get_file(file_id)
            node.chunk_store.delete(entry.chunks[0].chunk_id)

            report = await node.verify_file(file_id)
            assert report["status"] == "unrecoverable"
            assert report["available_chunks"] == report["total_chunks"] - 1
        finally:
            await node.stop()

    async def test_verify_detects_corrupt_chunk(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            src = _make_test_file(tmp_path / "src")
            file_id = await node.upload(src)

            entry = node.manifest.get_file(file_id)
            node.chunk_store.store(entry.chunks[0].chunk_id, b"not-the-real-bytes")

            report = await node.verify_file(file_id)
            assert report["status"] == "unrecoverable"
        finally:
            await node.stop()

    async def test_verify_all(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            await node.upload(_make_test_file(tmp_path / "src", name="one.bin"))
            await node.upload(_make_test_file(tmp_path / "src", name="two.bin"))

            reports = await node.verify_all()
            assert len(reports) == 2
            assert all(r["status"] == "healthy" for r in reports)
        finally:
            await node.stop()

    async def test_verify_unknown_file_raises(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            with pytest.raises(ManifestFileNotFoundError):
                await node.verify_file("0" * 64)
        finally:
            await node.stop()


class TestVerifyFecThresholds:
    """verify_file status boundaries for an erasure-coded entry."""

    async def test_verify_status_at_exact_share_counts(self, tmp_path):
        """healthy at N shares, degraded at exactly K, unrecoverable at K-1.

        The shares and manifest entry are laid down by hand on a single
        node so each share can be deleted individually; no cluster needed.
        """
        node = _make_node(tmp_path)

        payload = os.urandom(4096)
        shares = fec.encode(payload, 3, 5)  # K=3, N=5

        infos = []
        for i, share in enumerate(shares):
            chunk_id = derive_chunk_id(share, node.network.hmac_key)
            node.chunk_store.store(chunk_id, share)
            infos.append(
                ChunkInfo(
                    chunk_id=chunk_id,
                    integrity_hash=compute_integrity_hash(share),
                    index=i,
                    size=len(share),
                    stored_on=[node.node_id],
                )
            )

        node.manifest.add_file(
            FileEntry(
                file_id="fec-verify-boundary",
                name="fec.bin",
                size=len(payload),
                chunk_count=1,
                uploaded_at="2026-07-03T00:00:00+00:00",
                uploaded_by=node.node_id,
                chunks=infos,
                fec_enabled=True,
                replication_factor=1,
            )
        )

        report = await node.verify_file("fec-verify-boundary")
        assert report["status"] == "healthy"
        assert report["available_chunks"] == 5

        # N - K = 2 shares may vanish; at exactly K left it is degraded.
        node.chunk_store.delete(infos[0].chunk_id)
        node.chunk_store.delete(infos[1].chunk_id)
        report = await node.verify_file("fec-verify-boundary")
        assert report["status"] == "degraded"
        assert report["available_chunks"] == 3

        # One fewer than K and the file is gone.
        node.chunk_store.delete(infos[2].chunk_id)
        report = await node.verify_file("fec-verify-boundary")
        assert report["status"] == "unrecoverable"
