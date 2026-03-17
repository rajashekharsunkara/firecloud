from __future__ import annotations

import warnings
from dataclasses import dataclass

from pyraptorq import Decoder, Encoder


@dataclass(frozen=True)
class EncodedChunk:
    source_symbols: int
    total_symbols: int
    symbol_size: int
    original_size: int
    symbols: dict[int, bytes]


class RaptorQCodec:
    def __init__(self, source_symbols: int, total_symbols: int, symbol_size: int) -> None:
        if source_symbols <= 0:
            raise ValueError("source_symbols must be > 0")
        if total_symbols < source_symbols:
            raise ValueError("total_symbols must be >= source_symbols")
        if symbol_size <= 0:
            raise ValueError("symbol_size must be > 0")
        self.source_symbols = source_symbols
        self.total_symbols = total_symbols
        self.symbol_size = symbol_size
        self._payload_size = self.source_symbols * self.symbol_size

    @property
    def payload_size(self) -> int:
        return self._payload_size

    def encode(self, data: bytes) -> EncodedChunk:
        if len(data) > self._payload_size:
            raise ValueError(
                f"Chunk is too large for configured RaptorQ payload ({len(data)} > {self._payload_size})"
            )
        padded = data + b"\x00" * (self._payload_size - len(data))
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
            encoder = Encoder(padded, self.symbol_size)
        symbols = {symbol_id: encoder.gen_symbol(symbol_id) for symbol_id in range(self.total_symbols)}
        return EncodedChunk(
            source_symbols=self.source_symbols,
            total_symbols=self.total_symbols,
            symbol_size=self.symbol_size,
            original_size=len(data),
            symbols=symbols,
        )

    def decode(self, symbols: dict[int, bytes], original_size: int) -> bytes:
        if original_size > self._payload_size:
            raise ValueError("original_size exceeds configured payload size")
        if len(symbols) < self.source_symbols:
            raise ValueError(
                f"Need at least {self.source_symbols} symbols to decode; got {len(symbols)}"
            )
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
            decoder = Decoder(self.source_symbols, self.symbol_size, self._payload_size)

        for symbol_id in sorted(symbols):
            decoder.add_symbol(symbol_id, symbols[symbol_id])

        decoded = decoder.try_decode()
        if decoded is None:
            raise ValueError("RaptorQ decode failed with provided symbols")
        return decoded[:original_size]
