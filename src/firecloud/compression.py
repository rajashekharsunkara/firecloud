from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import zlib

try:
    import zstandard as zstd
except ModuleNotFoundError:
    zstd = None

ALGO_NONE = "none"

_ALREADY_COMPRESSED_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bz2",
    ".flac",
    ".gif",
    ".gz",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".png",
    ".webm",
    ".webp",
    ".xz",
    ".zip",
}

_TEXT_EXTENSIONS = {
    ".cfg",
    ".conf",
    ".csv",
    ".css",
    ".html",
    ".ini",
    ".json",
    ".log",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

_DOCUMENT_EXTENSIONS = {".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx"}

_BINARY_EXTENSIONS = {".bin", ".dat", ".dll", ".dylib", ".exe", ".obj", ".so"}


@dataclass(frozen=True)
class CompressionResult:
    algorithm: str
    payload: bytes


def compress_chunk(
    file_name: str,
    data: bytes,
    min_savings_ratio: float = 0.10,
    sample_size: int = 1024 * 1024,
) -> CompressionResult:
    if min_savings_ratio < 0 or min_savings_ratio >= 1:
        raise ValueError("min_savings_ratio must be in [0, 1)")
    if sample_size <= 0:
        raise ValueError("sample_size must be > 0")
    if not data:
        return CompressionResult(algorithm=ALGO_NONE, payload=data)

    suffix = Path(file_name).suffix.lower()
    if suffix in _ALREADY_COMPRESSED_EXTENSIONS:
        return CompressionResult(algorithm=ALGO_NONE, payload=data)

    level = _compression_level_for_extension(suffix)
    if level is None:
        sample = data[:sample_size]
        probe, _ = _compress_with_backend(sample, level=6)
        if not _is_worth_compressing(len(sample), len(probe), min_savings_ratio):
            return CompressionResult(algorithm=ALGO_NONE, payload=data)
        level = 6

    compressed, algo_prefix = _compress_with_backend(data, level=level)
    if not _is_worth_compressing(len(data), len(compressed), min_savings_ratio):
        return CompressionResult(algorithm=ALGO_NONE, payload=data)
    return CompressionResult(algorithm=f"{algo_prefix}:{level}", payload=compressed)


def decompress_chunk(algorithm: str, payload: bytes) -> bytes:
    if algorithm == ALGO_NONE:
        return payload
    if algorithm.startswith("zstd:"):
        if zstd is None:
            raise RuntimeError("zstandard package is required to decompress zstd payloads")
        return zstd.ZstdDecompressor().decompress(payload)
    if algorithm.startswith("zlib:"):
        return zlib.decompress(payload)
    raise ValueError(f"Unsupported compression algorithm: {algorithm}")


def _compress_with_backend(data: bytes, level: int) -> tuple[bytes, str]:
    if zstd is not None:
        return zstd.ZstdCompressor(level=level).compress(data), "zstd"
    return zlib.compress(data, level=level), "zlib"


def _compression_level_for_extension(suffix: str) -> int | None:
    if suffix in _TEXT_EXTENSIONS:
        return 9
    if suffix in _DOCUMENT_EXTENSIONS:
        return 6
    if suffix in _BINARY_EXTENSIONS:
        return 3
    return None


def _is_worth_compressing(original_size: int, compressed_size: int, min_savings_ratio: float) -> bool:
    required_max = int(original_size * (1 - min_savings_ratio))
    return compressed_size < required_max
