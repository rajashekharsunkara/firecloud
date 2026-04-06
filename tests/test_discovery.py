from __future__ import annotations

from collections import deque

from firecloud.discovery import NetworkManager


class _StubResponse:
    def __init__(self, status_code: int = 200, payload=None, *, json_error: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("invalid json")
        return self._payload


class _StubClient:
    def __init__(self, responses: deque) -> None:
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str):
        if not self._responses:
            raise RuntimeError("no response configured")
        item = self._responses.popleft()
        if isinstance(item, Exception):
            raise item
        return item


def test_network_manager_refresh_peers_imports_from_bootstrap(monkeypatch) -> None:
    responses = deque(
        [
            _StubResponse(
                200,
                [
                    {
                        "device_id": "peer-1",
                        "endpoint": "http://10.0.0.10:8080",
                        "hostname": "peer-one",
                        "node_type": "storage",
                        "public_key": "pk-1",
                        "available_storage": 1024,
                    },
                    {
                        "device_id": "peer-2",
                        "endpoint": "http://10.0.0.11:8080",
                        "hostname": "peer-two",
                        "node_type": "consumer",
                        "public_key": "pk-2",
                        "available_storage": 0,
                    },
                ],
            )
        ]
    )
    monkeypatch.setattr("firecloud.discovery.httpx.Client", lambda timeout=5.0: _StubClient(responses))

    manager = NetworkManager(
        device_id="local-device",
        port=8080,
        node_type="consumer",
        public_key="local-pk",
        bootstrap_peers=["http://bootstrap.local:8080"],
    )
    result = manager.refresh_peers()

    assert result["attempted"] == 1
    assert result["successful"] == 1
    assert result["imported"] == 2
    assert result["error"] is None
    peers = {peer.device_id: peer for peer in manager.get_peers()}
    assert set(peers.keys()) == {"peer-1", "peer-2"}
    assert peers["peer-1"].node_type == "storage"


def test_network_manager_refresh_peers_degrades_gracefully(monkeypatch) -> None:
    responses = deque(
        [
            RuntimeError("connection refused"),
            _StubResponse(500, []),
            _StubResponse(200, payload=None, json_error=True),
        ]
    )
    monkeypatch.setattr("firecloud.discovery.httpx.Client", lambda timeout=5.0: _StubClient(responses))

    manager = NetworkManager(
        device_id="local-device",
        port=8080,
        node_type="consumer",
        public_key="local-pk",
        bootstrap_peers=[
            "http://bootstrap-1.local:8080",
            "http://bootstrap-2.local:8080",
            "http://bootstrap-3.local:8080",
        ],
    )
    result = manager.refresh_peers()

    assert result["attempted"] == 3
    assert result["successful"] == 0
    assert result["imported"] == 0
    assert result["error"] is not None
    assert "bootstrap-1.local" in result["error"]
    assert "bootstrap-2.local" in result["error"]
    assert "bootstrap-3.local" in result["error"]
    assert manager.get_peers() == []


def test_network_manager_parses_existing_network_peers_shape(monkeypatch) -> None:
    responses = deque(
        [
            _StubResponse(
                200,
                [
                    {
                        "device_id": "peer-existing-shape",
                        "hostname": "peer-existing-shape",
                        "ip_address": "10.1.0.5",
                        "port": 8080,
                        "node_type": "storage",
                        "public_key": "pk-x",
                        "available_storage": 2048,
                        "protocol_version": "1.0",
                        "is_online": True,
                    }
                ],
            )
        ]
    )
    monkeypatch.setattr("firecloud.discovery.httpx.Client", lambda timeout=5.0: _StubClient(responses))

    manager = NetworkManager(
        device_id="local-device",
        port=8080,
        node_type="consumer",
        public_key="local-pk",
        bootstrap_peers=["http://bootstrap.local:8080"],
    )
    result = manager.refresh_peers()
    assert result["successful"] == 1
    assert [peer.device_id for peer in manager.get_peers()] == ["peer-existing-shape"]
