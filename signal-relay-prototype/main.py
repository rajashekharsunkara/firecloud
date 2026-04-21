from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from firebase_admin import auth as firebase_auth
from firebase_admin import initialize_app as firebase_initialize_app
from pydantic import BaseModel, Field

try:
    from google.api_core.exceptions import NotFound as GcsNotFound
    from google.cloud import storage
except ImportError:
    GcsNotFound = Exception  # type: ignore[assignment]
    storage = None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value


AUTH_MODE = os.getenv("FIRECLOUD_AUTH_MODE", "required").strip().lower()
if AUTH_MODE not in {"required", "optional", "disabled"}:
    raise RuntimeError("FIRECLOUD_AUTH_MODE must be one of: required, optional, disabled")

PEER_TTL_SECONDS = _env_int("FIRECLOUD_PEER_TTL_SECONDS", 180)
RELAY_CHUNK_TTL_SECONDS = _env_int("FIRECLOUD_RELAY_CHUNK_TTL_SECONDS", 6 * 60 * 60)
MANIFEST_TTL_SECONDS = _env_int("FIRECLOUD_MANIFEST_TTL_SECONDS", 7 * 24 * 60 * 60)

RATE_LIMIT_WINDOW_SECONDS = _env_int("FIRECLOUD_RATE_LIMIT_WINDOW_SECONDS", 60)
RATE_LIMIT_READ_MAX = _env_int("FIRECLOUD_RATE_LIMIT_READ_MAX", 240)
RATE_LIMIT_WRITE_MAX = _env_int("FIRECLOUD_RATE_LIMIT_WRITE_MAX", 120)
MAX_CHUNK_BYTES = _env_int("FIRECLOUD_MAX_CHUNK_BYTES", 5 * 1024 * 1024)
PRUNE_INTERVAL_SECONDS = _env_int("FIRECLOUD_PRUNE_INTERVAL_SECONDS", 30)

ALLOW_PRIVATE_UPSTREAMS = _env_bool("FIRECLOUD_ALLOW_PRIVATE_UPSTREAMS", False)
FIRECLOUD_STORAGE_BUCKET = os.getenv("FIRECLOUD_STORAGE_BUCKET", "").strip()
REQUIRE_DURABLE_STORAGE = _env_bool("FIRECLOUD_REQUIRE_DURABLE_STORAGE", False)

DEVICE_ID_PATTERN = r"^[A-Za-z0-9._-]{3,128}$"
OWNER_ID_PATTERN = r"^[A-Za-z0-9._:-]{3,128}$"
FILE_ID_PATTERN = r"^[A-Za-z0-9._-]{3,256}$"
CHUNK_HASH_PATTERN = re.compile(r"^[a-fA-F0-9]{16,128}$")

_REQUEST_HOP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

_RESPONSE_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("firecloud.signal_relay")

_peers: dict[str, dict[str, Any]] = {}
_relay_chunks: dict[tuple[str, str], dict[str, Any]] = {}
_manifest_envelopes: dict[tuple[str, str], dict[str, Any]] = {}

_rate_limit_hits: dict[str, Deque[float]] = defaultdict(deque)
_token_cache: dict[str, tuple[str, float]] = {}
_firebase_initialized = False
_last_prune_runs = {"peers": 0.0, "chunks": 0.0, "manifests": 0.0, "rate_limits": 0.0}
_background_prune_task: asyncio.Task[None] | None = None

_gcs_bucket: Any | None = None
if FIRECLOUD_STORAGE_BUCKET:
    if storage is None:
        raise RuntimeError(
            "google-cloud-storage dependency is required when FIRECLOUD_STORAGE_BUCKET is configured"
        )
    try:
        gcs_client = storage.Client()
        _gcs_bucket = gcs_client.bucket(FIRECLOUD_STORAGE_BUCKET)
        _gcs_bucket.reload()
    except Exception as exc:
        if REQUIRE_DURABLE_STORAGE:
            raise RuntimeError("Durable storage bucket initialization failed") from exc
        logger.warning("Durable storage unavailable, falling back to in-memory store: %s", exc)
        _gcs_bucket = None
elif REQUIRE_DURABLE_STORAGE:
    raise RuntimeError("FIRECLOUD_REQUIRE_DURABLE_STORAGE=true requires FIRECLOUD_STORAGE_BUCKET")


app = FastAPI(title="FireCloud Signaling + Relay", version="0.2.0")


@app.on_event("startup")
async def _startup_background_pruner() -> None:
    global _background_prune_task
    if _background_prune_task is None or _background_prune_task.done():
        _background_prune_task = asyncio.create_task(_background_prune_loop())


@app.on_event("shutdown")
async def _shutdown_background_pruner() -> None:
    global _background_prune_task
    if _background_prune_task is None:
        return
    _background_prune_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _background_prune_task
    _background_prune_task = None


async def _background_prune_loop() -> None:
    while True:
        try:
            now = time.time()
            _prune_stale_peers()
            _prune_stale_relay_chunks()
            _prune_stale_manifests()
            _prune_rate_limit_hits(now)
        except Exception:
            logger.exception("background prune loop failed")
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)


class RegisterRequest(BaseModel):
    device_id: str = Field(..., min_length=3, max_length=128, pattern=DEVICE_ID_PATTERN)
    public_key: str = ""
    public_ip: str | None = None
    public_port: int | None = Field(default=None, ge=1, le=65535)
    public_url: str | None = None
    local_port: int | None = Field(default=None, ge=1, le=65535)
    account_id: str | None = Field(default=None, min_length=3, max_length=128, pattern=OWNER_ID_PATTERN)
    role: str = Field(default="consumer", pattern=r"^(consumer|storage_provider)$")
    available_storage: int = Field(default=0, ge=0)
    nat_type: str | None = None
    relay_urls: list[str] = Field(default_factory=list)


class HeartbeatRequest(BaseModel):
    device_id: str = Field(..., min_length=3, max_length=128, pattern=DEVICE_ID_PATTERN)
    available_storage: int | None = Field(default=None, ge=0)


class ManifestEnvelopeUpsertRequest(BaseModel):
    owner_id: str = Field(..., min_length=3, max_length=128, pattern=OWNER_ID_PATTERN)
    file_id: str = Field(..., min_length=3, max_length=256, pattern=FILE_ID_PATTERN)
    encrypted_payload: str = Field(..., min_length=1)
    device_id: str = Field(..., min_length=3, max_length=128, pattern=DEVICE_ID_PATTERN)
    created_at: str | None = None


@dataclass(frozen=True)
class AuthContext:
    uid: str | None
    authenticated: bool


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "auth_mode": AUTH_MODE,
        "durable_storage": _gcs_bucket is not None,
    }


@app.post("/api/v1/peers/register")
def register_peer(payload: RegisterRequest, request: Request) -> dict[str, bool]:
    auth = _authenticate_request(request, require_token=True)
    _enforce_rate_limit(request, auth, write=True)

    row = payload.model_dump()
    account_id = _resolve_account_id(auth, row.get("account_id"), required=True)
    if account_id is None:
        raise HTTPException(status_code=400, detail="account_id is required")

    relay_urls: list[str] = []
    for raw_url in row["relay_urls"]:
        if not raw_url:
            continue
        relay_urls.append(_normalize_peer_url(raw_url))
    row["relay_urls"] = list(dict.fromkeys(relay_urls))
    if len(row["relay_urls"]) > 8:
        raise HTTPException(status_code=400, detail="relay_urls cannot contain more than 8 entries")

    if row.get("public_url"):
        row["public_url"] = _normalize_peer_url(str(row["public_url"]), require_path=False)

    if not row.get("public_ip"):
        row["public_ip"] = _extract_client_ip(request)
    if row.get("public_port") is None and row.get("local_port") is not None:
        row["public_port"] = row["local_port"]
    if row.get("public_url") is None and row.get("public_ip") and row.get("public_port"):
        row["public_url"] = f"http://{row['public_ip']}:{row['public_port']}"

    row["account_id"] = account_id
    row["has_direct_endpoint"] = bool(row.get("public_ip") and row.get("public_port"))
    row["_last_seen"] = time.time()

    existing = _get_peer(payload.device_id)
    if existing is not None:
        existing_account = str(existing.get("account_id") or "").strip()
        if existing_account and existing_account != account_id:
            raise HTTPException(status_code=409, detail="device_id already registered by another account")

    _set_peer(payload.device_id, row)
    return {"ok": True}


@app.post("/api/v1/peers/heartbeat")
def heartbeat(payload: HeartbeatRequest, request: Request) -> dict[str, Any]:
    auth = _authenticate_request(request, require_token=True)
    _enforce_rate_limit(request, auth, write=True)

    row = _get_peer(payload.device_id)
    if row is None:
        return {"ok": False, "reason": "not_registered"}
    _assert_account_access(row, auth)

    row["_last_seen"] = time.time()
    if payload.available_storage is not None:
        row["available_storage"] = payload.available_storage
    _set_peer(payload.device_id, row)
    return {"ok": True}


@app.get("/api/v1/peers")
def list_peers(
    request: Request,
    account_id: str | None = Query(default=None),
    scope: str = Query(default="account"),
) -> dict[str, list[dict[str, Any]]]:
    del scope  # account-scoped visibility is always enforced in hardened mode.
    auth = _authenticate_request(request, require_token=True)
    _enforce_rate_limit(request, auth, write=False)

    effective_account_id = _resolve_account_id(auth, account_id, required=True)
    if effective_account_id is None:
        raise HTTPException(status_code=400, detail="account_id is required")

    _prune_stale_peers()
    peers: list[dict[str, Any]] = []
    for row in _list_peers():
        if str(row.get("account_id") or "") != effective_account_id:
            continue
        peers.append(_to_public_peer(row))
    return {"peers": peers}


@app.delete("/api/v1/peers/{device_id}")
def unregister_peer(device_id: str, request: Request) -> dict[str, bool]:
    _assert_device_id(device_id)
    auth = _authenticate_request(request, require_token=True)
    _enforce_rate_limit(request, auth, write=True)

    row = _get_peer(device_id)
    if row is not None:
        _assert_account_access(row, auth)
    _delete_peer(device_id)
    return {"ok": True}


@app.post("/api/v1/manifests/upsert")
def upsert_manifest(payload: ManifestEnvelopeUpsertRequest, request: Request) -> dict[str, bool]:
    auth = _authenticate_request(request, require_token=True)
    _enforce_rate_limit(request, auth, write=True)

    owner_id = _resolve_account_id(auth, payload.owner_id, required=True)
    if owner_id is None:
        raise HTTPException(status_code=400, detail="owner_id is required")
    row = payload.model_dump()
    row["owner_id"] = owner_id
    row["_last_seen"] = time.time()
    _set_manifest(owner_id, payload.file_id, row)
    return {"ok": True}


@app.get("/api/v1/manifests")
def list_manifests(
    request: Request,
    owner_id: str | None = Query(default=None),
) -> dict[str, list[dict[str, Any]]]:
    auth = _authenticate_request(request, require_token=True)
    _enforce_rate_limit(request, auth, write=False)

    effective_owner_id = _resolve_account_id(auth, owner_id, required=True)
    if effective_owner_id is None:
        raise HTTPException(status_code=400, detail="owner_id is required")

    _prune_stale_manifests()
    manifests = [
        _to_public_manifest(row)
        for row in _list_manifests_by_owner(effective_owner_id)
    ]
    manifests.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return {"manifests": manifests}


@app.delete("/api/v1/manifests/{file_id}")
def delete_manifest(
    file_id: str,
    request: Request,
    owner_id: str | None = Query(default=None),
) -> dict[str, bool]:
    _assert_file_id(file_id)
    auth = _authenticate_request(request, require_token=True)
    _enforce_rate_limit(request, auth, write=True)

    effective_owner_id = _resolve_account_id(auth, owner_id, required=True)
    if effective_owner_id is None:
        raise HTTPException(status_code=400, detail="owner_id is required")

    _delete_manifest(effective_owner_id, file_id)
    return {"ok": True}


@app.api_route(
    "/relay/p2p/{device_id}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def relay_request(device_id: str, path: str, request: Request) -> Response:
    _assert_device_id(device_id)
    auth = _authenticate_request(request, require_token=True)
    _enforce_rate_limit(request, auth, write=request.method in {"POST", "PUT", "PATCH", "DELETE"})
    _prune_stale_peers()
    _prune_stale_relay_chunks()

    normalized_path = path if path.startswith("/") else f"/{path}"
    chunk_hash = _extract_chunk_hash(normalized_path)
    if chunk_hash is not None:
        _assert_chunk_hash(chunk_hash)
        return await _handle_chunk_relay_request(
            device_id=device_id,
            chunk_hash=chunk_hash,
            request=request,
            normalized_path=normalized_path,
            auth=auth,
        )

    _assert_allowed_proxy_path(normalized_path, request.method)
    peer = _get_peer(device_id)
    if peer is None:
        raise HTTPException(status_code=404, detail="peer not found")
    _assert_account_access(peer, auth)

    query_string = request.url.query
    if normalized_path == "/manifests":
        query_string = _normalize_manifest_query(query_string, auth.uid)

    return await _proxy_to_peer(
        peer=peer,
        method=request.method,
        path=normalized_path,
        query_string=query_string,
        request_headers=request.headers,
        body=await request.body(),
    )


async def _handle_chunk_relay_request(
    *,
    device_id: str,
    chunk_hash: str,
    request: Request,
    normalized_path: str,
    auth: AuthContext,
) -> Response:
    if request.method in {"POST", "PUT", "PATCH"}:
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="empty chunk payload")
        if len(body) > MAX_CHUNK_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"chunk payload exceeds limit ({MAX_CHUNK_BYTES} bytes)",
            )

        file_id = (request.headers.get("x-file-id") or "").strip()
        if file_id:
            _assert_file_id(file_id)
        account_id = _resolve_account_id(
            auth,
            request.headers.get("x-account-id"),
            required=False,
        )
        if account_id is None:
            raise HTTPException(status_code=400, detail="account context is required")

        remaining_refs = _store_chunk(device_id, chunk_hash, body, file_id, account_id)
        return JSONResponse(
            status_code=201,
            content={
                "status": "stored",
                "hash": chunk_hash,
                "relay_cached": True,
                "remaining_refs": remaining_refs,
            },
        )

    if request.method == "DELETE":
        file_id = (request.headers.get("x-file-id") or "").strip()
        if file_id:
            _assert_file_id(file_id)

        found, remaining_refs = _delete_chunk(device_id, chunk_hash, file_id, auth)
        if not found:
            return JSONResponse(
                status_code=404,
                content={"status": "not_found", "hash": chunk_hash},
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "deleted",
                "hash": chunk_hash,
                "remaining_refs": remaining_refs,
            },
        )

    if request.method == "GET":
        data = _get_chunk(device_id, chunk_hash, auth)
        if data is not None:
            return Response(
                content=data,
                status_code=200,
                media_type="application/octet-stream",
            )

        peer = _get_peer(device_id)
        if peer is None:
            raise HTTPException(status_code=404, detail="chunk not found")
        _assert_account_access(peer, auth)
        return await _proxy_to_peer(
            peer=peer,
            method="GET",
            path=normalized_path,
            query_string=request.url.query,
            request_headers=request.headers,
            body=b"",
        )

    raise HTTPException(status_code=405, detail="method not allowed")


async def _proxy_to_peer(
    *,
    peer: dict[str, Any],
    method: str,
    path: str,
    query_string: str,
    request_headers: Any,
    body: bytes,
) -> Response:
    target_url = _build_target_url(peer, path=path, query_string=query_string)
    headers = {
        key: value
        for key, value in request_headers.items()
        if key.lower() not in _REQUEST_HOP_HEADERS
    }

    timeout = httpx.Timeout(timeout=20.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        try:
            upstream = await client.request(
                method,
                target_url,
                headers=headers,
                content=body,
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"relay upstream failed: {exc}",
            ) from exc

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _RESPONSE_HOP_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )


def _authenticate_request(request: Request, *, require_token: bool) -> AuthContext:
    if AUTH_MODE == "disabled":
        return AuthContext(uid=None, authenticated=False)

    authorization = request.headers.get("authorization", "").strip()
    if not authorization:
        if require_token or AUTH_MODE == "required":
            raise HTTPException(status_code=401, detail="missing bearer token")
        return AuthContext(uid=None, authenticated=False)

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="invalid authorization header")

    uid = _verify_firebase_token(parts[1].strip())
    return AuthContext(uid=uid, authenticated=True)


def _verify_firebase_token(raw_token: str) -> str:
    now = time.time()
    cached = _token_cache.get(raw_token)
    if cached is not None:
        cached_uid, expires_at = cached
        if expires_at > now + 30:
            return cached_uid

    _ensure_firebase_initialized()
    try:
        decoded = firebase_auth.verify_id_token(raw_token, check_revoked=False)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid bearer token") from exc

    uid = decoded.get("uid")
    if not isinstance(uid, str) or not uid:
        raise HTTPException(status_code=401, detail="token missing uid")

    exp_raw = decoded.get("exp")
    if isinstance(exp_raw, (int, float)):
        expires_at = float(exp_raw)
    else:
        expires_at = now + 300.0

    _token_cache[raw_token] = (uid, expires_at)
    _prune_token_cache(now)
    return uid


def _ensure_firebase_initialized() -> None:
    global _firebase_initialized
    if _firebase_initialized:
        return
    try:
        firebase_initialize_app()
    except ValueError:
        # App already initialized by runtime.
        pass
    _firebase_initialized = True


def _prune_token_cache(now: float) -> None:
    stale_tokens = [token for token, (_, exp) in _token_cache.items() if exp <= now]
    for token in stale_tokens:
        _token_cache.pop(token, None)

    # Prevent unbounded growth under abuse.
    if len(_token_cache) > 2048:
        # Keep the newest half by expiration time.
        sorted_items = sorted(_token_cache.items(), key=lambda entry: entry[1][1], reverse=True)
        _token_cache.clear()
        for token, value in sorted_items[:1024]:
            _token_cache[token] = value


    def _prune_rate_limit_hits(now: float) -> None:
        if not _should_prune("rate_limits"):
            return

        stale_keys: list[str] = []
        for key, window in _rate_limit_hits.items():
            while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
                window.popleft()
            if not window:
                stale_keys.append(key)

        for key in stale_keys:
            _rate_limit_hits.pop(key, None)


def _resolve_account_id(
    auth: AuthContext,
    provided_account_id: str | None,
    *,
    required: bool,
) -> str | None:
    normalized = (provided_account_id or "").strip() or None
    if auth.uid is not None:
        if normalized is not None and normalized != auth.uid:
            raise HTTPException(status_code=403, detail="account mismatch")
        return auth.uid

    if normalized is not None:
        return normalized
    if required:
        if AUTH_MODE == "disabled":
            raise HTTPException(status_code=400, detail="account_id is required")
        raise HTTPException(status_code=401, detail="account context unavailable")
    return None


def _assert_account_access(row: dict[str, Any], auth: AuthContext) -> None:
    if auth.uid is None:
        if AUTH_MODE == "disabled":
            return
        raise HTTPException(status_code=401, detail="account context unavailable")

    account_id = str(row.get("account_id") or "").strip()
    if account_id and account_id != auth.uid:
        raise HTTPException(status_code=403, detail="forbidden")
    if not account_id:
        row["account_id"] = auth.uid


def _enforce_rate_limit(request: Request, auth: AuthContext, *, write: bool) -> None:
    now = time.time()
    _prune_rate_limit_hits(now)

    identifier = auth.uid or _extract_client_ip(request) or "unknown"
    lane = "write" if write else "read"
    key = f"{lane}:{identifier}"
    window = _rate_limit_hits[key]

    while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()

    max_requests = RATE_LIMIT_WRITE_MAX if write else RATE_LIMIT_READ_MAX
    if len(window) >= max_requests:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    window.append(now)


def _assert_device_id(device_id: str) -> None:
    if not re.fullmatch(DEVICE_ID_PATTERN, device_id):
        raise HTTPException(status_code=400, detail="invalid device_id")


def _assert_file_id(file_id: str) -> None:
    if not re.fullmatch(FILE_ID_PATTERN, file_id):
        raise HTTPException(status_code=400, detail="invalid file_id")


def _assert_chunk_hash(chunk_hash: str) -> None:
    if not CHUNK_HASH_PATTERN.fullmatch(chunk_hash):
        raise HTTPException(status_code=400, detail="invalid chunk hash")


def _normalize_peer_url(raw_url: str, *, require_path: bool = True) -> str:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"invalid URL: {raw_url}")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="URL credentials are not allowed")
    if parsed.query or parsed.fragment:
        raise HTTPException(status_code=400, detail="URL query/fragment not allowed")

    normalized_path = parsed.path.rstrip("/")
    if require_path and not normalized_path:
        normalized_path = ""
    elif not require_path and normalized_path not in {"", "/"}:
        raise HTTPException(status_code=400, detail="public_url must not include path")

    base = f"{parsed.scheme}://{parsed.netloc}"
    return f"{base}{normalized_path}" if normalized_path else base


def _to_public_peer(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_id": row.get("device_id", ""),
        "public_key": row.get("public_key", ""),
        "public_ip": row.get("public_ip"),
        "public_port": row.get("public_port"),
        "public_url": row.get("public_url"),
        "local_port": row.get("local_port"),
        "role": row.get("role", "consumer"),
        "available_storage": row.get("available_storage", 0),
        "nat_type": row.get("nat_type"),
        "relay_urls": row.get("relay_urls", []),
        "has_direct_endpoint": bool(row.get("has_direct_endpoint", False)),
    }


def _to_public_manifest(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "owner_id": row.get("owner_id"),
        "file_id": row.get("file_id"),
        "encrypted_payload": row.get("encrypted_payload"),
        "device_id": row.get("device_id"),
        "created_at": row.get("created_at"),
    }


def _extract_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        first_hop = forwarded.split(",")[0].strip()
        if first_hop and first_hop.lower() != "unknown":
            return first_hop
    if request.client and request.client.host:
        host = request.client.host
        if host.startswith("::ffff:"):
            return host[7:]
        return host
    return None


def _extract_chunk_hash(path: str) -> str | None:
    if not path.startswith("/chunks/"):
        return None
    parts = path.split("/")
    if len(parts) != 3:
        return None
    chunk_hash = parts[2].strip()
    return chunk_hash or None


def _build_target_url(peer: dict[str, Any], *, path: str, query_string: str) -> str:
    base = str(peer.get("public_url") or "").strip()
    if not base:
        public_ip = str(peer.get("public_ip") or "").strip()
        public_port_raw = peer.get("public_port")
        if not public_ip or public_port_raw is None:
            raise HTTPException(status_code=503, detail="peer has no reachable endpoint")
        try:
            public_port = int(public_port_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="peer has invalid public_port") from exc
        if public_port < 1 or public_port > 65535:
            raise HTTPException(status_code=503, detail="peer has invalid public_port")
        base = f"http://{public_ip}:{public_port}"

    normalized_base = _sanitize_upstream_base(base)
    normalized_path = path if path.startswith("/") else f"/{path}"
    target = f"{normalized_base}{normalized_path}"
    if query_string:
        target = f"{target}?{query_string}"
    return target


def _sanitize_upstream_base(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=503, detail="peer endpoint must be http or https")
    if not parsed.netloc:
        raise HTTPException(status_code=503, detail="peer endpoint is missing host")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=503, detail="peer endpoint cannot include credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise HTTPException(status_code=503, detail="peer endpoint must be origin-only URL")

    host = parsed.hostname
    if host is None:
        raise HTTPException(status_code=503, detail="peer endpoint host is invalid")
    if not ALLOW_PRIVATE_UPSTREAMS and _is_non_public_host(host):
        raise HTTPException(status_code=503, detail="peer endpoint host is not publicly routable")

    return f"{parsed.scheme}://{parsed.netloc}"


def _is_non_public_host(host: str) -> bool:
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".local") or lowered.endswith(".internal"):
        return True

    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False

    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _assert_allowed_proxy_path(path: str, method: str) -> None:
    if path in {"/health", "/info", "/manifests"}:
        if method != "GET":
            raise HTTPException(status_code=405, detail="method not allowed")
        return
    raise HTTPException(status_code=404, detail="path not relayed")


def _normalize_manifest_query(query_string: str, auth_uid: str | None) -> str:
    params = dict(parse_qsl(query_string, keep_blank_values=True))
    if auth_uid is not None:
        owner_id = (params.get("owner_id") or "").strip()
        if owner_id and owner_id != auth_uid:
            raise HTTPException(status_code=403, detail="owner_id mismatch")
        params["owner_id"] = auth_uid
    params["encrypted"] = "1"
    return urlencode(params)


def _should_prune(bucket: str) -> bool:
    now = time.time()
    last_run = _last_prune_runs.get(bucket, 0.0)
    if now - last_run < PRUNE_INTERVAL_SECONDS:
        return False
    _last_prune_runs[bucket] = now
    return True


def _prune_stale_peers() -> None:
    if not _should_prune("peers"):
        return
    now = time.time()

    if _gcs_bucket is not None:
        for blob_name, row in _iter_gcs_json("relay/peers/"):
            if now - float(row.get("_last_seen", 0)) > PEER_TTL_SECONDS:
                _gcs_delete_blob(blob_name)
        return

    stale_device_ids = [
        device_id
        for device_id, row in _peers.items()
        if now - float(row.get("_last_seen", 0)) > PEER_TTL_SECONDS
    ]
    for device_id in stale_device_ids:
        _peers.pop(device_id, None)


def _prune_stale_relay_chunks() -> None:
    if not _should_prune("chunks"):
        return
    now = time.time()

    if _gcs_bucket is not None:
        for blob_name, row in _iter_gcs_json("relay/chunks/"):
            if not blob_name.endswith(".meta.json"):
                continue
            if now - float(row.get("_last_seen", 0)) > RELAY_CHUNK_TTL_SECONDS:
                data_blob_name = blob_name.replace(".meta.json", ".bin")
                _gcs_delete_blob(data_blob_name)
                _gcs_delete_blob(blob_name)
        return

    stale_keys = [
        key
        for key, row in _relay_chunks.items()
        if now - float(row.get("_last_seen", 0)) > RELAY_CHUNK_TTL_SECONDS
    ]
    for key in stale_keys:
        _relay_chunks.pop(key, None)


def _prune_stale_manifests() -> None:
    if not _should_prune("manifests"):
        return
    now = time.time()

    if _gcs_bucket is not None:
        for blob_name, row in _iter_gcs_json("relay/manifests/"):
            if now - float(row.get("_last_seen", 0)) > MANIFEST_TTL_SECONDS:
                _gcs_delete_blob(blob_name)
        return

    stale_keys = [
        key
        for key, row in _manifest_envelopes.items()
        if now - float(row.get("_last_seen", 0)) > MANIFEST_TTL_SECONDS
    ]
    for key in stale_keys:
        _manifest_envelopes.pop(key, None)


def _peer_blob_path(device_id: str) -> str:
    return f"relay/peers/{device_id}.json"


def _manifest_blob_path(owner_id: str, file_id: str) -> str:
    return f"relay/manifests/{owner_id}/{file_id}.json"


def _chunk_blob_path(device_id: str, chunk_hash: str) -> str:
    return f"relay/chunks/{device_id}/{chunk_hash}.bin"


def _chunk_meta_blob_path(device_id: str, chunk_hash: str) -> str:
    return f"relay/chunks/{device_id}/{chunk_hash}.meta.json"


def _get_peer(device_id: str) -> dict[str, Any] | None:
    if _gcs_bucket is not None:
        return _gcs_read_json(_peer_blob_path(device_id))
    return _peers.get(device_id)


def _set_peer(device_id: str, row: dict[str, Any]) -> None:
    if _gcs_bucket is not None:
        _gcs_write_json(_peer_blob_path(device_id), row)
        return
    _peers[device_id] = row


def _delete_peer(device_id: str) -> None:
    if _gcs_bucket is not None:
        _gcs_delete_blob(_peer_blob_path(device_id))
        return
    _peers.pop(device_id, None)


def _list_peers() -> list[dict[str, Any]]:
    if _gcs_bucket is not None:
        return [row for _, row in _iter_gcs_json("relay/peers/")]
    return list(_peers.values())


def _set_manifest(owner_id: str, file_id: str, row: dict[str, Any]) -> None:
    if _gcs_bucket is not None:
        _gcs_write_json(_manifest_blob_path(owner_id, file_id), row)
        return
    _manifest_envelopes[(owner_id, file_id)] = row


def _delete_manifest(owner_id: str, file_id: str) -> None:
    if _gcs_bucket is not None:
        _gcs_delete_blob(_manifest_blob_path(owner_id, file_id))
        return
    _manifest_envelopes.pop((owner_id, file_id), None)


def _list_manifests_by_owner(owner_id: str) -> list[dict[str, Any]]:
    if _gcs_bucket is not None:
        prefix = f"relay/manifests/{owner_id}/"
        return [row for _, row in _iter_gcs_json(prefix)]
    return [
        row
        for (entry_owner_id, _), row in _manifest_envelopes.items()
        if entry_owner_id == owner_id
    ]


def _store_chunk(
    device_id: str,
    chunk_hash: str,
    payload: bytes,
    file_id: str,
    account_id: str,
) -> int:
    now = time.time()
    if _gcs_bucket is not None:
        data_blob_name = _chunk_blob_path(device_id, chunk_hash)
        meta_blob_name = _chunk_meta_blob_path(device_id, chunk_hash)
        existing_meta = _gcs_read_json(meta_blob_name) or {}
        existing_account = str(existing_meta.get("account_id") or "").strip()
        if existing_account and existing_account != account_id:
            raise HTTPException(status_code=403, detail="forbidden")

        refs = set(existing_meta.get("refs", []))
        if file_id:
            refs.add(file_id)
        meta = {
            "account_id": account_id,
            "refs": sorted(refs),
            "_last_seen": now,
        }

        _gcs_write_blob(data_blob_name, payload, content_type="application/octet-stream")
        _gcs_write_json(meta_blob_name, meta)
        return len(refs)

    key = (device_id, chunk_hash)
    entry = _relay_chunks.get(key)
    refs = set(entry.get("refs", set())) if entry else set()
    existing_account = str(entry.get("account_id") or "").strip() if entry else ""
    if existing_account and existing_account != account_id:
        raise HTTPException(status_code=403, detail="forbidden")
    if file_id:
        refs.add(file_id)

    _relay_chunks[key] = {
        "data": payload,
        "refs": refs,
        "account_id": account_id,
        "_last_seen": now,
    }
    return len(refs)


def _get_chunk(device_id: str, chunk_hash: str, auth: AuthContext) -> bytes | None:
    if _gcs_bucket is not None:
        data_blob_name = _chunk_blob_path(device_id, chunk_hash)
        meta_blob_name = _chunk_meta_blob_path(device_id, chunk_hash)
        meta = _gcs_read_json(meta_blob_name)
        if meta is None:
            return None
        _assert_account_access(meta, auth)
        meta["_last_seen"] = time.time()
        _gcs_write_json(meta_blob_name, meta)
        return _gcs_read_blob(data_blob_name)

    key = (device_id, chunk_hash)
    entry = _relay_chunks.get(key)
    if entry is None:
        return None
    _assert_account_access(entry, auth)
    entry["_last_seen"] = time.time()
    _relay_chunks[key] = entry
    return entry["data"]


def _delete_chunk(
    device_id: str,
    chunk_hash: str,
    file_id: str,
    auth: AuthContext,
) -> tuple[bool, int]:
    now = time.time()
    if _gcs_bucket is not None:
        data_blob_name = _chunk_blob_path(device_id, chunk_hash)
        meta_blob_name = _chunk_meta_blob_path(device_id, chunk_hash)
        meta = _gcs_read_json(meta_blob_name)
        if meta is None:
            return False, 0
        _assert_account_access(meta, auth)

        refs = set(meta.get("refs", []))
        if not file_id:
            refs.clear()
        else:
            refs.discard(file_id)

        if not refs:
            _gcs_delete_blob(data_blob_name)
            _gcs_delete_blob(meta_blob_name)
            return True, 0

        meta["refs"] = sorted(refs)
        meta["_last_seen"] = now
        _gcs_write_json(meta_blob_name, meta)
        return True, len(refs)

    key = (device_id, chunk_hash)
    entry = _relay_chunks.get(key)
    if entry is None:
        return False, 0
    _assert_account_access(entry, auth)

    refs = set(entry.get("refs", set()))
    if not file_id:
        refs.clear()
    else:
        refs.discard(file_id)

    if not refs:
        _relay_chunks.pop(key, None)
        return True, 0

    entry["refs"] = refs
    entry["_last_seen"] = now
    _relay_chunks[key] = entry
    return True, len(refs)


def _gcs_read_blob(blob_name: str) -> bytes | None:
    if _gcs_bucket is None:
        return None
    blob = _gcs_bucket.blob(blob_name)
    try:
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except GcsNotFound:
        return None
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"storage backend read failed: {exc}") from exc


def _gcs_write_blob(blob_name: str, payload: bytes, *, content_type: str) -> None:
    if _gcs_bucket is None:
        return
    blob = _gcs_bucket.blob(blob_name)
    try:
        blob.upload_from_string(payload, content_type=content_type)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"storage backend write failed: {exc}") from exc


def _gcs_read_json(blob_name: str) -> dict[str, Any] | None:
    payload = _gcs_read_blob(blob_name)
    if payload is None:
        return None
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"storage metadata corrupted at {blob_name}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=500, detail=f"invalid metadata at {blob_name}")
    return parsed


def _gcs_write_json(blob_name: str, payload: dict[str, Any]) -> None:
    _gcs_write_blob(
        blob_name,
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"),
        content_type="application/json",
    )


def _gcs_delete_blob(blob_name: str) -> None:
    if _gcs_bucket is None:
        return
    blob = _gcs_bucket.blob(blob_name)
    try:
        blob.delete()
    except GcsNotFound:
        return
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"storage backend delete failed: {exc}") from exc


def _iter_gcs_json(prefix: str) -> list[tuple[str, dict[str, Any]]]:
    if _gcs_bucket is None:
        return []
    try:
        blobs = list(_gcs_bucket.list_blobs(prefix=prefix))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"storage backend list failed: {exc}") from exc

    rows: list[tuple[str, dict[str, Any]]] = []
    for blob in blobs:
        if not blob.name.endswith(".json"):
            continue
        try:
            payload = blob.download_as_bytes()
        except GcsNotFound:
            continue
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"storage backend read failed: {exc}") from exc

        try:
            parsed = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"storage metadata corrupted at {blob.name}") from exc
        if isinstance(parsed, dict):
            rows.append((blob.name, parsed))
    return rows


logger.info(
    "FireCloud signaling relay started (auth_mode=%s, durable_storage=%s)",
    AUTH_MODE,
    _gcs_bucket is not None,
)
