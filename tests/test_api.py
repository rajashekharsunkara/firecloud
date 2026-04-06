from pathlib import Path

from fastapi.testclient import TestClient

from firecloud.api import create_api
from firecloud.config import FECConfig, FireCloudConfig
from firecloud.controller import FireCloudController
from firecloud.identity import DeviceIdentityManager
from firecloud.security import SecurityMiddleware, sign_request


def _client(tmp_path: Path) -> TestClient:
    cfg = FireCloudConfig(
        root_dir=tmp_path / "state",
        node_count=5,
        fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128),
    )
    controller = FireCloudController(config=cfg)
    app = create_api(controller)
    return TestClient(app)


def _grant_access(
    client: TestClient,
    requester_device_id: str = "req-1",
    requester_public_key: str = "pk-req-1",
) -> str:
    appeal = client.post(
        "/audit/appeals",
        json={
            "requester_device_id": requester_device_id,
            "requester_public_key": requester_public_key,
            "reason": "security_incident",
            "justification": "Need audit access to investigate storage inconsistency",
        },
    )
    assert appeal.status_code == 200
    appeal_id = appeal.json()["appeal_id"]

    for idx in range(3):
        vote = client.post(
            f"/audit/appeals/{appeal_id}/vote",
            json={
                "voter_device_id": f"voter-{idx}",
                "voter_public_key": f"pk-voter-{idx}",
                "vote": True,
                "reason": "approve",
            },
        )
        assert vote.status_code == 200

    status = client.post(
        "/audit/access-status",
        json={
            "requester_device_id": requester_device_id,
            "requester_public_key": requester_public_key,
        },
    )
    assert status.status_code == 200
    payload = status.json()
    assert payload["has_access"] is True
    return payload["grant"]["grant_id"]


def test_health_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_download_and_delete_endpoints(tmp_path: Path) -> None:
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
        json={"node_id": "node-extra", "endpoint": str(node_root), "kind": "http"},
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

    _grant_access(client)
    events = client.get(
        "/audit/events",
        params={
            "requester_device_id": "req-1",
            "requester_public_key": "pk-req-1",
            "limit": 1,
        },
    )
    assert events.status_code == 200
    assert len(events.json()) == 1

    invalid = client.get(
        "/audit/events",
        params={
            "requester_device_id": "req-1",
            "requester_public_key": "pk-req-1",
            "limit": 0,
        },
    )
    assert invalid.status_code == 422


def test_audit_access_requires_consensus_grant(tmp_path: Path) -> None:
    client = _client(tmp_path)
    requester_device_id = "req-audit"
    requester_public_key = "pk-audit"

    blocked_verify = client.get(
        "/audit/verify",
        params={
            "requester_device_id": requester_device_id,
            "requester_public_key": requester_public_key,
        },
    )
    assert blocked_verify.status_code == 403

    blocked_events = client.get(
        "/audit/events",
        params={
            "requester_device_id": requester_device_id,
            "requester_public_key": requester_public_key,
            "limit": 10,
        },
    )
    assert blocked_events.status_code == 403

    grant_id = _grant_access(
        client,
        requester_device_id=requester_device_id,
        requester_public_key=requester_public_key,
    )
    assert grant_id

    verify = client.get(
        "/audit/verify",
        params={
            "requester_device_id": requester_device_id,
            "requester_public_key": requester_public_key,
        },
    )
    assert verify.status_code == 200
    assert verify.json()["valid"] is True

    events = client.get(
        "/audit/events",
        params={
            "requester_device_id": requester_device_id,
            "requester_public_key": requester_public_key,
            "limit": 10,
        },
    )
    assert events.status_code == 200
    assert isinstance(events.json(), list)


def test_audit_events_are_scoped_by_grant_event_type(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = b"audit-scope"
    upload_res = client.post(
        "/files/upload",
        params={"file_name": "scope.bin"},
        content=payload,
        headers={"content-type": "application/octet-stream"},
    )
    assert upload_res.status_code == 200
    file_id = upload_res.json()["file_id"]
    client.get(f"/files/{file_id}/download")

    appeal = client.post(
        "/audit/appeals",
        json={
            "requester_device_id": "req-scope",
            "requester_public_key": "pk-scope",
            "reason": "security_incident",
            "justification": "Need only upload events",
            "scope_event_types": ["file_uploaded"],
        },
    )
    assert appeal.status_code == 200
    appeal_id = appeal.json()["appeal_id"]
    for idx in range(3):
        vote = client.post(
            f"/audit/appeals/{appeal_id}/vote",
            json={
                "voter_device_id": f"scope-voter-{idx}",
                "voter_public_key": f"scope-pk-voter-{idx}",
                "vote": True,
                "reason": "approve",
            },
        )
        assert vote.status_code == 200

    events = client.get(
        "/audit/events",
        params={
            "requester_device_id": "req-scope",
            "requester_public_key": "pk-scope",
            "limit": 50,
        },
    )
    assert events.status_code == 200
    event_types = {event["event_type"] for event in events.json()}
    assert event_types
    assert event_types == {"file_uploaded"}


def test_audit_reasons_and_access_status_endpoints(tmp_path: Path) -> None:
    client = _client(tmp_path)
    reasons = client.get("/audit/reasons")
    assert reasons.status_code == 200
    assert "security_incident" in reasons.json()

    status = client.post(
        "/audit/access-status",
        json={"requester_device_id": "req-none", "requester_public_key": "pk-none"},
    )
    assert status.status_code == 200
    assert status.json()["has_access"] is False


def test_audit_access_status_reports_grant_after_approval(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _grant_access(client, requester_device_id="req-status", requester_public_key="pk-status")
    status = client.post(
        "/audit/access-status",
        json={"requester_device_id": "req-status", "requester_public_key": "pk-status"},
    )
    assert status.status_code == 200
    payload = status.json()
    assert payload["has_access"] is True
    assert payload["grant"]["grant_id"].startswith("grant-")


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


def test_network_storage_status_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/network/storage-status")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {
        "online_http_with_capacity",
        "required_http_peers",
        "total_http_available_capacity",
        "storage_ready",
    }
    assert payload["storage_ready"] is True


def test_network_bootstrap_endpoints(tmp_path: Path) -> None:
    client = _client(tmp_path)

    status_response = client.get("/network/bootstrap/status")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert set(status_payload.keys()) == {
        "bootstrap_peers",
        "last_refresh",
        "last_refresh_error",
        "refresh_interval_seconds",
    }
    assert status_payload["bootstrap_peers"] == []
    assert status_payload["last_refresh_error"] is None

    refresh_response = client.post("/network/bootstrap/refresh")
    assert refresh_response.status_code == 200
    refresh_payload = refresh_response.json()
    assert set(refresh_payload.keys()) == {
        "bootstrap_peers",
        "attempted",
        "successful",
        "imported",
        "error",
    }
    assert refresh_payload["attempted"] == 0
    assert refresh_payload["successful"] == 0
    assert refresh_payload["imported"] == 0
    assert refresh_payload["error"] is None


def test_signed_upload_and_download_accept_standard_header_casing(tmp_path: Path) -> None:
    cfg = FireCloudConfig(
        root_dir=tmp_path / "state",
        node_count=5,
        fec=FECConfig(source_symbols=3, total_symbols=5, symbol_size=128),
    )
    controller = FireCloudController(config=cfg)
    security = SecurityMiddleware(data_dir=cfg.root_dir / "security")
    app = create_api(controller, security=security, require_signed_requests=True)
    identity = DeviceIdentityManager(cfg.root_dir / "identity")
    device = identity.get_identity()

    payload = b"signed-payload"
    signed_upload = sign_request(
        method="POST",
        path="/files/upload",
        body=payload,
        device_id=device.device_id,
        public_key=device.public_key,
        sign_callback=identity.sign_message,
    )
    upload_headers = signed_upload.to_headers()
    upload_headers["content-type"] = "application/octet-stream"

    with TestClient(app) as client:
        upload = client.post(
            "/files/upload",
            params={"file_name": "signed.bin"},
            content=payload,
            headers=upload_headers,
        )
        assert upload.status_code == 200
        file_id = upload.json()["file_id"]

        signed_download = sign_request(
            method="GET",
            path=f"/files/{file_id}/download",
            body=b"",
            device_id=device.device_id,
            public_key=device.public_key,
            sign_callback=identity.sign_message,
        )
        download = client.get(
            f"/files/{file_id}/download",
            headers=signed_download.to_headers(),
        )
        assert download.status_code == 200
        assert download.content == payload
