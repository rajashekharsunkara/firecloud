from __future__ import annotations

import warnings
from dataclasses import dataclass

try:
    from pyraptorq import Decoder, Encoder

    _HAS_PYRAPTORQ = True
except ModuleNotFoundError:
    Decoder = None
    Encoder = None
    _HAS_PYRAPTORQ = False

_GF_EXP: list[int] = [0] * 512
_GF_LOG: list[int] = [0] * 256
_GF_MUL_TABLE: list[list[int]] = [[0] * 256 for _ in range(256)]


def _init_gf256_tables() -> None:
    primitive_polynomial = 0x11D
    value = 1
    for index in range(255):
        _GF_EXP[index] = value
        _GF_LOG[value] = index
        value <<= 1
        if value & 0x100:
            value ^= primitive_polynomial
    for index in range(255, 512):
        _GF_EXP[index] = _GF_EXP[index - 255]
    for left in range(256):
        for right in range(256):
            _GF_MUL_TABLE[left][right] = _gf_mul_slow(left, right)


def _gf_mul_slow(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    return _GF_EXP[_GF_LOG[left] + _GF_LOG[right]]


def _gf_mul(left: int, right: int) -> int:
    return _GF_MUL_TABLE[left][right]


def _gf_inv(value: int) -> int:
    if value == 0:
        raise ValueError("Cannot invert zero in GF(256)")
    return _GF_EXP[255 - _GF_LOG[value]]


def _gf_pow(base: int, power: int) -> int:
    if power == 0:
        return 1
    if base == 0:
        return 0
    return _GF_EXP[(_GF_LOG[base] * power) % 255]


def _invert_matrix_gf256(matrix: list[list[int]]) -> list[list[int]]:
    size = len(matrix)
    augmented = [
        row[:] + [1 if row_index == col_index else 0 for col_index in range(size)]
        for row_index, row in enumerate(matrix)
    ]
    for column in range(size):
        pivot = None
        for row_index in range(column, size):
            if augmented[row_index][column] != 0:
                pivot = row_index
                break
        if pivot is None:
            raise ValueError("RaptorQ decode failed with provided symbols")
        if pivot != column:
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]

        pivot_value = augmented[column][column]
        inverse_pivot = _gf_inv(pivot_value)
        for cell in range(column, size * 2):
            augmented[column][cell] = _gf_mul(augmented[column][cell], inverse_pivot)

        for row_index in range(size):
            if row_index == column:
                continue
            factor = augmented[row_index][column]
            if factor == 0:
                continue
            for cell in range(column, size * 2):
                augmented[row_index][cell] ^= _gf_mul(factor, augmented[column][cell])

    return [row[size:] for row in augmented]


_init_gf256_tables()


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
        if not _HAS_PYRAPTORQ and self.total_symbols > 255:
            raise ValueError("Fallback FEC supports at most 255 total symbols")

    @property
    def payload_size(self) -> int:
        return self._payload_size

    def encode(self, data: bytes) -> EncodedChunk:
        if len(data) > self._payload_size:
            raise ValueError(
                f"Chunk is too large for configured RaptorQ payload ({len(data)} > {self._payload_size})"
            )
        padded = data + b"\x00" * (self._payload_size - len(data))
        if _HAS_PYRAPTORQ:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
                encoder = Encoder(padded, self.symbol_size)
            symbols = {symbol_id: encoder.gen_symbol(symbol_id) for symbol_id in range(self.total_symbols)}
        else:
            symbols = self._encode_fallback(padded)
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
        if _HAS_PYRAPTORQ:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
                decoder = Decoder(self.source_symbols, self.symbol_size, self._payload_size)

            for symbol_id in sorted(symbols):
                decoder.add_symbol(symbol_id, symbols[symbol_id])

            decoded = decoder.try_decode()
            if decoded is None:
                raise ValueError("RaptorQ decode failed with provided symbols")
            return decoded[:original_size]
        return self._decode_fallback(symbols=symbols, original_size=original_size)

    def _generator_row(self, symbol_id: int) -> list[int]:
        base = symbol_id + 1
        return [_gf_pow(base, power) for power in range(self.source_symbols)]

    def _encode_fallback(self, padded: bytes) -> dict[int, bytes]:
        source_parts = [
            padded[index * self.symbol_size : (index + 1) * self.symbol_size]
            for index in range(self.source_symbols)
        ]
        encoded: dict[int, bytes] = {}
        for symbol_id in range(self.total_symbols):
            coefficients = self._generator_row(symbol_id)
            out = bytearray(self.symbol_size)
            for source_index, coefficient in enumerate(coefficients):
                if coefficient == 0:
                    continue
                shard = source_parts[source_index]
                if coefficient == 1:
                    for idx in range(self.symbol_size):
                        out[idx] ^= shard[idx]
                    continue
                table = _GF_MUL_TABLE[coefficient]
                for idx in range(self.symbol_size):
                    out[idx] ^= table[shard[idx]]
            encoded[symbol_id] = bytes(out)
        return encoded

    def _decode_fallback(self, symbols: dict[int, bytes], original_size: int) -> bytes:
        selected_ids = sorted(symbols.keys())[: self.source_symbols]
        if len(selected_ids) < self.source_symbols:
            raise ValueError(
                f"Need at least {self.source_symbols} symbols to decode; got {len(selected_ids)}"
            )
        for symbol_id in selected_ids:
            if symbol_id < 0 or symbol_id >= self.total_symbols:
                raise ValueError(f"Symbol id out of range: {symbol_id}")
            if len(symbols[symbol_id]) != self.symbol_size:
                raise ValueError("Invalid symbol size for decode")

        decode_matrix = [self._generator_row(symbol_id) for symbol_id in selected_ids]
        inverse_matrix = _invert_matrix_gf256(decode_matrix)
        selected_symbols = [symbols[symbol_id] for symbol_id in selected_ids]

        recovered_parts: list[bytes] = []
        for row in inverse_matrix:
            out = bytearray(self.symbol_size)
            for symbol_index, coefficient in enumerate(row):
                if coefficient == 0:
                    continue
                shard = selected_symbols[symbol_index]
                if coefficient == 1:
                    for idx in range(self.symbol_size):
                        out[idx] ^= shard[idx]
                    continue
                table = _GF_MUL_TABLE[coefficient]
                for idx in range(self.symbol_size):
                    out[idx] ^= table[shard[idx]]
            recovered_parts.append(bytes(out))

        decoded = b"".join(recovered_parts)
        return decoded[:original_size]
