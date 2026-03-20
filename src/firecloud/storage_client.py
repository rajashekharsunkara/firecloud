from __future__ import annotations

from pathlib import Path

from .models import NodeDescriptor
from .transport import HttpNodeTransport, LocalNodeTransport, NodeTransport


class StorageClient:
    def __init__(
        self,
        nodes: list[NodeDescriptor],
        local_transport: NodeTransport | None = None,
        http_transport: NodeTransport | None = None,
    ) -> None:
        self._local_transport = local_transport or LocalNodeTransport()
        self._http_transport = http_transport or HttpNodeTransport()
        self._nodes: dict[str, NodeDescriptor] = {}
        self.set_nodes(nodes)

    def set_nodes(self, nodes: list[NodeDescriptor]) -> None:
        self._nodes = {node.node_id: node for node in nodes}

    def upsert_node(self, node: NodeDescriptor) -> None:
        self._nodes[node.node_id] = node

    def remove_node(self, node_id: str) -> None:
        self._nodes.pop(node_id, None)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def list_nodes(self) -> list[NodeDescriptor]:
        return [self._nodes[node_id] for node_id in sorted(self._nodes)]

    def node_descriptor(self, node_id: str) -> NodeDescriptor:
        node = self._nodes.get(node_id)
        if node is None:
            raise ValueError(f"Unknown node: {node_id}")
        return node

    def _transport_for(self, node: NodeDescriptor) -> NodeTransport:
        if node.kind == "local":
            return self._local_transport
        if node.kind == "http":
            return self._http_transport
        raise ValueError(f"Unsupported node kind: {node.kind}")

    def put_symbol(self, node_id: str, chunk_id: str, symbol_id: int, symbol_data: bytes) -> str:
        node = self.node_descriptor(node_id)
        transport = self._transport_for(node)
        return transport.put_symbol(
            node_id=node.node_id,
            endpoint=node.endpoint,
            chunk_id=chunk_id,
            symbol_id=symbol_id,
            symbol_data=symbol_data,
        )

    def get_symbol(self, node_id: str, symbol_path: str) -> bytes:
        node = self.node_descriptor(node_id)
        transport = self._transport_for(node)
        return transport.get_symbol(node_id=node.node_id, endpoint=node.endpoint, symbol_path=symbol_path)

    def has_symbol(self, node_id: str, symbol_path: str) -> bool:
        node = self.node_descriptor(node_id)
        transport = self._transport_for(node)
        return transport.has_symbol(node_id=node.node_id, endpoint=node.endpoint, symbol_path=symbol_path)

    def delete_symbol(self, node_id: str, symbol_path: str) -> None:
        node = self.node_descriptor(node_id)
        transport = self._transport_for(node)
        transport.delete_symbol(node_id=node.node_id, endpoint=node.endpoint, symbol_path=symbol_path)

    def symbol_count(self, node_id: str) -> int:
        node = self.node_descriptor(node_id)
        transport = self._transport_for(node)
        return transport.symbol_count(node_id=node.node_id, endpoint=node.endpoint)

    def local_symbol_path(self, node_id: str, symbol_path: str) -> Path | None:
        node = self._nodes.get(node_id)
        if node is None or node.kind != "local":
            return None
        return Path(node.endpoint) / symbol_path
