from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .controller import FireCloudController


class AddNodeRequest(BaseModel):
    node_id: str = Field(..., description="Node identifier")
    endpoint: str = Field(..., description="Node endpoint/path")
    kind: str = Field(default="local", description="Node kind: local or http")


def create_api(controller: FireCloudController) -> FastAPI:
    app = FastAPI(title="FireCloud Python MVP", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/files")
    def list_files() -> list[dict[str, object]]:
        return [
            {
                "file_id": item.file_id,
                "file_name": item.file_name,
                "file_size": item.file_size,
                "created_at": item.created_at,
            }
            for item in controller.list_files()
        ]

    @app.post("/files/upload")
    def upload_file(
        file_name: str = Query(..., min_length=1),
        payload: bytes = Body(..., media_type="application/octet-stream"),
    ) -> dict[str, str]:
        try:
            file_id = controller.upload_bytes(file_name=file_name, file_bytes=payload)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"file_id": file_id}

    @app.get("/files/{file_id}/download")
    def download_file(file_id: str) -> Response:
        try:
            file_name, content = controller.download_file_bytes(file_id=file_id)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
        )

    @app.delete("/files/{file_id}")
    def delete_file(file_id: str) -> dict[str, str]:
        try:
            controller.delete_file(file_id=file_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"file_id": file_id}

    @app.post("/maintenance/dedup-gc")
    def run_dedup_gc(force: bool = Query(default=False)) -> dict[str, int]:
        return controller.run_dedup_gc(force=force)

    @app.get("/nodes")
    def list_nodes() -> list[dict[str, object]]:
        return [
            {
                "node_id": node.node_id,
                "endpoint": node.endpoint,
                "kind": node.kind,
                "online": node.online,
                "symbol_count": node.symbol_count,
            }
            for node in controller.list_nodes()
        ]

    @app.post("/nodes/add")
    def add_node(payload: AddNodeRequest) -> dict[str, str]:
        try:
            controller.add_node(
                node_id=payload.node_id,
                endpoint=payload.endpoint,
                kind=payload.kind,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"node_id": payload.node_id}

    @app.delete("/nodes/{node_id}")
    def remove_node(node_id: str) -> dict[str, str]:
        try:
            controller.remove_node(node_id=node_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"node_id": node_id}

    @app.post("/nodes/{node_id}/offline")
    def set_node_offline(node_id: str) -> dict[str, object]:
        try:
            controller.set_node_online(node_id=node_id, online=False)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"node_id": node_id, "online": False}

    @app.post("/nodes/{node_id}/online")
    def set_node_online(node_id: str) -> dict[str, object]:
        try:
            controller.set_node_online(node_id=node_id, online=True)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"node_id": node_id, "online": True}

    @app.post("/files/{file_id}/repair")
    def repair_file(file_id: str) -> dict[str, object]:
        try:
            repaired_count = controller.repair_file(file_id=file_id)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"file_id": file_id, "repaired_symbols": repaired_count}

    @app.get("/audit/events")
    def audit_events(limit: int = Query(default=200, ge=1, le=1000)) -> list[dict[str, object]]:
        return [
            {
                "sequence": event.sequence,
                "event_time": event.event_time,
                "event_type": event.event_type,
                "payload": event.payload,
                "prev_hash": event.prev_hash,
                "event_hash": event.event_hash,
            }
            for event in controller.audit_events(limit=limit)
        ]

    @app.get("/audit/verify")
    def audit_verify() -> dict[str, object]:
        valid, details = controller.verify_audit_chain()
        return {"valid": valid, "details": details}

    return app
