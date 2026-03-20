from pathlib import Path

from fastapi.testclient import TestClient

from firecloud.api import create_api
from firecloud.config import FECConfig, FireCloudConfig
from firecloud.controller import FireCloudController


def _client(tmp_path: Path) -> TestClient:
    cfg = FireCloudConfig(
        root_dir=tmp_path / "state",
        node_count=5,
        fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128),
    )
    controller = FireCloudController(config=cfg)
    app = create_api(controller)
    return TestClient(app)


def test_health_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_download_and_audit_endpoints(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = b"api-flow-test"

    upload_res = client.post(
        "/files/upload",
        params={"file_name": "sample.txt"},
        content=payload,
        headers={"content-type": "application/octet-stream"},
    )
    assert upload_res.status_code == 200
    file_id = upload_res.json()["file_id"]

    files = client.get("/files")
    assert files.status_code == 200
    assert any(item["file_id"] == file_id for item in files.json())

    dl_res = client.get(f"/files/{file_id}/download")
    assert dl_res.status_code == 200
    assert dl_res.content == payload
    assert "attachment; filename=\"sample.txt\"" in dl_res.headers["content-disposition"]

    verify = client.get("/audit/verify")
    assert verify.status_code == 200
    assert verify.json()["valid"] is True

    delete = client.delete(f"/files/{file_id}")
    assert delete.status_code == 200


def test_upload_requires_file_name_and_body(tmp_path: Path) -> None:
    client = _client(tmp_path)
    no_name = client.post("/files/upload", content=b"abc", headers={"content-type": "application/octet-stream"})
    assert no_name.status_code == 422

    no_payload = client.post("/files/upload", params={"file_name": "x.bin"})
    assert no_payload.status_code == 422


def test_unknown_node_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/nodes/node-999/offline")
    assert response.status_code == 404


def test_add_and_remove_node_endpoints(tmp_path: Path) -> None:
    client = _client(tmp_path)
    node_root = tmp_path / "node-extra"
    node_root.mkdir(parents=True, exist_ok=True)

    add = client.post(
        "/nodes/add",
        json={"node_id": "node-extra", "endpoint": str(node_root), "kind": "local"},
    )
    assert add.status_code == 200

    nodes = client.get("/nodes")
    assert nodes.status_code == 200
    assert any(item["node_id"] == "node-extra" for item in nodes.json())

    remove = client.delete("/nodes/node-extra")
    assert remove.status_code == 200


def test_repair_unknown_file_returns_400(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/files/missing/repair")
    assert response.status_code == 400


def test_audit_events_limit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    file_id = client.post(
        "/files/upload",
        params={"file_name": "sample.bin"},
        content=b"z" * 200,
        headers={"content-type": "application/octet-stream"},
    ).json()["file_id"]
    client.get(f"/files/{file_id}/download")

    events = client.get("/audit/events", params={"limit": 1})
    assert events.status_code == 200
    assert len(events.json()) == 1

    invalid = client.get("/audit/events", params={"limit": 0})
    assert invalid.status_code == 422


def test_dedup_gc_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    file_id = client.post(
        "/files/upload",
        params={"file_name": "gc.txt"},
        content=b"gc-test-payload" * 200,
        headers={"content-type": "application/octet-stream"},
    ).json()["file_id"]
    delete = client.delete(f"/files/{file_id}")
    assert delete.status_code == 200

    gc = client.post("/maintenance/dedup-gc", params={"force": "true"})
    assert gc.status_code == 200
    payload = gc.json()
    assert set(payload.keys()) == {
        "processed_chunks",
        "deleted_chunks",
        "deleted_symbols",
        "failed_chunks",
    }
