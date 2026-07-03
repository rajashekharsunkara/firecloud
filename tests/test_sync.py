"""Tests for firecloud.sync: watchdog-based folder synchronization."""

import asyncio
import os
from pathlib import Path


from firecloud.network import Network
from firecloud.node import Node
from firecloud.sync import FolderSync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(tmp_path: Path, subdir: str = "store") -> Node:
    """Create a node with discovery disabled for testing."""
    net = Network.create("sync-test")
    return Node(
        network=net,
        storage_path=tmp_path / subdir,
        port=0,
        enable_discovery=False,
    )


# ---------------------------------------------------------------------------
# FolderSync lifecycle
# ---------------------------------------------------------------------------


class TestFolderSyncLifecycle:

    async def test_start_stop(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            sync_dir = tmp_path / "sync"
            fs = FolderSync(node, sync_dir)
            await fs.start()
            assert fs._running is True
            assert fs._observer is not None
            await fs.stop()
            assert fs._running is False
        finally:
            await node.stop()

    async def test_double_start_is_noop(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            sync_dir = tmp_path / "sync"
            fs = FolderSync(node, sync_dir)
            await fs.start()
            await fs.start()  # should not raise
            assert fs._running is True
            await fs.stop()
        finally:
            await node.stop()

    async def test_double_stop_is_noop(self, tmp_path):
        node = _make_node(tmp_path)
        await node.start()
        try:
            sync_dir = tmp_path / "sync"
            fs = FolderSync(node, sync_dir)
            await fs.start()
            await fs.stop()
            await fs.stop()  # should not raise
        finally:
            await node.stop()


# ---------------------------------------------------------------------------
# File creation detection (outbound sync)
# ---------------------------------------------------------------------------


class TestOutboundSync:

    async def test_new_file_is_uploaded(self, tmp_path):
        """Creating a file in the sync folder should trigger an upload."""
        node = _make_node(tmp_path)
        await node.start()
        sync_dir = tmp_path / "sync"
        fs = FolderSync(node, sync_dir)
        await fs.start()

        try:
            # Create a file in the sync folder
            test_file = sync_dir / "test_data.bin"
            content = os.urandom(5000)
            test_file.write_bytes(content)

            # Wait for debounce + processing
            await asyncio.sleep(2.0)

            # Verify it was uploaded to the node
            files = node.list_files()
            assert len(files) == 1
            assert files[0]["name"] == "test_data.bin"
        finally:
            await fs.stop()
            await node.stop()

    async def test_deleted_file_is_tombstoned(self, tmp_path):
        """Deleting a file from the sync folder should tombstone it in the manifest."""
        node = _make_node(tmp_path)
        await node.start()
        sync_dir = tmp_path / "sync"
        fs = FolderSync(node, sync_dir)
        await fs.start()

        try:
            # Create and wait for upload
            test_file = sync_dir / "to_delete.bin"
            test_file.write_bytes(os.urandom(5000))
            await asyncio.sleep(2.0)
            assert len(node.list_files()) == 1

            # Delete and wait for debounce
            test_file.unlink()
            await asyncio.sleep(2.0)

            # Should be tombstoned
            assert len(node.list_files()) == 0
        finally:
            await fs.stop()
            await node.stop()


# ---------------------------------------------------------------------------
# Incoming sync (pull from manifest)
# ---------------------------------------------------------------------------


class TestInboundSync:

    async def test_incoming_file_downloaded(self, tmp_path):
        """A file in the manifest but not in the sync folder should be downloaded."""
        node = _make_node(tmp_path)
        await node.start()

        try:
            # Upload a file outside the sync folder
            src = tmp_path / "external" / "from_peer.bin"
            src.parent.mkdir(parents=True, exist_ok=True)
            content = os.urandom(5000)
            src.write_bytes(content)
            await node.upload(src)

            # Now start sync pointing at a separate, empty folder
            sync_dir = tmp_path / "sync"
            fs = FolderSync(node, sync_dir)
            await fs.start()

            # Wait for the incoming loop to fire
            await asyncio.sleep(7.0)

            # The file should now exist in the sync folder
            downloaded = sync_dir / "from_peer.bin"
            assert downloaded.exists()
            assert downloaded.read_bytes() == content

            await fs.stop()
        finally:
            await node.stop()


# ---------------------------------------------------------------------------
# Name-to-ID mapping
# ---------------------------------------------------------------------------


class TestNameMapping:

    async def test_rebuild_name_map(self, tmp_path):
        """_rebuild_name_map should populate from existing manifest entries
        that are present in the sync folder."""
        node = _make_node(tmp_path)
        await node.start()

        try:
            # Upload a file
            src = tmp_path / "src" / "mapped.txt"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(b"hello world" * 500)
            file_id = await node.upload(src)

            # Point sync at a folder that contains the same filename
            sync_dir = tmp_path / "sync"
            sync_dir.mkdir(parents=True, exist_ok=True)
            (sync_dir / "mapped.txt").write_bytes(b"hello world" * 500)

            fs = FolderSync(node, sync_dir)
            fs._rebuild_name_map()

            assert fs._name_to_id["mapped.txt"] == file_id
            assert fs._id_to_name[file_id] == "mapped.txt"
        finally:
            await node.stop()
