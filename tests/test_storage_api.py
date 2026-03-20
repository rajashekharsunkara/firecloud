from pathlib import Path

from fastapi.testclient import TestClient

from firecloud.storage_api import create_storage_api


def _client(tmp_path: Path) -> TestClient:
    app = create_storage_api(node_id="node-test", root_dir=tmp_path / "node-store")
    return TestClient(app)


def test_storage_api_health(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["node_id"] == "node-test"


def test_storage_api_put_get_has_and_stats(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = b"symbol-bytes"
    put = client.put("/symbols/chunk-1/2", content=payload)
    assert put.status_code == 200
    symbol_path = put.json()["symbol_path"]

    has = client.head("/symbols", params={"path": symbol_path})
    assert has.status_code == 200

    get = client.get("/symbols", params={"path": symbol_path})
    assert get.status_code == 200
    assert get.content == payload

    stats = client.get("/stats")
    assert stats.status_code == 200
    assert stats.json()["symbol_count"] == 1

    delete = client.delete("/symbols", params={"path": symbol_path})
    assert delete.status_code == 204

    has_after = client.head("/symbols", params={"path": symbol_path})
    assert has_after.status_code == 404


def test_storage_api_missing_symbol(tmp_path: Path) -> None:
    client = _client(tmp_path)
    get = client.get("/symbols", params={"path": "missing/path.bin"})
    assert get.status_code == 404
    has = client.head("/symbols", params={"path": "missing/path.bin"})
    assert has.status_code == 404


def test_storage_api_rejects_path_traversal_and_invalid_chunk_id(tmp_path: Path) -> None:
    client = _client(tmp_path)

    bad_get = client.get("/symbols", params={"path": "../outside.bin"})
    assert bad_get.status_code == 404

    bad_head = client.head("/symbols", params={"path": "../outside.bin"})
    assert bad_head.status_code == 404

    bad_put = client.put("/symbols/bad/chunk/1", content=b"x")
    assert bad_put.status_code == 400
