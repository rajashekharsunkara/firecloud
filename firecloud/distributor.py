"""FireCloud Distributor Engine.

Decides the placement of chunks (local, replicated, or erasure coded)
based on the network peer count, and retrieves them, performing
erasure coding reconstruction if nodes are offline.
"""

import math
import struct

from firecloud import fec
from firecloud.crypto import derive_chunk_id, compute_integrity_hash
from firecloud.exceptions import ChunkNotFoundError
from firecloud.manifest import ChunkInfo

# Message type constant needed for peer socket commands
MSG_STORE_CHUNK = 0x10


class Distributor:
    """Orchestrates chunk distribution and retrieval strategies."""

    def __init__(
        self,
        peers: list[str],
        local_node_id: str,
        fec_enabled: bool = True,
        fec_threshold: int = 5,
    ) -> None:
        """Initialize the distributor.

        Args:
            peers: List of active peer node IDs.
            local_node_id: The ID of this local node.
            fec_enabled: Whether FEC is allowed.
            fec_threshold: The node count threshold to use FEC.
        """
        self.peers = peers
        self.local_node_id = local_node_id
        self.fec_enabled = fec_enabled
        self.fec_threshold = fec_threshold

    def get_strategy(self) -> str:
        """Determine the distribution strategy based on the node count."""
        total_nodes = len(self.peers) + 1
        if total_nodes < 2:
            return "local"
        elif self.fec_enabled and total_nodes >= self.fec_threshold:
            return "erasure_coding"
        else:
            return "replication"

    async def distribute(self, chunks: list, transport) -> list[ChunkInfo]:
        """Distribute chunks across peers. Returns placement info.

        Args:
            chunks: List of Chunk objects containing encrypted data.
            transport: The transport client/manager containing connections.
        """
        strategy = self.get_strategy()
        all_nodes = [self.local_node_id] + self.peers

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
                # Store on 2 nodes (replication factor = 2) using round-robin
                idx1 = c.index % len(all_nodes)
                idx2 = (c.index + 1) % len(all_nodes)
                nodes_to_store = [all_nodes[idx1], all_nodes[idx2]]

                for node_id in nodes_to_store:
                    if node_id == self.local_node_id:
                        transport.node.chunk_store.store(c.chunk_id, c.data)
                    else:
                        conn = transport.node.connections.get(node_id)
                        if conn:
                            payload = c.chunk_id.encode("utf-8") + c.data
                            await conn.send_message(MSG_STORE_CHUNK, payload)

                chunk_infos.append(
                    ChunkInfo(
                        chunk_id=c.chunk_id,
                        integrity_hash=c.integrity_hash,
                        index=c.index,
                        size=len(c.data),
                        stored_on=nodes_to_store,
                    )
                )
            return chunk_infos

        else:
            # erasure_coding strategy
            k = len(chunks)
            if k == 0:
                return []
            n = fec.compute_n(k)

            # Prepend a header containing the chunk count and each chunk's size
            header = struct.pack("!I", k) + b"".join(
                struct.pack("!I", len(c.data)) for c in chunks
            )
            payload = header + b"".join(c.data for c in chunks)

            # Encode into N shares
            shares = fec.encode(payload, k, n)

            chunk_infos = []
            hmac_key = transport.node.network.hmac_key

            for i, share_data in enumerate(shares):
                share_id = derive_chunk_id(share_data, hmac_key)
                share_hash = compute_integrity_hash(share_data)

                # Store share on a node round-robin
                node_id = all_nodes[i % len(all_nodes)]
                if node_id == self.local_node_id:
                    transport.node.chunk_store.store(share_id, share_data)
                else:
                    conn = transport.node.connections.get(node_id)
                    if conn:
                        store_payload = share_id.encode("utf-8") + share_data
                        await conn.send_message(MSG_STORE_CHUNK, store_payload)

                chunk_infos.append(
                    ChunkInfo(
                        chunk_id=share_id,
                        integrity_hash=share_hash,
                        index=i,
                        size=len(share_data),
                        stored_on=[node_id],
                    )
                )
            return chunk_infos

    async def retrieve(self, chunk_infos: list[ChunkInfo], transport) -> list[bytes]:
        """Retrieve chunks from peers, with FEC reconstruction if needed.

        Args:
            chunk_infos: List of ChunkInfo objects representing the placement of
                chunks or shares.
            transport: The transport client/manager containing connections.
        """
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
                    for node_id, conn in transport.node.connections.items():
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
            # erasure_coding strategy: chunk_infos are the N shares.
            # Determine threshold K from N.
            n = len(chunk_infos)
            if n == 0:
                return []
            k = 1
            while math.ceil(k * 1.5) < n:
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
                    for node_id, conn in transport.node.connections.items():
                        share_data = await conn.retrieve_chunk(info.chunk_id)
                        if share_data:
                            break

                if share_data is not None:
                    retrieved_shares.append((info.index, share_data))
                    if len(retrieved_shares) >= k:
                        break

            if len(retrieved_shares) < k:
                raise ChunkNotFoundError(
                    f"Insufficient shares to reconstruct file: need {k}, got {len(retrieved_shares)}"
                )

            # Reconstruct original payload
            payload = fec.decode(retrieved_shares, k)

            # Parse header
            num_chunks = struct.unpack("!I", payload[:4])[0]
            sizes = []
            offset = 4
            for _ in range(num_chunks):
                sizes.append(
                    struct.unpack("!I", payload[offset : offset + 4])[0]
                )
                offset += 4

            # Split payload into original encrypted chunks
            chunks_data = []
            for size in sizes:
                chunks_data.append(payload[offset : offset + size])
                offset += size

            return chunks_data
