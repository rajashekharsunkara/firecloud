from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .chunking import split_bytes
from .config import FireCloudConfig
from .crypto import (
    ENCRYPTION_OVERHEAD,
    KEY_SIZE,
    decrypt_xchacha20poly1305,
    encrypt_xchacha20poly1305,
)
from .fec import RaptorQCodec
from .hashing import blake3_hex
from .metadata import AuditEvent, ChunkRecord, FileRecord, MetadataStore
from .models import NodeDescriptor
from .storage_client import StorageClient
from .transport import TransportError


@dataclass(frozen=True)
class NodeStatusView:
    node_id: str
    endpoint: str
    kind: str
    online: bool
    symbol_count: int


class FireCloudController:
    def __init__(self, config: FireCloudConfig) -> None:
        self.config = config
        self.config.ensure_dirs()
        self.codec = RaptorQCodec(
            source_symbols=self.config.fec.source_symbols,
            total_symbols=self.config.fec.total_symbols,
            symbol_size=self.config.fec.symbol_size,
        )
        self.metadata = MetadataStore(self.config.db_path)
        self.master_key = self._load_or_create_master_key(self.config.master_key_path)
        self.storage_client = StorageClient(nodes=self._configured_nodes())
        self._initialize_nodes()

    def _configured_nodes(self) -> list[NodeDescriptor]:
        return [
            NodeDescriptor(node_id=node.node_id, endpoint=node.endpoint, kind=node.kind)
            for node in self.config.node_definitions()
        ]

    def _load_or_create_master_key(self, key_path: Path) -> bytes:
        if key_path.exists():
            key = key_path.read_bytes()
            if len(key) != KEY_SIZE:
                raise ValueError(f"Invalid master key length in {key_path}")
            return key
        key = secrets.token_bytes(KEY_SIZE)
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        return key

    def _initialize_nodes(self) -> None:
        for node in self.storage_client.list_nodes():
            self.metadata.upsert_node(
                node_id=node.node_id,
                status="online",
                endpoint=node.endpoint,
                kind=node.kind,
            )
        for row in self.metadata.list_nodes():
            if self.storage_client.has_node(row.node_id):
                continue
            try:
                self.storage_client.upsert_node(
                    NodeDescriptor(node_id=row.node_id, endpoint=row.endpoint, kind=row.kind)
                )
            except ValueError:
                continue

    def _online_node_ids(self) -> list[str]:
        return [
            node.node_id
            for node in self.metadata.list_nodes()
            if node.online and self.storage_client.has_node(node.node_id)
        ]

    @staticmethod
    def _canonical_payload(payload: dict[str, object]) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _append_audit_event(self, event_type: str, payload: dict[str, object]) -> None:
        payload_json = self._canonical_payload(payload)
        prev_hash = self.metadata.latest_event_hash()
        event_hash = blake3_hex(f"{prev_hash}|{event_type}|{payload_json}".encode())
        self.metadata.append_audit_event(
            event_type=event_type,
            payload=payload,
            prev_hash=prev_hash,
            event_hash=event_hash,
        )

    def list_nodes(self) -> list[NodeStatusView]:
        node_rows = self.metadata.list_nodes()
        result: list[NodeStatusView] = []
        for row in node_rows:
            if not self.storage_client.has_node(row.node_id):
                continue
            symbol_count = 0
            try:
                symbol_count = self.storage_client.symbol_count(row.node_id)
            except (ValueError, TransportError):
                symbol_count = 0
            result.append(
                NodeStatusView(
                    node_id=row.node_id,
                    endpoint=row.endpoint,
                    kind=row.kind,
                    online=row.online,
                    symbol_count=symbol_count,
                )
            )
        return result

    def set_node_online(self, node_id: str, online: bool) -> None:
        if self.metadata.get_node(node_id) is None:
            raise ValueError(f"Unknown node: {node_id}")
        status = "online" if online else "offline"
        self.metadata.set_node_status(node_id, status)
        self._append_audit_event("node_status_changed", {"node_id": node_id, "status": status})

    def add_node(self, node_id: str, endpoint: str, kind: str = "local") -> None:
        descriptor = NodeDescriptor(node_id=node_id, endpoint=endpoint, kind=kind)
        self.storage_client.upsert_node(descriptor)
        self.metadata.upsert_node(
            node_id=descriptor.node_id,
            status="online",
            endpoint=descriptor.endpoint,
            kind=descriptor.kind,
        )
        self._append_audit_event(
            "node_added",
            {"node_id": descriptor.node_id, "endpoint": descriptor.endpoint, "kind": descriptor.kind},
        )

    def remove_node(self, node_id: str) -> None:
        if self.metadata.get_node(node_id) is None:
            raise ValueError(f"Unknown node: {node_id}")
        self.storage_client.remove_node(node_id)
        self.metadata.remove_node(node_id)
        self._append_audit_event("node_removed", {"node_id": node_id})

    def list_files(self) -> list[FileRecord]:
        return self.metadata.list_files()

    def upload_file(self, file_path: Path) -> str:
        if not file_path.exists() or not file_path.is_file():
            raise ValueError(f"File does not exist: {file_path}")
        online_nodes = self._online_node_ids()
        if len(online_nodes) < self.config.fec.total_symbols:
            raise RuntimeError(
                "Not enough online nodes to satisfy total symbol requirement during upload"
            )
        file_bytes = file_path.read_bytes()
        file_id = uuid4().hex
        self.metadata.create_file(file_id=file_id, file_name=file_path.name, file_size=len(file_bytes))

        max_plain_chunk_size = self.codec.payload_size - ENCRYPTION_OVERHEAD
        if max_plain_chunk_size <= 0:
            raise ValueError("Configured symbol size is too small to fit encrypted payloads")
        chunks = split_bytes(file_bytes, max_plain_chunk_size)
        for chunk_index, plain_chunk in enumerate(chunks):
            encrypted_chunk = encrypt_xchacha20poly1305(
                key=self.master_key, plaintext=plain_chunk, aad=file_id.encode()
            )
            encoded_chunk = self.codec.encode(encrypted_chunk)
            chunk_id = f"{file_id}:{chunk_index}"
            self.metadata.add_chunk(
                chunk_id=chunk_id,
                file_id=file_id,
                chunk_index=chunk_index,
                plain_size=len(plain_chunk),
                encrypted_size=len(encrypted_chunk),
            )
            for symbol_id, symbol_data in encoded_chunk.symbols.items():
                target_node = online_nodes[(chunk_index + symbol_id) % len(online_nodes)]
                relative_path = self.storage_client.put_symbol(
                    node_id=target_node,
                    chunk_id=chunk_id,
                    symbol_id=symbol_id,
                    symbol_data=symbol_data,
                )
                self.metadata.add_symbol(
                    chunk_id=chunk_id,
                    node_id=target_node,
                    symbol_id=symbol_id,
                    symbol_path=relative_path,
                )

        self._append_audit_event(
            "file_uploaded",
            {
                "file_id": file_id,
                "file_name": file_path.name,
                "file_size": len(file_bytes),
                "chunk_count": len(chunks),
            },
        )
        return file_id

    def _collect_chunk_symbols(self, chunk: ChunkRecord) -> dict[int, bytes]:
        node_state = {node.node_id: node.online for node in self.metadata.list_nodes()}
        collected: dict[int, bytes] = {}
        for symbol in self.metadata.list_symbols(chunk.chunk_id):
            if symbol.symbol_id in collected:
                continue
            if not node_state.get(symbol.node_id, False):
                continue
            if not self.storage_client.has_node(symbol.node_id):
                continue
            try:
                if not self.storage_client.has_symbol(symbol.node_id, symbol.symbol_path):
                    continue
                collected[symbol.symbol_id] = self.storage_client.get_symbol(
                    symbol.node_id, symbol.symbol_path
                )
            except (TransportError, FileNotFoundError, ValueError):
                continue
        return collected

    def download_file(self, file_id: str, destination_path: Path) -> Path:
        file_record = self.metadata.get_file(file_id)
        if file_record is None:
            raise ValueError(f"Unknown file_id: {file_id}")

        output = bytearray()
        chunks = self.metadata.list_chunks(file_id)
        for chunk in chunks:
            collected = self._collect_chunk_symbols(chunk)
            if len(collected) < self.config.fec.source_symbols:
                raise RuntimeError(
                    f"Not enough symbols to decode chunk {chunk.chunk_id}: "
                    f"need {self.config.fec.source_symbols}, found {len(collected)}"
                )
            encrypted_chunk = self.codec.decode(collected, original_size=chunk.encrypted_size)
            plain_chunk = decrypt_xchacha20poly1305(
                key=self.master_key, payload=encrypted_chunk, aad=file_id.encode()
            )
            output.extend(plain_chunk[: chunk.plain_size])

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_bytes(bytes(output))
        self._append_audit_event(
            "file_downloaded",
            {"file_id": file_id, "destination": str(destination_path), "size": len(output)},
        )
        return destination_path

    def repair_file(self, file_id: str) -> int:
        file_record = self.metadata.get_file(file_id)
        if file_record is None:
            raise ValueError(f"Unknown file_id: {file_id}")
        online_nodes = self._online_node_ids()
        if len(online_nodes) < self.config.fec.source_symbols:
            raise RuntimeError(
                "Not enough online nodes to repair. Need at least source_symbols online."
            )
        repaired_symbols = 0
        for chunk in self.metadata.list_chunks(file_id):
            collected = self._collect_chunk_symbols(chunk)
            if len(collected) < self.config.fec.source_symbols:
                raise RuntimeError(
                    f"Cannot repair chunk {chunk.chunk_id}: insufficient symbols available"
                )
            existing_symbol_ids = set(collected.keys())
            if len(existing_symbol_ids) >= self.config.fec.total_symbols:
                continue

            encrypted_chunk = self.codec.decode(collected, original_size=chunk.encrypted_size)
            recoded = self.codec.encode(encrypted_chunk)
            for symbol_id, symbol_data in recoded.symbols.items():
                if symbol_id in existing_symbol_ids:
                    continue
                target_node = online_nodes[(chunk.chunk_index + symbol_id) % len(online_nodes)]
                relative_path = self.storage_client.put_symbol(
                    node_id=target_node,
                    chunk_id=chunk.chunk_id,
                    symbol_id=symbol_id,
                    symbol_data=symbol_data,
                )
                self.metadata.add_symbol(
                    chunk_id=chunk.chunk_id,
                    node_id=target_node,
                    symbol_id=symbol_id,
                    symbol_path=relative_path,
                )
                repaired_symbols += 1
        self._append_audit_event(
            "file_repaired", {"file_id": file_id, "repaired_symbols": repaired_symbols}
        )
        return repaired_symbols

    def audit_events(self, limit: int = 200) -> list[AuditEvent]:
        return self.metadata.list_audit_events(limit=limit)

    def verify_audit_chain(self) -> tuple[bool, str]:
        prev_hash = "GENESIS"
        events = self.metadata.list_audit_events_ascending()
        for event in events:
            payload_json = self._canonical_payload(event.payload)
            expected_hash = blake3_hex(
                f"{event.prev_hash}|{event.event_type}|{payload_json}".encode()
            )
            if event.prev_hash != prev_hash:
                return (
                    False,
                    f"Audit chain broken at sequence {event.sequence}: invalid prev_hash linkage",
                )
            if event.event_hash != expected_hash:
                return (
                    False,
                    f"Audit chain broken at sequence {event.sequence}: hash mismatch",
                )
            prev_hash = event.event_hash
        return True, f"Audit chain verified ({len(events)} events)"

    def local_symbol_path(self, node_id: str, symbol_path: str) -> Path | None:
        return self.storage_client.local_symbol_path(node_id=node_id, symbol_path=symbol_path)
