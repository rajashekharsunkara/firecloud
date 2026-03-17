from __future__ import annotations

from pathlib import Path


class NodeStore:
    def __init__(self, node_id: str, root_dir: Path) -> None:
        self.node_id = node_id
        self.root_dir = root_dir
        self.symbols_dir = self.root_dir / "symbols"
        self.symbols_dir.mkdir(parents=True, exist_ok=True)

    def _chunk_dir(self, chunk_id: str) -> Path:
        directory = self.symbols_dir / chunk_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def put_symbol(self, chunk_id: str, symbol_id: int, symbol_data: bytes) -> str:
        file_path = self._chunk_dir(chunk_id) / f"{symbol_id}.bin"
        file_path.write_bytes(symbol_data)
        return str(file_path.relative_to(self.root_dir))

    def get_symbol(self, relative_path: str) -> bytes:
        return (self.root_dir / relative_path).read_bytes()

    def has_symbol(self, relative_path: str) -> bool:
        return (self.root_dir / relative_path).exists()

    def symbol_count(self) -> int:
        return sum(1 for _ in self.symbols_dir.rglob("*.bin"))
