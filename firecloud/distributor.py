"""Chunk placement and retrieval.

Picks a strategy (local, replicated, or erasure coded) from the node count,
stores chunks accordingly, and reconstructs on the way back if nodes are
offline.
"""

import logging
import struct

from firecloud import fec
from firecloud.crypto import derive_chunk_id, compute_integrity_hash
from firecloud.exceptions import ChunkNotFoundError, InsufficientPeersError
from firecloud.manifest import ChunkInfo

logger = logging.getLogger("firecloud.distributor")

# Replicated chunks are stored on this many nodes.
REPLICATION_FACTOR = 2

# zfec refuses more than 256 blocks. K only controls how the payload is
# split, so cap it: ceil(1.5 * 170) = 255 fits, 171 would not.
_MAX_FEC_K = 170


class Distributor:
    """Orchestrates chunk distribution and retrieval strategies."""

    def __init__(
        self,
        peers: list[str],
        local_node_id: str,
        fec_enabled: bool = True,
        fec_threshold: int = 5,
        store_local: bool = True,
    ) -> None:
        """store_local=False means this node is not a storage target itself.

        One-shot CLI clients use that so chunks land on the durable peers;
        anything kept locally would vanish when the command exits.
        """
        self.peers = peers
        self.local_node_id = local_node_id
        self.fec_enabled = fec_enabled
        self.fec_threshold = fec_threshold
        self.store_local = store_local

    def _placement_nodes(self) -> list[str]:
        """Nodes eligible to hold chunks, in preference order.

        With store_local=False chunks go to peers only. If there are no
        peers at all, the local store is still the last resort.
        """
        if self.store_local:
            return [self.local_node_id] + self.peers
        if self.peers:
            return list(self.peers)
        return [self.local_node_id]

    def get_strategy(self) -> str:
        """Determine the distribution strategy based on the node count."""
        total_nodes = len(self.peers) + 1
        if total_nodes < 2:
            return "local"
        elif self.fec_enabled and total_nodes >= self.fec_threshold:
            return "erasure_coding"
        else:
            return "replication"

    async def _store_on(self, node_id: str, chunk_id: str, data: bytes, transport) -> bool:
        """Try to store a chunk on a single node, confirming success."""
        try:
            if node_id == self.local_node_id:
                transport.node.chunk_store.store(chunk_id, data)
                return True
            conn = transport.node.connections.get(node_id)
            if conn is None:
                return False
            return await conn.store_chunk(chunk_id, data)
        except Exception as exc:
            logger.warning(
                f"Failed to store chunk {chunk_id[:16]}... on {node_id}: {exc}"
            )
            return False

    async def _store_replicated(
        self, chunk_id: str, data: bytes, preferred: list[str], all_nodes: list[str],
        transport, copies: int,
    ) -> list[str]:
        """Store *copies* replicas, preferring *preferred* nodes.

        Falls back to any other node when a preferred target fails. Raises
        InsufficientPeersError if nobody accepted the chunk.
        """
        candidates = preferred + [n for n in all_nodes if n not in preferred]
        stored_on: list[str] = []
        for node_id in candidates:
            if len(stored_on) >= copies:
                break
            if await self._store_on(node_id, chunk_id, data, transport):
                stored_on.append(node_id)
        if not stored_on:
            raise InsufficientPeersError(
                f"Could not store chunk {chunk_id[:16]}... on any node"
            )
        if len(stored_on) < copies:
            logger.warning(
                f"Chunk {chunk_id[:16]}... stored on {len(stored_on)}/{copies} nodes"
            )
        return stored_on

    async def distribute(self, chunks: list, transport) -> list[ChunkInfo]:
        """Place encrypted chunks on the network; returns placement info."""
        strategy = self.get_strategy()
        all_nodes = self._placement_nodes()

        if strategy == "local":
            chunk_infos = []
            for c in chunks:
                transport.node.chunk_store.store(c.chunk_id, c.data)
                chunk_infos.append(
                    ChunkInfo(
                        chunk_id=c.chunk_id,
                        integrity_hash=c.integrity_hash,
                        index=c.index,
                        size=len(c.data),
                        stored_on=[self.local_node_id],
                    )
                )
            return chunk_infos

        elif strategy == "replication":
            chunk_infos = []
            for c in chunks:
                # Preferred targets: round-robin pair over all nodes.
                idx1 = c.index % len(all_nodes)
                idx2 = (c.index + 1) % len(all_nodes)
                preferred = [all_nodes[idx1], all_nodes[idx2]]

                stored_on = await self._store_replicated(
                    c.chunk_id, c.data, preferred, all_nodes, transport,
                    copies=REPLICATION_FACTOR,
                )

                chunk_infos.append(
                    ChunkInfo(
                        chunk_id=c.chunk_id,
                        integrity_hash=c.integrity_hash,
                        index=c.index,
                        size=len(c.data),
                        stored_on=stored_on,
                    )
                )
            return chunk_infos

        else:
            num_chunks = len(chunks)
            if num_chunks == 0:
                return []
            # K capped so N stays under zfec's block limit on big files.
            k = min(num_chunks, _MAX_FEC_K)
            n = fec.compute_n(k)

            # Header records the chunk count and sizes so the decoded
            # payload can be split back into the original chunks.
            header = struct.pack("!I", num_chunks) + b"".join(
                struct.pack("!I", len(c.data)) for c in chunks
            )
            payload = header + b"".join(c.data for c in chunks)

            shares = fec.encode(payload, k, n)

            chunk_infos = []
            hmac_key = transport.node.network.hmac_key

            for i, share_data in enumerate(shares):
                share_id = derive_chunk_id(share_data, hmac_key)
                share_hash = compute_integrity_hash(share_data)

                # Preferred target: round-robin; any other node as fallback.
                preferred = [all_nodes[i % len(all_nodes)]]
                stored_on = await self._store_replicated(
                    share_id, share_data, preferred, all_nodes, transport,
                    copies=1,
                )

                chunk_infos.append(
                    ChunkInfo(
                        chunk_id=share_id,
                        integrity_hash=share_hash,
                        index=i,
                        size=len(share_data),
                        stored_on=stored_on,
                    )
                )
            return chunk_infos

    async def retrieve(
        self,
        chunk_infos: list[ChunkInfo],
        transport,
        strategy: str | None = None,
        k: int | None = None,
    ) -> list[bytes]:
        """Retrieve chunks from peers, reconstructing via FEC if needed.

        *strategy* should come from the manifest entry (how the file was
        stored). Deriving it from the live peer count would misread FEC
        shares as replicated chunks, or vice versa, once the cluster has
        grown or shrunk since upload. *k* is the FEC threshold; None means
        infer it from the share count.
        """
        if strategy is None:
            strategy = self.get_strategy()

        if strategy != "erasure_coding":
            chunks_data = []
            for info in chunk_infos:
                chunk_data = None
                # Try primary stored nodes
                for node_id in info.stored_on:
                    if node_id == self.local_node_id:
                        if transport.node.chunk_store.has(info.chunk_id):
                            chunk_data = transport.node.chunk_store.retrieve(
                                info.chunk_id
                            )
                            break
                    else:
                        conn = transport.node.connections.get(node_id)
                        if conn:
                            chunk_data = await conn.retrieve_chunk(info.chunk_id)
                            if chunk_data:
                                break

                # Fallback to other connections if primary is down
                if chunk_data is None:
                    for node_id, conn in list(transport.node.connections.items()):
                        chunk_data = await conn.retrieve_chunk(info.chunk_id)
                        if chunk_data:
                            break

                # Absolute local fallback
                if chunk_data is None and self.local_node_id not in info.stored_on:
                    if transport.node.chunk_store.has(info.chunk_id):
                        chunk_data = transport.node.chunk_store.retrieve(
                            info.chunk_id
                        )

                if chunk_data is None:
                    raise ChunkNotFoundError(
                        f"Failed to retrieve chunk {info.chunk_id}"
                    )
                chunks_data.append(chunk_data)
            return chunks_data

        else:
            # chunk_infos are the N erasure-coded shares here.
            n = len(chunk_infos)
            if n == 0:
                return []
            if k is None:
                # Smallest K whose share count reaches N. Exact inverse of
                # compute_n for every N we produce.
                k = 1
                while fec.compute_n(k) < n:
                    k += 1

            retrieved_shares = []  # list of (index, share_data)

            for info in chunk_infos:
                share_data = None
                # Try local first
                if self.local_node_id in info.stored_on:
                    if transport.node.chunk_store.has(info.chunk_id):
                        share_data = transport.node.chunk_store.retrieve(
                            info.chunk_id
                        )

                # Try primary peer connections
                if share_data is None:
                    for node_id in info.stored_on:
                        if node_id != self.local_node_id:
                            conn = transport.node.connections.get(node_id)
                            if conn:
                                share_data = await conn.retrieve_chunk(
                                    info.chunk_id
                                )
                                if share_data:
                                    break

                # Try generic fallback peer connections
                if share_data is None:
                    for node_id, conn in list(transport.node.connections.items()):
                        share_data = await conn.retrieve_chunk(info.chunk_id)
                        if share_data:
                            break

                # A corrupt share poisons the whole reconstruction, so
                # treat a hash mismatch as a miss.
                if share_data is not None:
                    if compute_integrity_hash(share_data) != info.integrity_hash:
                        logger.warning(
                            f"Share {info.chunk_id[:16]}... failed integrity "
                            "check; treating as missing"
                        )
                        share_data = None

                if share_data is not None:
                    retrieved_shares.append((info.index, share_data))
                    if len(retrieved_shares) >= k:
                        break

            if len(retrieved_shares) < k:
                raise ChunkNotFoundError(
                    f"Insufficient shares to reconstruct file: need {k}, got {len(retrieved_shares)}"
                )

            payload = fec.decode(retrieved_shares, k)

            num_chunks = struct.unpack("!I", payload[:4])[0]
            sizes = []
            offset = 4
            for _ in range(num_chunks):
                sizes.append(
                    struct.unpack("!I", payload[offset : offset + 4])[0]
                )
                offset += 4

            chunks_data = []
            for size in sizes:
                chunks_data.append(payload[offset : offset + size])
                offset += size

            return chunks_data
