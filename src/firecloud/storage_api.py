from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Response

from .storage import NodeStore


def _dir_size_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def create_storage_api(
    node_id: str,
    root_dir: Path,
    *,
    total_bytes: int | None = None,
    reserved_bytes: int = 0,
    min_free_bytes: int = 0,
) -> FastAPI:
    store = NodeStore(node_id=node_id, root_dir=root_dir)
    app = FastAPI(title=f"FireCloud Storage Node ({node_id})", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "node_id": node_id}

    @app.put("/symbols/{chunk_id:path}/{symbol_id}")
    def put_symbol(
        chunk_id: str,
        symbol_id: int,
        payload: bytes = Body(..., media_type="application/octet-stream"),
    ) -> dict[str, str]:
        try:
            symbol_path = store.put_symbol(chunk_id=chunk_id, symbol_id=symbol_id, symbol_data=payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"symbol_path": symbol_path}

    @app.get("/symbols")
    def get_symbol(path: str = Query(..., min_length=1)) -> Response:
        try:
            exists = store.has_symbol(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not exists:
            raise HTTPException(status_code=404, detail="Symbol not found")
        try:
            data = store.get_symbol(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(content=data, media_type="application/octet-stream")

    @app.head("/symbols")
    def has_symbol(path: str = Query(..., min_length=1)) -> Response:
        if not store.has_symbol(path):
            return Response(status_code=404)
        return Response(status_code=200)

    @app.delete("/symbols")
    def delete_symbol(path: str = Query(..., min_length=1)) -> Response:
        if not store.has_symbol(path):
            return Response(status_code=404)
        try:
            store.delete_symbol(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.get("/stats")
    def stats() -> dict[str, int]:
        used_bytes = _dir_size_bytes(store.symbols_dir)
        if total_bytes is None:
            available_bytes = 0
            total_value = 0
        else:
            available_bytes = max(0, int(total_bytes) - used_bytes - reserved_bytes - min_free_bytes)
            total_value = int(total_bytes)
        return {
            "symbol_count": store.symbol_count(),
            "used_bytes": used_bytes,
            "total_bytes": total_value,
            "available_bytes": available_bytes,
        }

    return app
