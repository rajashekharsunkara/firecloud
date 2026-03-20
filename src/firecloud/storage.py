from __future__ import annotations

from pathlib import Path
import re

_CHUNK_ID_RE = re.compile(r"^[A-Za-z0-9:._-]+$")


class NodeStore:
    def __init__(self, node_id: str, root_dir: Path) -> None:
        self.node_id = node_id
        self.root_dir = root_dir
        self.symbols_dir = self.root_dir / "symbols"
        self.symbols_dir.mkdir(parents=True, exist_ok=True)

    def _validate_chunk_id(self, chunk_id: str) -> None:
        if not chunk_id or not _CHUNK_ID_RE.fullmatch(chunk_id):
            raise ValueError("Invalid chunk_id")

    def _validate_symbol_id(self, symbol_id: int) -> None:
        if symbol_id < 0:
            raise ValueError("symbol_id must be >= 0")

    def _safe_relative_path(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ValueError("Path must be relative")
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValueError("Path traversal is not allowed")
        resolved = (self.root_dir / candidate).resolve()
        if not resolved.is_relative_to(self.root_dir.resolve()):
            raise ValueError("Path traversal is not allowed")
        return resolved

    def _chunk_dir(self, chunk_id: str) -> Path:
        self._validate_chunk_id(chunk_id)
        directory = self.symbols_dir / chunk_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def put_symbol(self, chunk_id: str, symbol_id: int, symbol_data: bytes) -> str:
        self._validate_symbol_id(symbol_id)
        file_path = self._chunk_dir(chunk_id) / f"{symbol_id}.bin"
        file_path.write_bytes(symbol_data)
        return file_path.relative_to(self.root_dir).as_posix()

    def get_symbol(self, relative_path: str) -> bytes:
        return self._safe_relative_path(relative_path).read_bytes()

    def has_symbol(self, relative_path: str) -> bool:
        try:
            return self._safe_relative_path(relative_path).exists()
        except ValueError:
            return False

    def symbol_count(self) -> int:
        return sum(1 for _ in self.symbols_dir.rglob("*.bin"))

    def delete_symbol(self, relative_path: str) -> None:
        path = self._safe_relative_path(relative_path)
        if path.exists():
            path.unlink()
