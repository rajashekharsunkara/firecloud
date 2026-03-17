from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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
class FireCloudConfig:
    root_dir: Path = Path(".firecloud")
    node_count: int = 5
    fec: FECConfig = field(default_factory=FECConfig)
    db_filename: str = "metadata.db"
    master_key_filename: str = "master.key"

    def __post_init__(self) -> None:
        if self.node_count < self.fec.total_symbols:
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

    def ensure_dirs(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.nodes_dir.mkdir(parents=True, exist_ok=True)
