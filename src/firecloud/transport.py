from __future__ import annotations

from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from .storage import NodeStore


class TransportError(RuntimeError):
    pass


class NodeTransport(Protocol):
    def put_symbol(
        self, node_id: str, endpoint: str, chunk_id: str, symbol_id: int, symbol_data: bytes
    ) -> str: ...

    def get_symbol(self, node_id: str, endpoint: str, symbol_path: str) -> bytes: ...

    def has_symbol(self, node_id: str, endpoint: str, symbol_path: str) -> bool: ...

    def delete_symbol(self, node_id: str, endpoint: str, symbol_path: str) -> None: ...

    def symbol_count(self, node_id: str, endpoint: str) -> int: ...

    def storage_stats(self, node_id: str, endpoint: str) -> dict[str, int]: ...


class LocalNodeTransport:
    def __init__(self) -> None:
        self._stores: dict[str, NodeStore] = {}

    def _store(self, node_id: str, endpoint: str) -> NodeStore:
        key = f"{node_id}:{endpoint}"
        store = self._stores.get(key)
        if store is None:
            store = NodeStore(node_id=node_id, root_dir=Path(endpoint))
            self._stores[key] = store
        return store

    def put_symbol(
        self, node_id: str, endpoint: str, chunk_id: str, symbol_id: int, symbol_data: bytes
    ) -> str:
        return self._store(node_id=node_id, endpoint=endpoint).put_symbol(
            chunk_id=chunk_id, symbol_id=symbol_id, symbol_data=symbol_data
        )

    def get_symbol(self, node_id: str, endpoint: str, symbol_path: str) -> bytes:
        return self._store(node_id=node_id, endpoint=endpoint).get_symbol(symbol_path)

    def has_symbol(self, node_id: str, endpoint: str, symbol_path: str) -> bool:
        return self._store(node_id=node_id, endpoint=endpoint).has_symbol(symbol_path)

    def delete_symbol(self, node_id: str, endpoint: str, symbol_path: str) -> None:
        self._store(node_id=node_id, endpoint=endpoint).delete_symbol(symbol_path)

    def symbol_count(self, node_id: str, endpoint: str) -> int:
        return self._store(node_id=node_id, endpoint=endpoint).symbol_count()

    def storage_stats(self, node_id: str, endpoint: str) -> dict[str, int]:
        store = self._store(node_id=node_id, endpoint=endpoint)
        symbols_dir = store.symbols_dir
        used = 0
        for path in symbols_dir.rglob("*.bin"):
            try:
                used += path.stat().st_size
            except OSError:
                continue
        return {
            "symbol_count": store.symbol_count(),
            "available_bytes": 0,
            "used_bytes": used,
            "total_bytes": 0,
        }


class HttpNodeTransport:
    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self._timeout = timeout_seconds

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout)

    def put_symbol(
        self, node_id: str, endpoint: str, chunk_id: str, symbol_id: int, symbol_data: bytes
    ) -> str:
        url = f"{endpoint.rstrip('/')}/symbols/{quote(chunk_id, safe='')}/{symbol_id}"
        try:
            with self._client() as client:
                response = client.put(
                    url, content=symbol_data, headers={"content-type": "application/octet-stream"}
                )
        except httpx.HTTPError as exc:
            raise TransportError(f"HTTP put_symbol failed for {node_id}: {exc}") from exc
        if response.status_code != 200:
            raise TransportError(
                f"HTTP put_symbol failed for {node_id}: {response.status_code} {response.text}"
            )
        payload = response.json()
        symbol_path = payload.get("symbol_path")
        if not isinstance(symbol_path, str) or not symbol_path:
            raise TransportError(f"Invalid put_symbol response for {node_id}")
        return symbol_path

    def get_symbol(self, node_id: str, endpoint: str, symbol_path: str) -> bytes:
        url = f"{endpoint.rstrip('/')}/symbols"
        try:
            with self._client() as client:
                response = client.get(url, params={"path": symbol_path})
        except httpx.HTTPError as exc:
            raise TransportError(f"HTTP get_symbol failed for {node_id}: {exc}") from exc
        if response.status_code == 404:
            raise FileNotFoundError(f"Symbol not found on node {node_id}: {symbol_path}")
        if response.status_code != 200:
            raise TransportError(
                f"HTTP get_symbol failed for {node_id}: {response.status_code} {response.text}"
            )
        return response.content

    def has_symbol(self, node_id: str, endpoint: str, symbol_path: str) -> bool:
        url = f"{endpoint.rstrip('/')}/symbols"
        try:
            with self._client() as client:
                response = client.head(url, params={"path": symbol_path})
        except httpx.HTTPError as exc:
            raise TransportError(f"HTTP has_symbol failed for {node_id}: {exc}") from exc
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise TransportError(
            f"HTTP has_symbol failed for {node_id}: {response.status_code} {response.text}"
        )

    def delete_symbol(self, node_id: str, endpoint: str, symbol_path: str) -> None:
        url = f"{endpoint.rstrip('/')}/symbols"
        try:
            with self._client() as client:
                response = client.delete(url, params={"path": symbol_path})
        except httpx.HTTPError as exc:
            raise TransportError(f"HTTP delete_symbol failed for {node_id}: {exc}") from exc
        if response.status_code in {200, 204, 404}:
            return
        raise TransportError(
            f"HTTP delete_symbol failed for {node_id}: {response.status_code} {response.text}"
        )

    def symbol_count(self, node_id: str, endpoint: str) -> int:
        url = f"{endpoint.rstrip('/')}/stats"
        try:
            with self._client() as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            raise TransportError(f"HTTP symbol_count failed for {node_id}: {exc}") from exc
        if response.status_code != 200:
            raise TransportError(
                f"HTTP symbol_count failed for {node_id}: {response.status_code} {response.text}"
            )
        payload = response.json()
        count = payload.get("symbol_count")
        if not isinstance(count, int):
            raise TransportError(f"Invalid stats response for {node_id}")
        return count

    def storage_stats(self, node_id: str, endpoint: str) -> dict[str, int]:
        url = f"{endpoint.rstrip('/')}/stats"
        try:
            with self._client() as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            raise TransportError(f"HTTP storage_stats failed for {node_id}: {exc}") from exc
        if response.status_code != 200:
            raise TransportError(
                f"HTTP storage_stats failed for {node_id}: {response.status_code} {response.text}"
            )
        payload = response.json()
        count = payload.get("symbol_count")
        if not isinstance(count, int):
            raise TransportError(f"Invalid stats response for {node_id}")

        available = payload.get("available_bytes")
        used = payload.get("used_bytes")
        total = payload.get("total_bytes")
        if not isinstance(available, int):
            available = 0
        if not isinstance(used, int):
            used = 0
        if not isinstance(total, int):
            total = 0
        return {
            "symbol_count": count,
            "available_bytes": max(0, available),
            "used_bytes": max(0, used),
            "total_bytes": max(0, total),
        }
