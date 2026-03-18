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
    source = tmp_path / "sample.txt"
    source.write_text("api-flow-test")

    upload_res = client.post("/files/upload", json={"path": str(source)})
    assert upload_res.status_code == 200
    file_id = upload_res.json()["file_id"]

    files = client.get("/files")
    assert files.status_code == 200
    assert any(item["file_id"] == file_id for item in files.json())

    destination = tmp_path / "out" / "sample.txt"
    dl_res = client.post(f"/files/{file_id}/download", json={"destination": str(destination)})
    assert dl_res.status_code == 200
    assert destination.read_text() == "api-flow-test"

    verify = client.get("/audit/verify")
    assert verify.status_code == 200
    assert verify.json()["valid"] is True


def test_upload_invalid_path_returns_400(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/files/upload", json={"path": str(tmp_path / "missing.bin")})
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]

    response_dir = client.post("/files/upload", json={"path": str(tmp_path)})
    assert response_dir.status_code == 400


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
    source = tmp_path / "sample.bin"
    source.write_bytes(b"z" * 200)
    file_id = client.post("/files/upload", json={"path": str(source)}).json()["file_id"]
    client.post(f"/files/{file_id}/download", json={"destination": str(tmp_path / 'out.bin')})

    events = client.get("/audit/events", params={"limit": 1})
    assert events.status_code == 200
    assert len(events.json()) == 1

    invalid = client.get("/audit/events", params={"limit": 0})
    assert invalid.status_code == 422
