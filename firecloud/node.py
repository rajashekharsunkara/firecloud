"""FireCloud Node — orchestrates storage, transport, discovery, and sync.

The :class:`Node` is the primary user-facing object.  It wires together
the chunk store, manifest, transport layer, mDNS discovery, and
distributor so that files can be uploaded, downloaded, deleted, and
synced across the LAN with a single method call.
"""

import asyncio
import builtins
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from firecloud.chunker import Chunk, chunk_file, reassemble_chunks, compute_file_id
from firecloud.crypto import encrypt_chunk, decrypt_chunk, compute_integrity_hash
from firecloud.discovery import LANDiscovery
from firecloud.distributor import Distributor
from firecloud.exceptions import (
    ChunkCorruptError,
)
from firecloud.manifest import FileEntry, Manifest
from firecloud.network import Network
from firecloud.storage import ChunkStore
from firecloud.transport import NodeClient, NodeServer, PeerConnection, MSG_SYNC_MANIFEST

logger = logging.getLogger("firecloud.node")


class Node:
    """Main FireCloud node that orchestrates all operations.

    Ties together:
    - :class:`~firecloud.storage.ChunkStore` for local chunk persistence
    - :class:`~firecloud.manifest.Manifest` for file metadata
    - :class:`~firecloud.transport.NodeServer` / :class:`~firecloud.transport.NodeClient`
      for peer-to-peer communication
    - :class:`~firecloud.discovery.LANDiscovery` for mDNS peer discovery
    - :class:`~firecloud.distributor.Distributor` for chunk placement
    """

    def __init__(
        self,
        network: Network,
        storage_path: Path | str,
        port: int = 7474,
        max_storage: int | None = None,
        host: str = "0.0.0.0",
        node_id: str | None = None,
        enable_discovery: bool = True,
    ) -> None:
        """Initialise the node.

        Args:
            network: The :class:`~firecloud.network.Network` this node belongs to.
            storage_path: Root directory for chunk storage and metadata.
            port: TCP port to listen on.
            max_storage: Maximum bytes for the chunk store (``None`` = 80 % of disk).
            host: Interface address to bind the server to.
            node_id: Unique identifier for this node.  Auto-generated when ``None``.
            enable_discovery: Whether to start mDNS discovery.
        """
        self.network = network
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.port = port
        self.host = host
        self.enable_discovery = enable_discovery

        # Node identity
        self.node_id = node_id or uuid.uuid4().hex[:16]

        # Core subsystems — initialised eagerly so tests can inject mocks.
        chunks_dir = self.storage_path / "chunks"
        self.chunk_store = ChunkStore(chunks_dir, max_storage=max_storage)
        self.manifest = Manifest(self.storage_path)

        # Transport
        self._server: NodeServer | None = None
        self._client: NodeClient | None = None

        # Discovery
        self._discovery: LANDiscovery | None = None

        # Active peer connections keyed by peer node_id.
        self.connections: dict[str, PeerConnection] = {}

        # Known peer addresses: {node_id: (host, port)}
        self._known_peers: dict[str, tuple[str, int]] = {}

        # Background tasks
        self._heartbeat_task: asyncio.Task | None = None
        self._manifest_sync_task: asyncio.Task | None = None

        # Running state
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the node: server, discovery, heartbeat, and manifest sync."""
        if self._running:
            return

        # Start TCP server
        self._server = NodeServer(self, self.host, self.port)
        await self._server.start()

        # Start client
        self._client = NodeClient(self)

        # Start mDNS discovery
        if self.enable_discovery:
            try:
                self._discovery = LANDiscovery(
                    self.node_id,
                    self.network.network_id,
                    self.port,
                )
                self._discovery.on_peer_found(self._on_peer_discovered)
                self._discovery.on_peer_removed(self._on_peer_removed)
                await self._discovery.start()
            except Exception as exc:
                logger.warning(f"mDNS discovery failed to start: {exc}")
                self._discovery = None

        # Start periodic tasks
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._manifest_sync_task = asyncio.create_task(self._manifest_sync_loop())

        self._running = True
        logger.info(
            f"Node {self.node_id} started on {self.host}:{self.port} "
            f"(network {self.network.network_id})"
        )

    async def stop(self) -> None:
        """Gracefully shut down the node."""
        if not self._running:
            return
        self._running = False

        # Cancel periodic tasks
        for task in (self._heartbeat_task, self._manifest_sync_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close all peer connections
        for conn in list(self.connections.values()):
            try:
                await conn.close()
            except Exception:
                pass
        self.connections.clear()

        # Stop server
        if self._server:
            await self._server.stop()
            self._server = None

        # Stop discovery
        if self._discovery:
            try:
                await self._discovery.stop()
            except Exception:
                pass
            self._discovery = None

        logger.info(f"Node {self.node_id} stopped")

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def upload(self, filepath: str | Path) -> str:
        """Upload a file to the network.

        Pipeline: read → chunk → encrypt → distribute → manifest.

        Args:
            filepath: Path to the local file to upload.

        Returns:
            The file_id (HMAC-SHA-256 of the whole file content).

        Raises:
            builtins.FileNotFoundError: If *filepath* does not exist.
            StorageFullError: If the quota is exceeded.
        """
        filepath = Path(filepath)
        if not filepath.is_file():
            raise builtins.FileNotFoundError(f"File not found: {filepath}")

        hmac_key = self.network.hmac_key
        enc_key = self.network.encryption_key

        # 1. Compute the file-level ID
        file_id = compute_file_id(filepath, hmac_key)

        # 2. Content-defined chunking
        chunks = chunk_file(filepath, hmac_key)

        # 3. Encrypt each chunk
        encrypted_chunks = []
        for c in chunks:
            enc_data = encrypt_chunk(c.data, enc_key)
            encrypted_chunks.append(
                Chunk(
                    index=c.index,
                    offset=c.offset,
                    length=c.length,
                    data=enc_data,
                    chunk_id=c.chunk_id,
                    integrity_hash=c.integrity_hash,
                )
            )

        # 4. Distribute
        peer_ids = list(self.connections.keys())
        distributor = Distributor(
            peers=peer_ids,
            local_node_id=self.node_id,
            fec_enabled=len(peer_ids) + 1 >= 5,
        )
        chunk_infos = await distributor.distribute(encrypted_chunks, self._client)

        # 5. Build manifest entry
        strategy = distributor.get_strategy()
        entry = FileEntry(
            file_id=file_id,
            name=filepath.name,
            size=filepath.stat().st_size,
            chunk_count=len(chunks),
            uploaded_at=datetime.now(timezone.utc).isoformat(),
            uploaded_by=self.node_id,
            chunks=chunk_infos,
            fec_enabled=(strategy == "erasure_coding"),
            replication_factor=2 if strategy == "replication" else 1,
        )
        self.manifest.add_file(entry)

        # 6. Sync manifest to peers
        await self._sync_manifest_to_peers()

        logger.info(f"Uploaded {filepath.name} → {file_id}")
        return file_id

    async def download(self, file_id: str, output: str | Path) -> None:
        """Download a file from the network.

        Pipeline: manifest → retrieve → decrypt → verify → reassemble → write.

        Args:
            file_id: The unique file identifier.
            output: Local path to write the reassembled file to.

        Raises:
            firecloud.exceptions.FileNotFoundError: If the file is not in
                the manifest or is tombstoned.
            ChunkNotFoundError: If chunks are irrecoverable.
            ChunkCorruptError: If integrity verification fails.
        """
        output = Path(output)
        entry = self.manifest.get_file(file_id)

        enc_key = self.network.encryption_key

        # Retrieve encrypted chunks
        peer_ids = list(self.connections.keys())
        distributor = Distributor(
            peers=peer_ids,
            local_node_id=self.node_id,
            fec_enabled=entry.fec_enabled,
        )
        encrypted_data_list = await distributor.retrieve(entry.chunks, self._client)

        # Decrypt and verify each chunk
        decrypted_chunks: list[Chunk] = []
        for i, enc_data in enumerate(encrypted_data_list):
            plaintext = decrypt_chunk(enc_data, enc_key)

            # When NOT using FEC, verify integrity against the manifest
            if not entry.fec_enabled and i < len(entry.chunks):
                expected_hash = entry.chunks[i].integrity_hash
                actual_hash = compute_integrity_hash(plaintext)
                if actual_hash != expected_hash:
                    raise ChunkCorruptError(
                        f"Integrity check failed for chunk {entry.chunks[i].chunk_id}"
                    )

            decrypted_chunks.append(
                Chunk(
                    index=i,
                    offset=0,
                    length=len(plaintext),
                    data=plaintext,
                    chunk_id="",
                    integrity_hash="",
                )
            )

        # Reassemble and write
        reassembled = reassemble_chunks(decrypted_chunks)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(reassembled)

        logger.info(f"Downloaded {entry.name} → {output}")

    async def delete(self, file_id: str) -> None:
        """Tombstone a file in the manifest and sync to peers.

        Args:
            file_id: The file to delete.

        Raises:
            firecloud.exceptions.FileNotFoundError: If the file is not found.
        """
        self.manifest.delete_file(file_id)
        await self._sync_manifest_to_peers()
        logger.info(f"Deleted file {file_id}")

    def list_files(self) -> list[dict]:
        """Return a list of all non-deleted files as plain dicts.

        Each dict contains: ``file_id``, ``name``, ``size``,
        ``chunk_count``, ``uploaded_at``, ``uploaded_by``,
        ``fec_enabled``, ``replication_factor``.
        """
        entries = self.manifest.list_files()
        return [
            {
                "file_id": e.file_id,
                "name": e.name,
                "size": e.size,
                "chunk_count": e.chunk_count,
                "uploaded_at": e.uploaded_at,
                "uploaded_by": e.uploaded_by,
                "fec_enabled": e.fec_enabled,
                "replication_factor": e.replication_factor,
            }
            for e in entries
        ]

    # ------------------------------------------------------------------
    # Networking
    # ------------------------------------------------------------------

    async def connect(self, address: str) -> None:
        """Connect to a peer by ``host:port`` string.

        Args:
            address: Peer address in ``host:port`` format.
        """
        host, port_str = address.rsplit(":", 1)
        port = int(port_str)
        peer_node_id = await self._client.connect(host, port)
        self._known_peers[peer_node_id] = (host, port)
        logger.info(f"Connected to peer {peer_node_id} at {host}:{port}")

    def status(self) -> dict:
        """Return a status dict describing this node."""
        return {
            "node_id": self.node_id,
            "network_id": self.network.network_id,
            "host": self.host,
            "port": self.port,
            "running": self._running,
            "peers_connected": len(self.connections),
            "files_stored": len(self.manifest.list_files()),
            "chunks_stored": len(self.chunk_store.list_chunks()),
            "storage_used": self.chunk_store.used_bytes(),
            "storage_available": self.chunk_store.available_bytes(),
        }

    def peers(self) -> list[dict]:
        """Return a list of known / connected peers."""
        result = []
        all_peer_ids = set(self.connections.keys()) | set(self._known_peers.keys())
        for pid in all_peer_ids:
            addr = self._known_peers.get(pid)
            result.append({
                "node_id": pid,
                "host": addr[0] if addr else "unknown",
                "port": addr[1] if addr else 0,
                "connected": pid in self.connections,
            })
        return result

    # ------------------------------------------------------------------
    # Connection management (called by transport layer)
    # ------------------------------------------------------------------

    def register_connection(self, peer_node_id: str, conn: PeerConnection) -> None:
        """Register an active peer connection (called by transport)."""
        self.connections[peer_node_id] = conn
        logger.debug(f"Registered connection with peer {peer_node_id}")

    def on_connection_closed(self, peer_node_id: str) -> None:
        """Handle a closed connection (called by PeerConnection)."""
        self.connections.pop(peer_node_id, None)
        logger.debug(f"Connection with peer {peer_node_id} closed")
        if self._running:
            asyncio.create_task(self._rereplicate_peer_chunks(peer_node_id))

    async def remove_node(self, node_id: str) -> None:
        """Explicitly remove a node from the network and trigger re-replication.

        This closes any connection, removes the node from known list,
        and re-replicates any of its chunks that were replicated on this network.
        """
        conn = self.connections.pop(node_id, None)
        if conn:
            try:
                await conn.close()
            except Exception:
                pass
        self._known_peers.pop(node_id, None)
        await self._rereplicate_peer_chunks(node_id)

    async def _rereplicate_peer_chunks(self, offline_node_id: str) -> None:
        """Scan manifest for chunks stored on the offline node and re-replicate them."""
        from firecloud.transport import MSG_STORE_CHUNK
        active_peers = [pid for pid in self.connections.keys() if pid != offline_node_id]
        if not active_peers:
            logger.info("No active peers available for re-replication.")
            return

        all_nodes = [self.node_id] + active_peers

        for entry in self.manifest.list_files():
            # zfec shares are handled separately; focus on standard replication for re-replication
            if entry.fec_enabled or entry.replication_factor < 2:
                continue

            updated = False
            for chunk_info in entry.chunks:
                if offline_node_id in chunk_info.stored_on:
                    chunk_info.stored_on = [nid for nid in chunk_info.stored_on if nid != offline_node_id]
                    
                    while len(chunk_info.stored_on) < entry.replication_factor:
                        candidate = None
                        for nid in all_nodes:
                            if nid not in chunk_info.stored_on:
                                candidate = nid
                                break
                        if not candidate:
                            break

                        chunk_data = None
                        if self.chunk_store.has(chunk_info.chunk_id):
                            chunk_data = self.chunk_store.retrieve(chunk_info.chunk_id)
                        else:
                            for nid in chunk_info.stored_on:
                                conn = self.connections.get(nid)
                                if conn:
                                    chunk_data = await conn.retrieve_chunk(chunk_info.chunk_id)
                                    if chunk_data:
                                        break

                        if chunk_data is not None:
                            try:
                                if candidate == self.node_id:
                                    self.chunk_store.store(chunk_info.chunk_id, chunk_data)
                                else:
                                    conn = self.connections.get(candidate)
                                    if conn:
                                        payload = chunk_info.chunk_id.encode("utf-8") + chunk_data
                                        await conn.send_message(MSG_STORE_CHUNK, payload)
                                chunk_info.stored_on.append(candidate)
                                updated = True
                                logger.info(f"Re-replicated chunk {chunk_info.chunk_id[:16]}... to {candidate}")
                            except Exception as exc:
                                logger.warning(f"Failed to re-replicate chunk {chunk_info.chunk_id} to {candidate}: {exc}")
                        else:
                            break

            if updated:
                self.manifest.add_file(entry)
                await self._sync_manifest_to_peers()

    def add_peer_discovered(self, node_id: str, host: str, port: int) -> None:
        """Record a newly discovered peer address (called by transport/discovery)."""
        if node_id != self.node_id:
            self._known_peers[node_id] = (host, port)

    # ------------------------------------------------------------------
    # Discovery callbacks
    # ------------------------------------------------------------------

    def _on_peer_discovered(self, node_id: str, host: str, port: int) -> None:
        """Callback when mDNS discovers a peer — schedule auto-connect."""
        if node_id == self.node_id or node_id in self.connections:
            return
        self._known_peers[node_id] = (host, port)
        asyncio.ensure_future(self._try_connect(node_id, host, port))

    def _on_peer_removed(self, node_id: str) -> None:
        """Callback when mDNS detects a peer departure."""
        conn = self.connections.pop(node_id, None)
        if conn:
            asyncio.ensure_future(conn.close())
        logger.debug(f"Peer {node_id} removed via mDNS")

    async def _try_connect(self, node_id: str, host: str, port: int) -> None:
        """Try to connect to a discovered peer, silently ignoring failures."""
        try:
            if node_id not in self.connections:
                await self._client.connect(host, port)
        except Exception as exc:
            logger.debug(f"Auto-connect to {node_id} at {host}:{port} failed: {exc}")

    # ------------------------------------------------------------------
    # Periodic tasks
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Send heartbeats to all connected peers every 30 seconds."""
        from firecloud.transport import MSG_HEARTBEAT

        try:
            while self._running:
                await asyncio.sleep(30)
                ts = datetime.now(timezone.utc).isoformat().encode("utf-8")
                payload = self.node_id.encode("utf-8") + b"|" + ts
                for conn in list(self.connections.values()):
                    try:
                        await conn.send_message(MSG_HEARTBEAT, payload)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass

    async def _manifest_sync_loop(self) -> None:
        """Periodically sync the manifest to all peers every 60 seconds."""
        try:
            while self._running:
                await asyncio.sleep(60)
                await self._sync_manifest_to_peers()
        except asyncio.CancelledError:
            pass

    async def _sync_manifest_to_peers(self) -> None:
        """Push the local manifest entries to all connected peers."""
        entries = self.manifest.to_entries()
        if not entries:
            return
        entries_dicts = [asdict(e) for e in entries]
        payload = json.dumps(entries_dicts).encode("utf-8")
        for conn in list(self.connections.values()):
            try:
                await conn.send_message(MSG_SYNC_MANIFEST, payload)
            except Exception as exc:
                logger.debug(f"Manifest sync failed for peer: {exc}")
