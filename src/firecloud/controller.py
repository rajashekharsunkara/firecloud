from __future__ import annotations

import json
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .chunking import split_bytes_fastcdc
from .compression import CompressionResult, compress_chunk, decompress_chunk
from .config import FireCloudConfig
from .crypto import (
    ENCRYPTION_OVERHEAD,
    KEY_SIZE,
    decrypt_xchacha20poly1305,
    encrypt_xchacha20poly1305,
)
from .fec import RaptorQCodec
from .hashing import blake3_hex, chunk_hash
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

    def _online_node_ids(
        self, *, required: int | None = None, prefer_http: bool = False
    ) -> list[str]:
        online_descriptors: list[NodeDescriptor] = []
        for node in self.metadata.list_nodes():
            if not node.online or not self.storage_client.has_node(node.node_id):
                continue
            try:
                descriptor = self.storage_client.node_descriptor(node.node_id)
            except ValueError:
                continue
            online_descriptors.append(descriptor)

        if prefer_http:
            http_nodes = [node.node_id for node in online_descriptors if node.kind == "http"]
            if required is None:
                if http_nodes:
                    return http_nodes
            elif len(http_nodes) >= required:
                return http_nodes

        return [node.node_id for node in online_descriptors]

    def _online_http_nodes_with_capacity(self) -> tuple[list[str], int]:
        node_ids: list[str] = []
        total_available = 0
        for node_id in self._online_node_ids(required=None, prefer_http=False):
            descriptor = self.storage_client.node_descriptor(node_id)
            if descriptor.kind != "http":
                continue
            try:
                stats = self.storage_client.node_storage_stats(node_id)
            except (TransportError, ValueError):
                continue
            available = max(0, int(stats.get("available_bytes", 0)))
            total_available += available
            if available > 0:
                node_ids.append(node_id)
        return node_ids, total_available

    def storage_availability_summary(self) -> dict[str, int]:
        online_nodes = self._online_node_ids(required=None, prefer_http=False)
        http_with_capacity, total_http_available_capacity = self._online_http_nodes_with_capacity()
        http_online = 0
        local_online = 0
        online_http_with_capacity = len(http_with_capacity)

        for node_id in online_nodes:
            descriptor = self.storage_client.node_descriptor(node_id)
            if descriptor.kind == "http":
                http_online += 1
            else:
                local_online += 1

        return {
            "online_nodes": len(online_nodes),
            "http_online": http_online,
            "local_online": local_online,
            "online_http_with_capacity": online_http_with_capacity,
            "total_http_available_capacity": total_http_available_capacity,
        }

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
        if kind != "http":
            raise ValueError("Only HTTP storage peers are allowed in decentralized mode")
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

    def _chunking_bounds(self) -> tuple[int, int, int]:
        max_plain_chunk_size = self.codec.payload_size - ENCRYPTION_OVERHEAD
        if max_plain_chunk_size <= 0:
            raise ValueError("Configured symbol size is too small to fit encrypted payloads")
        configured = self.config.chunking
        effective_max = min(configured.max_size, max_plain_chunk_size)
        effective_avg = min(configured.avg_size, effective_max)
        effective_min = min(configured.min_size, effective_avg)
        if effective_min <= 0:
            raise ValueError("Invalid effective chunking bounds")
        return effective_min, effective_avg, effective_max

    def _storage_chunk_id(self, chunk: ChunkRecord) -> str:
        chunk_ref = self.metadata.get_chunk_hash(chunk.chunk_id)
        return chunk_ref or chunk.chunk_id

    def _aad_for_chunk(self, file_id: str, chunk: ChunkRecord) -> bytes:
        chunk_ref = self.metadata.get_chunk_hash(chunk.chunk_id)
        if chunk_ref is not None:
            return chunk_ref.encode()
        return file_id.encode()

    @staticmethod
    def _symbol_hash(symbol_data: bytes) -> str:
        return blake3_hex(symbol_data)

    def _upload_content(self, file_name: str, file_bytes: bytes) -> str:
        if not file_name:
            raise ValueError("file_name cannot be empty")
        if self.config.decentralized_mode:
            online_nodes, total_available = self._online_http_nodes_with_capacity()
            if len(online_nodes) < self.config.fec.total_symbols:
                raise RuntimeError(
                    "Insufficient network storage peers with capacity. "
                    f"Need {self.config.fec.total_symbols} online HTTP peers."
                )
            if total_available < len(file_bytes):
                raise RuntimeError(
                    "Insufficient free storage capacity across online network peers."
                )
        else:
            online_nodes = self._online_node_ids(
                required=self.config.fec.total_symbols,
                prefer_http=True,
            )
            if len(online_nodes) < self.config.fec.total_symbols:
                raise RuntimeError(
                    "Not enough online nodes to satisfy total symbol requirement during upload"
                )
        file_id = uuid4().hex

        min_size, avg_size, max_size = self._chunking_bounds()
        chunks = split_bytes_fastcdc(
            file_bytes,
            min_size=min_size,
            avg_size=avg_size,
            max_size=max_size,
            normalization_level=self.config.chunking.normalization_level,
        )

        pending_chunks: list[dict[str, object]] = []
        pending_chunk_refs: list[tuple[str, str]] = []
        pending_symbols: list[tuple[str, str, int, str, str]] = []
        pending_dedup_chunks: list[dict[str, object]] = []
        pending_dedup_symbols: list[tuple[str, str, int, str, str]] = []
        pending_copied_symbol_chunks: list[tuple[str, str]] = []
        dedup_increment_counts: dict[str, int] = defaultdict(int)
        canonical_updates: dict[str, str] = {}
        dedup_catalog: dict[str, dict[str, object]] = {}

        for chunk_index, plain_chunk in enumerate(chunks):
            logical_chunk_id = f"{file_id}:{chunk_index}"
            hash_hex = chunk_hash(plain_chunk)

            catalog_entry = dedup_catalog.get(hash_hex)
            if catalog_entry is None:
                existing = self.metadata.get_dedup_chunk(hash_hex)
                if existing is not None:
                    source_chunk_id = existing.canonical_chunk_id
                    source_symbols = self.metadata.list_symbols(source_chunk_id)
                    if len(source_symbols) == 0:
                        source_chunk_id = self.metadata.find_chunk_for_hash(chunk_hash=hash_hex)
                        if source_chunk_id is None:
                            raise RuntimeError(f"Dedup index corruption for chunk hash {hash_hex}")
                        source_symbols = self.metadata.list_symbols(source_chunk_id)
                        if len(source_symbols) == 0:
                            raise RuntimeError(f"Dedup symbol index corruption for chunk hash {hash_hex}")
                        if source_chunk_id != existing.canonical_chunk_id:
                            canonical_updates[hash_hex] = source_chunk_id
                    catalog_entry = {
                        "plain_size": existing.plain_size,
                        "compressed_size": existing.compressed_size,
                        "compression": existing.compression,
                        "encrypted_size": existing.encrypted_size,
                        "source_kind": "db",
                        "canonical_chunk_id": source_chunk_id,
                        "symbols": [],
                    }
                    dedup_catalog[hash_hex] = catalog_entry
                else:
                    if self.config.compression.enabled:
                        compression_result = compress_chunk(
                            file_name=file_name,
                            data=plain_chunk,
                            min_savings_ratio=self.config.compression.min_savings_ratio,
                            sample_size=self.config.compression.sample_size,
                        )
                    else:
                        compression_result = CompressionResult(algorithm="none", payload=plain_chunk)
                    encrypted_chunk = encrypt_xchacha20poly1305(
                        key=self.master_key, plaintext=compression_result.payload, aad=hash_hex.encode()
                    )
                    encoded_chunk = self.codec.encode(encrypted_chunk)
                    chunk_symbols: list[tuple[str, int, str, str]] = []
                    for symbol_id, symbol_data in encoded_chunk.symbols.items():
                        target_node = online_nodes[(chunk_index + symbol_id) % len(online_nodes)]
                        relative_path = self.storage_client.put_symbol(
                            node_id=target_node,
                            chunk_id=hash_hex,
                            symbol_id=symbol_id,
                            symbol_data=symbol_data,
                        )
                        symbol_hash = self._symbol_hash(symbol_data)
                        pending_symbols.append(
                            (logical_chunk_id, target_node, symbol_id, relative_path, symbol_hash)
                        )
                        pending_dedup_symbols.append(
                            (hash_hex, target_node, symbol_id, relative_path, symbol_hash)
                        )
                        chunk_symbols.append((target_node, symbol_id, relative_path, symbol_hash))

                    pending_chunks.append(
                        {
                            "chunk_id": logical_chunk_id,
                            "file_id": file_id,
                            "chunk_index": chunk_index,
                            "plain_size": len(plain_chunk),
                            "compressed_size": len(compression_result.payload),
                            "compression": compression_result.algorithm,
                            "encrypted_size": len(encrypted_chunk),
                        }
                    )
                    pending_dedup_chunks.append(
                        {
                            "chunk_hash": hash_hex,
                            "canonical_chunk_id": logical_chunk_id,
                            "plain_size": len(plain_chunk),
                            "compressed_size": len(compression_result.payload),
                            "compression": compression_result.algorithm,
                            "encrypted_size": len(encrypted_chunk),
                            "ref_count": 1,
                        }
                    )
                    pending_chunk_refs.append((logical_chunk_id, hash_hex))
                    dedup_catalog[hash_hex] = {
                        "plain_size": len(plain_chunk),
                        "compressed_size": len(compression_result.payload),
                        "compression": compression_result.algorithm,
                        "encrypted_size": len(encrypted_chunk),
                        "source_kind": "pending",
                        "canonical_chunk_id": logical_chunk_id,
                        "symbols": chunk_symbols,
                    }
                    continue

            # Deduplicated chunk path (existing in DB or already seen in this upload)
            if catalog_entry is None:
                raise RuntimeError(f"Dedup catalog setup failure for chunk hash {hash_hex}")

            plain_size = int(catalog_entry["plain_size"])
            compressed_size = int(catalog_entry["compressed_size"])
            compression = str(catalog_entry["compression"])
            encrypted_size = int(catalog_entry["encrypted_size"])
            source_kind = str(catalog_entry["source_kind"])

            pending_chunks.append(
                {
                    "chunk_id": logical_chunk_id,
                    "file_id": file_id,
                    "chunk_index": chunk_index,
                    "plain_size": plain_size,
                    "compressed_size": compressed_size,
                    "compression": compression,
                    "encrypted_size": encrypted_size,
                }
            )
            pending_chunk_refs.append((logical_chunk_id, hash_hex))

            if source_kind == "db":
                source_chunk_id = str(catalog_entry["canonical_chunk_id"])
                pending_copied_symbol_chunks.append((source_chunk_id, logical_chunk_id))
            else:
                source_symbols = list(catalog_entry["symbols"])
                for node_id, symbol_id, symbol_path, symbol_hash in source_symbols:
                    pending_symbols.append((logical_chunk_id, node_id, symbol_id, symbol_path, symbol_hash))

            dedup_increment_counts[hash_hex] += 1

        self.metadata.commit_upload(
            file_id=file_id,
            file_name=file_name,
            file_size=len(file_bytes),
            chunks=pending_chunks,
            chunk_refs=pending_chunk_refs,
            copied_symbol_chunks=pending_copied_symbol_chunks,
            symbols=pending_symbols,
            dedup_chunks=pending_dedup_chunks,
            dedup_symbols=pending_dedup_symbols,
            dedup_increment_counts=dict(dedup_increment_counts),
            canonical_updates=canonical_updates,
        )

        self._append_audit_event(
            "file_uploaded",
            {
                "file_id": file_id,
                "file_name": file_name,
                "file_size": len(file_bytes),
                "chunk_count": len(chunks),
            },
        )
        return file_id

    def upload_file(self, file_path: Path) -> str:
        if not file_path.exists() or not file_path.is_file():
            raise ValueError(f"File does not exist: {file_path}")
        return self._upload_content(file_name=file_path.name, file_bytes=file_path.read_bytes())

    def upload_bytes(self, file_name: str, file_bytes: bytes) -> str:
        return self._upload_content(file_name=file_name, file_bytes=file_bytes)

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
                symbol_data = self.storage_client.get_symbol(
                    symbol.node_id, symbol.symbol_path
                )
                if symbol.symbol_hash and self._symbol_hash(symbol_data) != symbol.symbol_hash:
                    continue
                collected[symbol.symbol_id] = symbol_data
            except (TransportError, FileNotFoundError, ValueError):
                continue
        return collected

    def _download_content(self, file_id: str) -> tuple[FileRecord, bytes]:
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
            compressed_chunk = decrypt_xchacha20poly1305(
                key=self.master_key,
                payload=encrypted_chunk,
                aad=self._aad_for_chunk(file_id=file_id, chunk=chunk),
            )
            plain_chunk = decompress_chunk(chunk.compression, compressed_chunk)
            output.extend(plain_chunk[: chunk.plain_size])
        return file_record, bytes(output)

    def download_file(self, file_id: str, destination_path: Path) -> Path:
        file_record, output = self._download_content(file_id)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_bytes(output)
        self._append_audit_event(
            "file_downloaded",
            {
                "file_id": file_id,
                "file_name": file_record.file_name,
                "destination": str(destination_path),
                "size": len(output),
            },
        )
        return destination_path

    def download_file_bytes(self, file_id: str) -> tuple[str, bytes]:
        file_record, output = self._download_content(file_id)
        self._append_audit_event(
            "file_downloaded",
            {"file_id": file_id, "file_name": file_record.file_name, "destination": "<bytes>", "size": len(output)},
        )
        return file_record.file_name, output

    def delete_file(self, file_id: str) -> None:
        file_record = self.metadata.get_file(file_id)
        if file_record is None:
            raise ValueError(f"Unknown file_id: {file_id}")

        chunks = self.metadata.list_chunks(file_id)
        for chunk in chunks:
            chunk_hash_value = self.metadata.get_chunk_hash(chunk.chunk_id)
            self.metadata.delete_symbols(chunk.chunk_id)
            self.metadata.remove_chunk_dedup_ref(chunk.chunk_id)
            self.metadata.delete_chunk(chunk.chunk_id)

            if chunk_hash_value is None:
                continue
            record = self.metadata.decrement_dedup_ref_count(chunk_hash_value)
            if record.ref_count == 0:
                continue
            if record.canonical_chunk_id != chunk.chunk_id:
                continue
            replacement = self.metadata.find_chunk_for_hash(
                chunk_hash=chunk_hash_value,
                exclude_chunk_id=chunk.chunk_id,
            )
            if replacement is not None:
                self.metadata.set_dedup_canonical_chunk(chunk_hash_value, replacement)

        self.metadata.delete_file(file_id)
        self._append_audit_event(
            "file_deleted",
            {"file_id": file_id, "file_name": file_record.file_name, "chunk_count": len(chunks)},
        )

    def repair_file(self, file_id: str) -> int:
        file_record = self.metadata.get_file(file_id)
        if file_record is None:
            raise ValueError(f"Unknown file_id: {file_id}")
        if self.config.decentralized_mode:
            online_nodes, total_available = self._online_http_nodes_with_capacity()
            if len(online_nodes) < self.config.fec.source_symbols:
                raise RuntimeError(
                    "Insufficient network storage peers with capacity for repair."
                )
            if total_available <= 0:
                raise RuntimeError("No free network storage capacity available for repair.")
        else:
            online_nodes = self._online_node_ids(
                required=self.config.fec.source_symbols,
                prefer_http=True,
            )
            if len(online_nodes) < self.config.fec.source_symbols:
                raise RuntimeError(
                    "Not enough online nodes to repair. Need at least source_symbols online."
                )
        repaired_symbols = 0
        for chunk in self.metadata.list_chunks(file_id):
            chunk_hash = self.metadata.get_chunk_hash(chunk.chunk_id)
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
            storage_chunk_id = self._storage_chunk_id(chunk)
            for symbol_id, symbol_data in recoded.symbols.items():
                if symbol_id in existing_symbol_ids:
                    continue
                target_node = online_nodes[(chunk.chunk_index + symbol_id) % len(online_nodes)]
                relative_path = self.storage_client.put_symbol(
                    node_id=target_node,
                    chunk_id=storage_chunk_id,
                    symbol_id=symbol_id,
                    symbol_data=symbol_data,
                )
                self.metadata.add_symbol(
                    chunk_id=chunk.chunk_id,
                    node_id=target_node,
                    symbol_id=symbol_id,
                    symbol_path=relative_path,
                    symbol_hash=self._symbol_hash(symbol_data),
                )
                if chunk_hash is not None:
                    self.metadata.upsert_dedup_symbol(
                        chunk_hash=chunk_hash,
                        node_id=target_node,
                        symbol_id=symbol_id,
                        symbol_path=relative_path,
                        symbol_hash=self._symbol_hash(symbol_data),
                    )
                repaired_symbols += 1
        self._append_audit_event(
            "file_repaired", {"file_id": file_id, "repaired_symbols": repaired_symbols}
        )
        return repaired_symbols

    def run_dedup_gc(self, force: bool = False, limit: int | None = None) -> dict[str, int]:
        max_chunks = limit or self.config.dedup_gc.max_chunks_per_run
        due_before = None
        if not force:
            due = datetime.now(tz=UTC) - timedelta(days=self.config.dedup_gc.grace_period_days)
            due_before = due.isoformat()

        candidates = self.metadata.list_gc_pending_dedup_chunks(due_before=due_before, limit=max_chunks)
        deleted_chunks = 0
        deleted_symbols = 0
        failed_chunks = 0

        for candidate in candidates:
            symbols = self.metadata.list_dedup_symbols(candidate.chunk_hash)
            chunk_failed = False
            for symbol in symbols:
                if not self.storage_client.has_node(symbol.node_id):
                    chunk_failed = True
                    break
                try:
                    self.storage_client.delete_symbol(symbol.node_id, symbol.symbol_path)
                    deleted_symbols += 1
                except (TransportError, ValueError):
                    chunk_failed = True
                    break
            if chunk_failed:
                failed_chunks += 1
                continue
            self.metadata.delete_dedup_symbols(candidate.chunk_hash)
            self.metadata.cleanup_chunk_refs_for_hash(candidate.chunk_hash)
            self.metadata.delete_dedup_chunk(candidate.chunk_hash)
            deleted_chunks += 1

        summary = {
            "processed_chunks": len(candidates),
            "deleted_chunks": deleted_chunks,
            "deleted_symbols": deleted_symbols,
            "failed_chunks": failed_chunks,
        }
        self._append_audit_event("dedup_gc_run", {"force": force, **summary})
        return summary

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
