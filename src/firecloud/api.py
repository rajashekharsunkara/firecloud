from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .controller import FireCloudController


class UploadRequest(BaseModel):
    path: str = Field(..., description="Absolute or relative path to source file")


class DownloadRequest(BaseModel):
    destination: str = Field(..., description="Destination path for restored file")


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
    def upload_file(payload: UploadRequest) -> dict[str, str]:
        file_path = Path(payload.path)
        try:
            file_id = controller.upload_file(file_path)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"file_id": file_id}

    @app.post("/files/{file_id}/download")
    def download_file(file_id: str, payload: DownloadRequest) -> dict[str, str]:
        destination = Path(payload.destination)
        try:
            output_path = controller.download_file(file_id=file_id, destination_path=destination)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"destination": str(output_path)}

    @app.get("/nodes")
    def list_nodes() -> list[dict[str, object]]:
        return [
            {
                "node_id": node.node_id,
                "online": node.online,
                "symbol_count": node.symbol_count,
            }
            for node in controller.list_nodes()
        ]

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
