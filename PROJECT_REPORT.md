# FireCloud Project Report

**Project:** FireCloud  
**Repository:** `firecloud`  
**Report type:** Full technical + product/operational documentation  
**Assessment mode:** Read-only codebase audit (no implementation files modified)

---

## 1. Executive Summary

FireCloud is a multi-surface distributed storage project composed of:

1. **Python backend/core** (`src/firecloud`) for chunking, encryption, erasure coding, metadata, audit, API, and node management.
2. **Flutter mobile app** (`mobile/`) that runs a local peer node (HTTP + LAN/WAN discovery + storage role logic).
3. **Tauri desktop app** (`desktop/`) that acts as a desktop client for the Python API.
4. **Signaling + relay service** (`signal-relay-prototype/`) for WAN peer discovery, manifest sync, and relay chunk fallback.

The system already implements major storage pipeline features (FastCDC, dedup index/refcounting, adaptive compression decisions, XChaCha20-Poly1305 in Python, RaptorQ/FEC, repair, and dedup GC), but product maturity differs by surface:

- **Python backend:** most complete.
- **Mobile app:** feature-rich and usable; some cryptography/identity pieces are explicitly prototype-grade.
- **Desktop app:** command bridge is broad, UI is still early and mostly file-centric.
- **Relay service:** strongly hardened for auth/account scoping and proxy safety.

---

## 2. Problem Statement and Project Goals

### 2.1 Problem addressed
Traditional cloud storage is centralized, trust-heavy, and potentially expensive. FireCloud aims to let user devices participate in a storage network where data is split, encrypted, and distributed.

### 2.2 Core goals in current implementation

- **Distributed redundancy** via erasure coding (default 3-of-5).
- **Storage efficiency** via content-defined chunking + dedup + conditional compression.
- **Auditability** via append-only hash-chained event logs with consensus-gated access.
- **Role-based participation** (consumer vs storage provider).
- **Cross-device availability** (manifest sync and peer discovery over LAN/WAN).

---

## 3. Repository and Component Map

| Area | Path | Stack | Responsibility |
|---|---|---|---|
| Core backend | `src/firecloud/` | Python, FastAPI, SQLite | Storage orchestration, metadata, API, discovery, security, audit consensus |
| Python tests | `tests/` | pytest | Unit/integration tests for backend modules |
| Mobile app | `mobile/` | Flutter/Dart, Riverpod | Local node runtime, P2P data operations, UI, auth, sync |
| Desktop app | `desktop/` | SvelteKit + Tauri (Rust) | Desktop UI + Rust API bridge to backend endpoints |
| WAN relay | `signal-relay-prototype/` | FastAPI, httpx, firebase-admin | Account-scoped signaling + relay proxy/chunk cache |
| Ops scripts | `scripts/` | Bash | bootstrap, run, test, build helpers |

---

## 4. End-to-End Functional Architecture

### 4.1 Upload flow (Python backend)

1. File bytes split with **FastCDC** (`split_bytes_fastcdc`).
2. Each chunk hashed (`blake3`) for dedup lookup.
3. Compression applied per extension/content heuristic (`compress_chunk`), only if savings threshold met.
4. Compressed chunk encrypted with **XChaCha20-Poly1305** (`aad = chunk hash`).
5. Encrypted chunk encoded into symbols with **RaptorQ**.
6. Symbols distributed across online nodes via `StorageClient`.
7. Metadata committed transactionally (`files`, `chunks`, `symbols`, dedup tables).
8. Audit event appended (`file_uploaded`) in hash chain.

### 4.2 Download flow (Python backend)

1. Lookup file/chunk metadata.
2. Collect available symbols from online nodes.
3. Decode encrypted chunk from at least `source_symbols`.
4. Decrypt with XChaCha20-Poly1305 and chunk-specific AAD.
5. Decompress according to stored algorithm flag.
6. Reassemble output bytes in chunk order.
7. Append `file_downloaded` audit event.

### 4.3 Repair and dedup garbage collection

- **Repair** regenerates missing symbols when enough symbols remain.
- **Dedup GC** deletes symbol files and dedup index entries only after configurable grace period unless forced.

---

## 5. Python Backend Deep Dive (`src/firecloud`)

### 5.1 Configuration model (`config.py`)

Default technical parameters:

- **FEC:** `source_symbols=3`, `total_symbols=5`, `symbol_size=64KB`.
- **Chunking:** min `64KB`, avg `1MB`, max `4MB`, normalization `2`.
- **Compression:** enabled, min saving ratio `10%`, sample size `1MB`.
- **Dedup GC:** grace period `30 days`, max `1000` chunks/run.

Modes:

- **Local mode** (default): simulated local nodes.
- **Decentralized mode:** starts without local nodes and requires HTTP peers.

### 5.2 Data pipeline modules

| Module | Key details |
|---|---|
| `chunking.py` | FastCDC implementation with deterministic gear table |
| `compression.py` | zstd preferred, zlib fallback; extension-aware levels; skip already-compressed types |
| `crypto.py` | XChaCha20-Poly1305 AEAD via PyNaCl bindings |
| `fec.py` | pyraptorq path + GF(256) fallback (fallback capped at 255 total symbols) |
| `hashing.py` | BLAKE3 utilities for chunk and symbol hashing |

### 5.3 Storage abstraction and transport

- `storage_client.py` abstracts node symbol operations.
- `transport.py` provides:
  - `LocalNodeTransport` (filesystem-backed).
  - `HttpNodeTransport` (`/symbols` and `/stats` API calls).
- `storage.py` (`NodeStore`) enforces path traversal protections and chunk/symbol ID validation.
- `storage_api.py` exposes standalone storage-node API.

### 5.4 Metadata layer (`metadata.py`)

SQLite tables:

- `files`, `chunks`, `symbols`, `nodes`, `audit_events`
- Dedup internals: `dedup_chunks`, `chunk_dedup_refs`, `dedup_symbols`

Notable behavior:

- Transactional `commit_upload(...)` writes all file/chunk/symbol/dedup state.
- Dedup refcount lifecycle supports canonical chunk updates and GC marking.
- Audit events stored with previous hash linkage.

### 5.5 API surface (`api.py`)

#### Core endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/files` | List files |
| POST | `/files/upload` | Upload bytes (`file_name` query + octet-stream body) |
| GET | `/files/{file_id}/download` | Download file bytes |
| DELETE | `/files/{file_id}` | Delete file |
| POST | `/files/{file_id}/repair` | Repair symbol redundancy |
| POST | `/maintenance/dedup-gc` | Run dedup GC |

#### Node and network endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/nodes` | List nodes |
| POST | `/nodes/add` | Register node |
| DELETE | `/nodes/{node_id}` | Remove node |
| POST | `/nodes/{node_id}/offline` | Mark offline |
| POST | `/nodes/{node_id}/online` | Mark online |
| GET | `/network/storage-status` | Capacity/readiness summary |
| GET | `/network/peers` | Discovery peers |
| GET | `/network/stats` | Network stats |
| GET | `/network/bootstrap/status` | Bootstrap refresh status |
| POST | `/network/bootstrap/refresh` | Force bootstrap refresh |

#### Role/quota endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/node/state` | Role/quota state |
| GET | `/node/role` | Role summary |
| POST | `/node/role` | Switch role |
| POST | `/node/quota` | Update quota |

#### Audit consensus endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/audit/events` | Access scoped audit events (requires active grant) |
| GET | `/audit/verify` | Verify hash chain (requires active grant) |
| POST | `/audit/appeals` | Create audit access appeal |
| GET | `/audit/appeals/pending` | List pending appeals |
| POST | `/audit/appeals/{appeal_id}/vote` | Submit vote |
| POST | `/audit/access-status` | Check grant status |
| GET | `/audit/reasons` | Enumerate allowed appeal reasons |

### 5.6 Discovery (`discovery.py`)

- LAN discovery via multicast announcements:
  - address `224.0.0.251`, port `5353`
  - steady interval `30s`, startup burst `3x @ 1s`
- Bootstrap refresh manager for peer imports from configured API peers.
- Tracks online/offline peers and total available storage stats.

### 5.7 Security layer (`security.py`)

Controls implemented:

- Signed request model (method/path/body-hash/timestamp/nonce/device/public-key/signature).
- Rate limiter with burst + minute + hour + operation-specific limits.
- Replay protection via nonce store (SQLite-backed by default).
- Timestamp skew protection.
- Device fingerprint registry (anti-Sybil heuristics, banning, reputation fields).

Important operational behavior:

- API security middleware can be enabled, but **signed requests are not required by default** unless `create_api(..., require_signed_requests=True)` is used.

### 5.8 Audit consensus (`audit_consensus.py`)

- Voting threshold: **51%**, with `MIN_VOTERS=3`.
- Appeal statuses: `pending / approved / rejected / expired`.
- Approved appeals create time-limited access grants with scope controls:
  - time range,
  - event type filters,
  - max access count.

### 5.9 CLI and TUI

- CLI (`cli.py`) supports upload/download/delete/repair/node add/remove/gc/audit verification/API run/storage-node run/TUI run.
- Textual TUI (`tui/app.py`) provides two live tables (files/nodes) and command input for core operations.

---

## 6. Mobile Application Deep Dive (`mobile/`)

### 6.1 Stack and dependencies

- Flutter + Riverpod + GoRouter + Dio + SharedPreferences + Firebase Auth + Google Sign-In.
- Supports Android/iOS + desktop targets (Windows/Linux/macOS scaffolding exists).

### 6.2 Node runtime model

`FireCloudNode` starts a local HTTP server (default `:4001`) with endpoints:

| Method(s) | Path | Purpose |
|---|---|---|
| GET | `/health` | Node health |
| GET | `/info` | Role and storage state |
| GET/POST/PUT/DELETE | `/chunks/{hash}` | Chunk retrieval/storage/delete |
| GET | `/files` | Manifest-backed file list |
| GET | `/manifests` | Manifest sync endpoint (plain or encrypted envelope mode) |

Role behavior:

- **Storage provider:** can store chunks and maintain replicas.
- **Consumer:** local chunk persistence is purged by policy.

### 6.3 Local storage and chunk distribution

`local_storage.dart` handles:

- chunk store, manifest store, manifest cache,
- chunk reference bookkeeping (`chunk_refs.json`) for safe deletion,
- capacity checks and quota enforcement,
- distributor logic for multi-provider replication and relay fallback.

Replication behavior:

- Provider node keeps local copy + up to 4 remote targets.
- Consumer uploads to up to 5 remote providers.
- Explicit failures raised for no providers or failed replication.

### 6.4 Discovery and WAN signaling

LAN discovery (`peer_discovery.dart`):

- multicast `239.255.42.99:45454`,
- broadcast interval `5s`,
- startup burst and probe-based quick convergence.

WAN signaling (`signaling_client.dart`):

- register/heartbeat/poll against signaling server,
- account-scoped headers (`X-Account-ID`) + optional Firebase bearer token,
- relay URL propagation and endpoint candidate prioritization.

### 6.5 Auth and cross-device manifest sync

- Firebase Auth + Google Sign-In state provider.
- `ManifestSyncService`:
  - encrypts manifest envelopes per owner ID,
  - syncs from peers and signaling server,
  - persists encrypted manifest cache for offline restoration.

### 6.6 Storage lock and background mode

- Storage lock reserves disk via garbage-fill files.
- Background operation:
  - Android: foreground service + wakelock.
  - Desktop: tray/minimize-to-background pattern.
- Node settings UI controls role/quota/background/signaling/relay/theme/account.

### 6.7 Mobile UI surfaces

| Screen | Main capabilities |
|---|---|
| Files | Upload/download/delete, provider-capacity aware actions |
| Network | Peer discovery status, provider/consumer grouping |
| Settings | Account, role, quota lock, background mode, endpoint config, theme |
| Audit | Local audit logs and ledger request submission with attachments |

### 6.8 Security caveats in current mobile implementation

The mobile code explicitly includes non-production crypto/identity shortcuts:

- `mobile/lib/crypto/encryption.dart`: XOR-stream scheme (placeholder, not AEAD-grade).
- `mobile/lib/node/device_identity.dart`: simplified key generation/signing/verification (not full Ed25519 semantics).
- File encryption keys are stored in local JSON (`file_keys.json`) without OS keystore protection.

---

## 7. Desktop Application Deep Dive (`desktop/`)

### 7.1 Architecture

- Frontend: SvelteKit.
- Native wrapper: Tauri 2 (Rust).
- Rust command layer proxies calls to backend API (`server_url` default `http://localhost:8080`).

### 7.2 Rust command coverage

Implemented command groups:

- Health, file CRUD, node state/role/quota, network peers/stats, audit appeals/votes/events/verify, settings get/save.

### 7.3 Current maturity notes

- Frontend routes currently include only `+layout.svelte` and `+page.svelte` (files-centric UI).
- Sidebar shows Files/Audit/Settings, but routed page implementation is still mostly file management.
- Desktop audit identity falls back to static placeholder values when no device/public key exists.
- Settings are managed in in-memory app state; persistent settings storage is limited.

---

## 8. Signaling + Relay Service Deep Dive (`signal-relay-prototype/`)

### 8.1 Core responsibilities

- WAN peer registration/listing/heartbeat.
- Encrypted manifest envelope upsert/list/delete.
- Relay endpoint for proxied peer access and chunk cache fallback.

### 8.2 Endpoint inventory

| Method | Path |
|---|---|
| GET | `/health` |
| POST | `/api/v1/peers/register` |
| POST | `/api/v1/peers/heartbeat` |
| GET | `/api/v1/peers` |
| DELETE | `/api/v1/peers/{device_id}` |
| POST | `/api/v1/manifests/upsert` |
| GET | `/api/v1/manifests` |
| DELETE | `/api/v1/manifests/{file_id}` |
| GET/POST/PUT/PATCH/DELETE | `/relay/p2p/{device_id}/{path...}` |

### 8.3 Security and hardening controls

- Firebase bearer token verification (`required/optional/disabled` auth mode).
- Strict account binding (`uid` vs provided account/owner IDs).
- Rate limiting (separate read/write lanes).
- Relay proxy path allowlist (`/health`, `/info`, `/manifests` only).
- Chunk relay size limits.
- SSRF guardrails block private/non-routable upstream hosts by default.
- Relay chunk and manifest access is account-scoped.

### 8.4 Durability model

- In-memory mode for development.
- Optional GCS-backed durable mode for peers/manifests/chunk metadata+data.
- Can enforce durable-only startup with `FIRECLOUD_REQUIRE_DURABLE_STORAGE=true`.

---

## 9. Data Model and Persistence Summary

### 9.1 Backend (Python)

- SQLite metadata DB: file/chunk/symbol/node/audit/dedup state.
- Master encryption key stored in `master.key` (permissions set to `0600`).
- Identity/role/security/audit-consensus each persist under root data directories.

### 9.2 Mobile

- Application-documents `firecloud/` subtree:
  - chunks/manifests/cache/manifest_cache/chunk refs,
  - file key map (`file_keys.json`),
  - audit and ledger JSON stores.

### 9.3 Relay

- In-memory dictionaries or GCS object storage (config-driven).

---

## 10. Testing and Quality Posture

### 10.1 Python backend tests

- **71 pytest tests** in `tests/` (API, controller, storage API, metadata, FEC, security signing behavior, discovery, fault injection, CLI, compression, config/crypto/chunking).

Key validated areas include:

- upload/download/delete/repair flows,
- fault tolerance boundaries,
- dedup + GC lifecycle,
- signed request header handling,
- bootstrap discovery behavior,
- storage API validation and traversal resistance.

### 10.2 Mobile tests

- Dart tests cover chunking/manifest model, encryption helpers, signaling client behavior, manifest sync behavior, peer endpoint prioritization, node role manager, plus widget smoke.

### 10.3 Desktop and relay testing

- No dedicated automated test suite found for desktop Rust/Svelte integration.
- No dedicated automated test suite found for signaling-relay service in repository.

---

## 11. Build, Run, and Operations Snapshot

### 11.1 Python/core scripts (`scripts/`)

| Script | Purpose |
|---|---|
| `bootstrap.sh` | Create venv + install package (`-e .[dev]`) |
| `test.sh` | Run pytest |
| `run-api.sh` | Start FireCloud API |
| `run-storage-node.sh` | Start storage node API |
| `run-tui.sh` | Start Textual TUI |
| `verify-audit.sh` | Run audit chain verification |

### 11.2 Mobile build helper

- `scripts/build-mobile.sh` runs:
  - dependency fetch,
  - analyze,
  - tests,
  - APK build with optional signaling/relay compile-time defines.

### 11.3 Desktop build helper

- `scripts/build-desktop.sh` supports host or containerized Tauri builds, including Linux dependency checks.

### 11.4 Relay deployment

- `signal-relay-prototype/Dockerfile` available.
- README includes Cloud Run deployment steps and required environment variables.

---

## 12. Documentation vs Implementation Gap Analysis

| Document claim | Actual implementation status |
|---|---|
| README presents “fully decentralized / no central server” framing | LAN can run peer-to-peer, but WAN discovery/sync rely on signaling+relay service; desktop depends on backend API |
| README architecture mentions AES-256 in diagram text | Python backend uses XChaCha20-Poly1305 AEAD; mobile currently uses simplified XOR-based placeholder crypto |
| TODO says FastCDC/dedup/adaptive compression not implemented | These are implemented in current Python backend pipeline |
| `plan.md` describes Rust core + QUIC/Kademlia/Raft/SWIM target architecture | Current runtime is Python backend + Flutter mobile + Tauri desktop + HTTP-based relay/signaling |

---

## 13. Risks, Limitations, and Technical Debt

### 13.1 Security-sensitive gaps

- Mobile crypto and mobile identity/signature layers are explicitly prototype-grade.
- Mobile local key storage is not hardened with platform keystore.
- Backend request-signature enforcement is optional and not default-on.
- Desktop audit identity currently falls back to static placeholder identity values.

### 13.2 Product/architecture gaps

- Desktop UI coverage trails backend command/API capability.
- Desktop persistence model is limited for settings/identity continuity.
- Relay and desktop lack broad automated test coverage.
- System combines multiple architectural paradigms (local simulation, HTTP decentralized mode, mobile-native P2P, relay fallback), which increases integration complexity.

### 13.3 Operational concerns

- WAN mode requires correct signaling/relay endpoint and auth setup.
- Placeholder defaults (`signal.firecloud.app` / `relay.firecloud.app`) are not production-ready without deployment.

---

## 14. Recommended Next Milestones

1. **Security hardening first**
   - Replace mobile placeholder crypto/signing with production-grade primitives and secure key storage.
   - Enable/request signed backend requests in hardened environments by default.
2. **Desktop maturity**
   - Complete UI parity with Rust command layer and add persistent identity/settings handling.
3. **Test expansion**
   - Add relay service tests and desktop command/UI integration tests.
4. **Architecture alignment**
   - Reconcile README/TODO/plan content with current code reality and roadmap stages.
5. **Operational packaging**
   - Provide opinionated dev/prod environment templates for relay + API + client defaults.

---

## 15. Key File Reference Index

### Core backend

- `src/firecloud/controller.py`
- `src/firecloud/metadata.py`
- `src/firecloud/api.py`
- `src/firecloud/security.py`
- `src/firecloud/discovery.py`
- `src/firecloud/audit_consensus.py`
- `src/firecloud/storage_api.py`
- `src/firecloud/storage_client.py`
- `src/firecloud/transport.py`
- `src/firecloud/node_roles.py`
- `src/firecloud/identity.py`

### Mobile

- `mobile/lib/node/firecloud_node.dart`
- `mobile/lib/storage/local_storage.dart`
- `mobile/lib/p2p/peer_discovery.dart`
- `mobile/lib/p2p/signaling_client.dart`
- `mobile/lib/providers/node_provider.dart`
- `mobile/lib/providers/auth_provider.dart`
- `mobile/lib/providers/storage_lock_provider.dart`
- `mobile/lib/services/manifest_sync_service.dart`
- `mobile/lib/crypto/encryption.dart`

### Desktop

- `desktop/src/routes/+layout.svelte`
- `desktop/src/routes/+page.svelte`
- `desktop/src-tauri/src/commands.rs`
- `desktop/src-tauri/src/main.rs`
- `desktop/src-tauri/src/state.rs`
- `desktop/src-tauri/src/config.rs`

### Relay

- `signal-relay-prototype/main.py`
- `signal-relay-prototype/README.md`
- `signal-relay-prototype/Dockerfile`

---

## 16. Final Assessment

FireCloud is a substantial prototype with strong backend mechanics and ambitious cross-platform scope. The backend data pipeline and metadata/audit architecture are already meaningful and test-backed. The mobile product has rich workflow coverage and practical role-based behavior, while still carrying explicit cryptographic hardening debt. The relay service is comparatively mature in request-hardening and tenancy controls. The desktop layer is a promising shell with broad Rust command capabilities but incomplete UX parity.

This makes the project well-positioned for a hardening-and-consolidation phase: security normalization, documentation alignment, test expansion, and product-surface parity.
