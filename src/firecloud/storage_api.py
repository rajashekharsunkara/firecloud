from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Response

from .storage import NodeStore


def create_storage_api(node_id: str, root_dir: Path) -> FastAPI:
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
        symbol_path = store.put_symbol(chunk_id=chunk_id, symbol_id=symbol_id, symbol_data=payload)
        return {"symbol_path": symbol_path}

    @app.get("/symbols")
    def get_symbol(path: str = Query(..., min_length=1)) -> Response:
        if not store.has_symbol(path):
            raise HTTPException(status_code=404, detail="Symbol not found")
        data = store.get_symbol(path)
        return Response(content=data, media_type="application/octet-stream")

    @app.head("/symbols")
    def has_symbol(path: str = Query(..., min_length=1)) -> Response:
        return Response(status_code=200 if store.has_symbol(path) else 404)

    @app.get("/stats")
    def stats() -> dict[str, int]:
        return {"symbol_count": store.symbol_count()}

    return app
