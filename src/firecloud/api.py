from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .audit_consensus import AppealReason, AuditConsensusManager, create_signed_vote
from .controller import FireCloudController
from .discovery import NetworkManager
from .identity import DeviceIdentityManager, generate_hardware_fingerprint
from .node_roles import NodeRole, NodeRoleManager
from .security import SecurityMiddleware, SignedRequest


class AddNodeRequest(BaseModel):
    node_id: str = Field(..., description="Node identifier")
    endpoint: str = Field(..., description="Node endpoint/path")
    kind: str = Field(default="http", description="Node kind: http")


class NodeRoleRequest(BaseModel):
    role: str = Field(..., description="Node role: storage or consumer")
    storage_bytes: int | None = Field(default=None, ge=1)


class NodeQuotaRequest(BaseModel):
    total_bytes: int = Field(..., ge=1)


class AuditAppealRequest(BaseModel):
    requester_device_id: str = Field(..., min_length=1)
    requester_public_key: str = Field(..., min_length=1)
    reason: str
    justification: str = Field(..., min_length=1)
    scope_start: str | None = None
    scope_end: str | None = None
    scope_event_types: list[str] | None = None
    evidence_b64: str | None = None
    evidence_description: str = ""


class AuditVoteRequest(BaseModel):
    voter_device_id: str = Field(..., min_length=1)
    voter_public_key: str = Field(..., min_length=1)
    vote: bool
    reason: str = ""


class AuditAccessStatusRequest(BaseModel):
    requester_device_id: str = Field(..., min_length=1)
    requester_public_key: str = Field(..., min_length=1)


class NetworkStorageStatus(BaseModel):
    online_http_with_capacity: int
    required_http_peers: int
    total_http_available_capacity: int
    storage_ready: bool


_SIGNED_HEADER_NAMES = (
    "x-firecloud-request-id",
    "x-firecloud-timestamp",
    "x-firecloud-nonce",
    "x-firecloud-device-id",
    "x-firecloud-public-key",
    "x-firecloud-signature",
    "x-firecloud-body-hash",
)


def _operation_for_request(method: str, path: str) -> str:
    if method == "POST" and path == "/files/upload":
        return "upload"
    if method == "GET" and path.endswith("/download"):
        return "download"
    return "request"


def create_api(
    controller: FireCloudController,
    security: SecurityMiddleware | None = None,
    require_signed_requests: bool = False,
) -> FastAPI:
    identity_manager = DeviceIdentityManager(controller.config.root_dir / "identity")
    role_manager = NodeRoleManager(
        data_dir=controller.config.root_dir / "node-role",
        storage_dir=controller.config.root_dir / "node-role-storage",
    )
    consensus_manager = AuditConsensusManager(controller.config.root_dir / "audit-consensus")

    local_identity = identity_manager.get_identity()
    local_fp, _ = generate_hardware_fingerprint()

    try:
        role_manager.get_state()
    except ValueError:
        role_manager.initialize_node(
            device_id=local_identity.device_id,
            role=NodeRole.STORAGE if local_identity.node_type == "storage" else NodeRole.CONSUMER,
            storage_bytes=10 * 1024 * 1024 * 1024 if local_identity.node_type == "storage" else None,
        )

    security_layer = security
    if require_signed_requests and security_layer is None:
        security_layer = SecurityMiddleware(data_dir=controller.config.root_dir / "security")

    if security_layer is not None:
        security_layer.sybil_protection.register_device(
            device_id=local_identity.device_id,
            public_key=local_identity.public_key,
            fingerprint_hash=local_fp,
            platform="api-local",
        )

    # Use a mutable container to share network_manager between lifespan and endpoints
    app_state = {"network_manager": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup
        identity = identity_manager.get_identity()
        state = role_manager.get_state()
        storage_bytes = state.quota.available_bytes if state.quota else 0
        network_manager = NetworkManager(
            device_id=identity.device_id,
            port=8080,
            node_type=state.role.value,
            public_key=identity.public_key,
            available_storage=storage_bytes,
            bootstrap_peers=list(controller.config.bootstrap_peers),
        )
        network_manager.start()
        app_state["network_manager"] = network_manager
        yield
        # Shutdown
        network_manager.stop()
        app_state["network_manager"] = None

    app = FastAPI(title="FireCloud Python MVP", version="0.1.0", lifespan=lifespan)

    if security_layer is not None:
        @app.middleware("http")
        async def firecloud_security_middleware(request: Request, call_next):
            path = request.url.path
            if path in {"/health", "/docs", "/openapi.json", "/redoc"}:
                return await call_next(request)

            headers = {k.lower(): v for k, v in request.headers.items()}
            present_headers = [header for header in _SIGNED_HEADER_NAMES if header in headers]
            if require_signed_requests and len(present_headers) < len(_SIGNED_HEADER_NAMES):
                return JSONResponse(status_code=401, content={"detail": "Signed request headers required"})
            if not require_signed_requests and len(present_headers) == 0:
                return await call_next(request)
            if 0 < len(present_headers) < len(_SIGNED_HEADER_NAMES):
                missing = [header for header in _SIGNED_HEADER_NAMES if header not in headers]
                return JSONResponse(
                    status_code=400,
                    content={"detail": f"Incomplete signed request headers; missing: {', '.join(missing)}"},
                )

            body = await request.body()
            signed_req = SignedRequest.from_headers(
                method=request.method,
                path=path,
                headers=dict(request.headers),
            )
            operation = _operation_for_request(method=request.method.upper(), path=path)
            allowed, reason = security_layer.validate_request(
                req=signed_req,
                body=body,
                operation=operation,
            )
            if not allowed:
                return JSONResponse(status_code=401, content={"detail": reason})
            return await call_next(request)

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

    @app.get("/network/storage-status")
    def network_storage_status() -> dict[str, object]:
        summary = controller.storage_availability_summary()
        required = controller.config.fec.total_symbols
        if controller.config.decentralized_mode:
            storage_ready = summary["online_http_with_capacity"] >= required
        else:
            storage_ready = summary["online_nodes"] >= required
        return NetworkStorageStatus(
            online_http_with_capacity=summary["online_http_with_capacity"],
            required_http_peers=required,
            total_http_available_capacity=summary["total_http_available_capacity"],
            storage_ready=storage_ready,
        ).model_dump()

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
    def audit_events(
        requester_device_id: str = Query(..., min_length=1),
        requester_public_key: str = Query(..., min_length=1),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[dict[str, object]]:
        grant = consensus_manager.get_active_grant_for_requester(
            requester_device_id=requester_device_id,
            requester_public_key=requester_public_key,
        )
        if grant is None:
            raise HTTPException(
                status_code=403,
                detail="Audit access requires 51% consensus approval",
            )
        if not consensus_manager.record_access(grant.grant_id):
            raise HTTPException(status_code=403, detail="Audit access grant is no longer valid")

        events = controller.audit_events(limit=limit)
        scoped_events = [
            event
            for event in events
            if grant.scope_start <= event.event_time <= grant.scope_end
            and (
                len(grant.scope_event_types) == 0
                or event.event_type in grant.scope_event_types
            )
        ]
        return [
            {
                "sequence": event.sequence,
                "event_time": event.event_time,
                "event_type": event.event_type,
                "payload": event.payload,
                "prev_hash": event.prev_hash,
                "event_hash": event.event_hash,
            }
            for event in scoped_events
        ]

    @app.get("/audit/verify")
    def audit_verify(
        requester_device_id: str = Query(..., min_length=1),
        requester_public_key: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        grant = consensus_manager.get_active_grant_for_requester(
            requester_device_id=requester_device_id,
            requester_public_key=requester_public_key,
        )
        if grant is None:
            raise HTTPException(
                status_code=403,
                detail="Audit verification requires 51% consensus approval",
            )
        if not consensus_manager.record_access(grant.grant_id):
            raise HTTPException(status_code=403, detail="Audit access grant is no longer valid")
        valid, details = controller.verify_audit_chain()
        return {"valid": valid, "details": details}

    @app.get("/node/state")
    def node_state() -> dict[str, object]:
        state = role_manager.get_state()
        return state.to_dict()

    @app.get("/node/role")
    def get_node_role() -> dict[str, str]:
        """Get current node role."""
        state = role_manager.get_state()
        return {"role": state.role.value}

    @app.post("/node/role")
    def set_node_role(payload: NodeRoleRequest) -> dict[str, object]:
        try:
            target_role = NodeRole(payload.role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="role must be 'storage' or 'consumer'") from exc
        try:
            transfer = role_manager.initiate_role_switch(
                new_role=target_role,
                storage_bytes=payload.storage_bytes,
            )
            identity_manager.change_node_type(target_role.value)
            state = role_manager.get_state()
            network_manager = app_state["network_manager"]
            if network_manager is not None:
                network_manager.update_node_type(target_role.value)
                if state.quota:
                    network_manager.update_storage(state.quota.available_bytes)
            return {"role": state.role.value, "pending_transfer": transfer.to_dict() if transfer else None}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/node/quota")
    def set_node_quota(payload: NodeQuotaRequest) -> dict[str, object]:
        try:
            quota = role_manager.update_quota(total_bytes=payload.total_bytes)
            network_manager = app_state["network_manager"]
            if network_manager is not None:
                network_manager.update_storage(quota.available_bytes)
            return {"quota": quota.to_dict()}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/network/peers")
    def network_peers() -> list[dict[str, object]]:
        network_manager = app_state["network_manager"]
        if network_manager is None:
            return []
        return [peer.to_dict() for peer in network_manager.get_peers()]

    @app.get("/network/stats")
    def network_stats() -> dict[str, object]:
        network_manager = app_state["network_manager"]
        if network_manager is None:
            return {"online_peers": 0, "storage_nodes": 0, "total_network_storage": 0}
        return network_manager.get_network_stats()

    @app.get("/network/bootstrap/status")
    def network_bootstrap_status() -> dict[str, object]:
        network_manager = app_state["network_manager"]
        if network_manager is None:
            return {
                "bootstrap_peers": [],
                "last_refresh": None,
                "last_refresh_error": None,
                "refresh_interval_seconds": 0,
            }
        return network_manager.bootstrap_status()

    @app.post("/network/bootstrap/refresh")
    def network_bootstrap_refresh() -> dict[str, object]:
        network_manager = app_state["network_manager"]
        if network_manager is None:
            return {
                "bootstrap_peers": [],
                "attempted": 0,
                "successful": 0,
                "imported": 0,
                "error": None,
            }
        return network_manager.refresh_peers()

    @app.post("/audit/appeals")
    def create_audit_appeal(payload: AuditAppealRequest) -> dict[str, object]:
        try:
            reason = AppealReason(payload.reason)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unsupported appeal reason: {payload.reason}") from exc
        evidence = payload.evidence_b64.encode() if payload.evidence_b64 else None
        appeal = consensus_manager.create_appeal(
            requester_device_id=payload.requester_device_id,
            requester_public_key=payload.requester_public_key,
            reason=reason,
            justification=payload.justification,
            evidence=evidence,
            evidence_description=payload.evidence_description,
            scope_start=payload.scope_start,
            scope_end=payload.scope_end,
            scope_event_types=payload.scope_event_types,
        )
        return {"appeal_id": appeal.appeal_id, "status": appeal.status, "expires_at": appeal.expires_at}

    @app.get("/audit/appeals/pending")
    def list_pending_appeals() -> list[dict[str, object]]:
        network_manager = app_state["network_manager"]
        peers = len(network_manager.get_peers()) if network_manager is not None else len(controller.list_nodes())
        total_voters = max(1, peers)
        pending = consensus_manager.list_pending_appeals()
        response: list[dict[str, object]] = []
        for appeal in pending:
            status = consensus_manager.get_vote_status(appeal.appeal_id, total_eligible_voters=total_voters)
            response.append(
                {
                    "appeal_id": appeal.appeal_id,
                    "requester_device_id": appeal.requester_device_id,
                    "reason": appeal.reason.value,
                    "justification": appeal.justification,
                    "status": appeal.status,
                    "created_at": appeal.created_at,
                    "expires_at": appeal.expires_at,
                    "vote_count": status.get("total_votes", 0),
                    "votes_needed": status.get("votes_needed", 0),
                }
            )
        return response

    @app.post("/audit/appeals/{appeal_id}/vote")
    def vote_on_appeal(appeal_id: str, payload: AuditVoteRequest) -> dict[str, object]:
        try:
            vote_data = create_signed_vote(
                appeal_id=appeal_id,
                voter_device_id=payload.voter_device_id,
                vote=payload.vote,
                reason=payload.reason,
                sign_callback=identity_manager.sign_message,
            )
            consensus_manager.submit_vote(
                appeal_id=appeal_id,
                voter_device_id=payload.voter_device_id,
                voter_public_key=payload.voter_public_key,
                vote=payload.vote,
                reason=payload.reason,
                signature=vote_data["signature"],
            )
            network_manager = app_state["network_manager"]
            peers = len(network_manager.get_peers()) if network_manager is not None else len(controller.list_nodes())
            total_voters = max(peers, consensus_manager.MIN_VOTERS)
            status = consensus_manager.get_vote_status(appeal_id=appeal_id, total_eligible_voters=total_voters)
            should_finalize = status["threshold_met"] or status["is_expired"] or (
                status["total_votes"] >= total_voters
            )
            grant = None
            if should_finalize:
                grant = consensus_manager.finalize_appeal(
                    appeal_id=appeal_id,
                    total_eligible_voters=total_voters,
                )
            status = consensus_manager.get_vote_status(appeal_id=appeal_id, total_eligible_voters=total_voters)
            return {"status": status, "grant_id": grant.grant_id if grant else None}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/audit/access-status")
    def audit_access_status(payload: AuditAccessStatusRequest) -> dict[str, object]:
        return consensus_manager.access_status(
            requester_device_id=payload.requester_device_id,
            requester_public_key=payload.requester_public_key,
        )

    @app.get("/audit/reasons")
    def audit_reasons() -> list[str]:
        return [reason.value for reason in AppealReason]

    return app
