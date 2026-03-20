from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ChunkingConfig:
    min_size: int = 64 * 1024
    avg_size: int = 1024 * 1024
    max_size: int = 4 * 1024 * 1024
    normalization_level: int = 2

    def __post_init__(self) -> None:
        if self.min_size <= 0:
            raise ValueError("min_size must be > 0")
        if self.avg_size <= 0:
            raise ValueError("avg_size must be > 0")
        if self.max_size <= 0:
            raise ValueError("max_size must be > 0")
        if self.min_size > self.avg_size:
            raise ValueError("min_size must be <= avg_size")
        if self.avg_size > self.max_size:
            raise ValueError("avg_size must be <= max_size")
        if self.normalization_level < 0 or self.normalization_level > 3:
            raise ValueError("normalization_level must be between 0 and 3")


@dataclass(frozen=True)
class CompressionConfig:
    enabled: bool = True
    min_savings_ratio: float = 0.10
    sample_size: int = 1024 * 1024

    def __post_init__(self) -> None:
        if self.min_savings_ratio < 0 or self.min_savings_ratio >= 1:
            raise ValueError("min_savings_ratio must be in [0, 1)")
        if self.sample_size <= 0:
            raise ValueError("sample_size must be > 0")


@dataclass(frozen=True)
class DedupGCConfig:
    grace_period_days: int = 30
    max_chunks_per_run: int = 1000

    def __post_init__(self) -> None:
        if self.grace_period_days < 0:
            raise ValueError("grace_period_days must be >= 0")
        if self.max_chunks_per_run <= 0:
            raise ValueError("max_chunks_per_run must be > 0")


@dataclass(frozen=True)
class FECConfig:
    source_symbols: int = 3
    total_symbols: int = 5
    symbol_size: int = 64 * 1024

    def __post_init__(self) -> None:
        if self.source_symbols <= 0:
            raise ValueError("source_symbols must be > 0")
        if self.total_symbols < self.source_symbols:
            raise ValueError("total_symbols must be >= source_symbols")
        if self.symbol_size <= 0:
            raise ValueError("symbol_size must be > 0")

    @property
    def chunk_size(self) -> int:
        return self.source_symbols * self.symbol_size


@dataclass
class NodeConfig:
    node_id: str
    endpoint: str
    kind: Literal["local", "http"] = "local"

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("node_id cannot be empty")
        if not self.endpoint:
            raise ValueError("endpoint cannot be empty")
        if self.kind not in {"local", "http"}:
            raise ValueError("kind must be one of: local, http")


@dataclass
class FireCloudConfig:
    root_dir: Path = Path(".firecloud")
    node_count: int = 5
    nodes: tuple[NodeConfig, ...] | None = None
    fec: FECConfig = field(default_factory=FECConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    dedup_gc: DedupGCConfig = field(default_factory=DedupGCConfig)
    db_filename: str = "metadata.db"
    master_key_filename: str = "master.key"

    def __post_init__(self) -> None:
        if self.nodes is not None:
            if len(self.nodes) == 0:
                raise ValueError("nodes cannot be empty")
            node_ids = [node.node_id for node in self.nodes]
            if len(node_ids) != len(set(node_ids)):
                raise ValueError("Duplicate node_id values are not allowed")
            effective_node_count = len(self.nodes)
        else:
            effective_node_count = self.node_count
            if self.node_count <= 0:
                raise ValueError("node_count must be > 0")
        if effective_node_count < self.fec.total_symbols:
            raise ValueError("node_count must be >= total_symbols")

    @property
    def db_path(self) -> Path:
        return self.root_dir / self.db_filename

    @property
    def master_key_path(self) -> Path:
        return self.root_dir / self.master_key_filename

    @property
    def nodes_dir(self) -> Path:
        return self.root_dir / "nodes"

    def node_data_dir(self, node_id: str) -> Path:
        return self.nodes_dir / node_id

    def node_definitions(self) -> list[NodeConfig]:
        if self.nodes is not None:
            return list(self.nodes)
        return [
            NodeConfig(node_id=f"node-{idx}", endpoint=str(self.node_data_dir(f"node-{idx}")), kind="local")
            for idx in range(1, self.node_count + 1)
        ]

    def ensure_dirs(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.nodes_dir.mkdir(parents=True, exist_ok=True)
        for node in self.node_definitions():
            if node.kind == "local":
                Path(node.endpoint).mkdir(parents=True, exist_ok=True)
