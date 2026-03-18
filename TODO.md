# FireCloud Production Backlog (as of 2026-03-18)

This backlog translates `plan.md` into actionable implementation work for this Python codebase.

## Current Baseline

Implemented in MVP:

- Local in-process controller with upload/download/repair/audit.
- XChaCha20-Poly1305 chunk encryption.
- RaptorQ 3-of-5 erasure coding.
- Local node simulation via filesystem.
- SQLite metadata + hash-chained audit log.
- FastAPI + CLI + Textual TUI.

Not yet implemented from `plan.md`:

- Real network layer (QUIC, Kademlia DHT, NAT traversal, path selection).
- FastCDC chunking, dedup index, adaptive compression.
- Hierarchical key management and multi-user identity.
- Distributed control plane (membership, consensus, CRDT sync).
- Background repair queue, observability, hardening, deployment tooling.

## Plan Coverage Matrix

| Plan section | Status | Backlog IDs |
|---|---|---|
| 1. System Architecture | Partial | FC-101, FC-102, FC-103 |
| 2. Network Layer | Not started | FC-201, FC-202, FC-203, FC-204, FC-205 |
| 3. Storage Layer | Partial | FC-301, FC-302, FC-303, FC-304 |
| 4. Security Layer | Partial | FC-401, FC-402, FC-403, FC-404 |
| 5. Data Pipeline | Partial | FC-501, FC-502, FC-503 |
| 6. Distributed Components | Not started | FC-601, FC-602, FC-603 |
| 7. Client Applications | Partial | FC-701, FC-702 |
| 8. Performance | Spec only | FC-801, FC-802 |
| 9. Fault Tolerance | Partial | FC-901, FC-902 |
| 10. Scalability | Spec only | FC-1001, FC-1002 |

## Phase 1: Service Boundaries and Networked Storage (Critical Path)

### FC-101 Split in-process simulation into networked services
- [ ] Introduce service boundary between controller and storage nodes.
- Files:
  - `src/firecloud/controller.py` (refactor to orchestration-only)
  - `src/firecloud/storage.py` (keep local backend)
  - `src/firecloud/storage_client.py` (new)
  - `src/firecloud/models.py` (new)
  - `tests/test_controller.py` (update for mocked/real client paths)
- Done when:
  - Controller no longer reads/writes symbol files directly.
  - All symbol operations go through a client abstraction.

### FC-102 Add storage-node HTTP service
- [ ] Implement standalone storage-node API for symbol put/get/exists/list stats.
- Files:
  - `src/firecloud/storage_api.py` (new FastAPI app)
  - `src/firecloud/storage_node.py` (new runtime bootstrap)
  - `src/firecloud/api.py` (controller API wiring changes)
  - `tests/test_storage_api.py` (new)
  - `tests/test_controller.py` (networked node integration cases)
- Done when:
  - Multiple storage services can run independently.
  - Controller can upload/download/repair over HTTP.

### FC-103 Node registry and topology config
- [ ] Replace fixed `node-1..node-n` local assumptions with configurable node registry.
- Files:
  - `src/firecloud/config.py` (node endpoints schema)
  - `src/firecloud/metadata.py` (persist endpoint and capability info)
  - `src/firecloud/cli.py` (`node add/remove/list` commands)
  - `tests/test_config_chunking_crypto.py` (config validation)
- Done when:
  - Nodes can be dynamically registered and marked online/offline.

## Phase 2: Core Network Layer

### FC-201 QUIC-ready transport abstraction
- [ ] Add transport interface and HTTP implementation now, QUIC implementation path next.
- Files:
  - `src/firecloud/transport.py` (new interface)
  - `src/firecloud/storage_client.py` (HTTP transport impl)
  - `src/firecloud/config.py` (transport config)
  - `tests/test_transport.py` (new)
- Done when:
  - Storage client is decoupled from protocol details.
  - Switching transport implementation does not change controller logic.

### FC-202 Peer bootstrap and discovery
- [ ] Add bootstrap peer list and active peer refresh process.
- Files:
  - `src/firecloud/discovery.py` (new)
  - `src/firecloud/config.py` (bootstrap endpoints)
  - `src/firecloud/api.py` (peer health/discovery endpoints)
  - `tests/test_discovery.py` (new)
- Done when:
  - Node list can be refreshed from bootstrap peers.
  - Discovery failures degrade gracefully.

### FC-203 Kademlia-style manifest locator (incremental)
- [ ] Implement pluggable key-based manifest lookup with local backend and DHT-compatible interface.
- Files:
  - `src/firecloud/dht.py` (new interface + local adapter)
  - `src/firecloud/routing.py` (lookup orchestration)
  - `src/firecloud/controller.py` (use locator instead of direct metadata lookup)
  - `tests/test_routing.py` (extend)
- Done when:
  - Manifest resolution is key-based and backend-pluggable.

### FC-204 Intelligent path scoring
- [ ] Implement probe + scoring for selecting best node endpoint per operation.
- Files:
  - `src/firecloud/path_selection.py` (new)
  - `src/firecloud/storage_client.py` (apply selected endpoint)
  - `tests/test_path_selection.py` (new)
- Done when:
  - Endpoint selection uses measured latency/failure signals.

### FC-205 NAT traversal integration hooks
- [ ] Define candidate gathering and relay fallback hooks for future ICE/STUN/TURN support.
- Files:
  - `src/firecloud/connectivity.py` (new)
  - `src/firecloud/config.py` (STUN/TURN settings)
  - `tests/test_connectivity.py` (new)
- Done when:
  - Connectivity layer can expose direct and relay candidates.
  - Controller can choose fallback mode when direct path fails.

## Phase 3: Storage Pipeline Parity (FastCDC, Dedup, Compression)

### FC-301 Implement FastCDC chunker
- [ ] Replace fixed-size split with content-defined chunking.
- Files:
  - `src/firecloud/chunking.py` (FastCDC implementation)
  - `src/firecloud/controller.py` (adopt FastCDC output)
  - `tests/test_config_chunking_crypto.py` (new FastCDC tests)
  - `tests/test_controller.py` (boundary-shift behavior tests)
- Done when:
  - Configurable min/avg/max chunk sizes are enforced.
  - Small file and boundary-shift cases are covered by tests.

### FC-302 Deduplication index and reference counting
- [ ] Add chunk-hash dedup index with refcounts and GC state.
- Files:
  - `src/firecloud/hashing.py` (chunk hash API)
  - `src/firecloud/metadata.py` (dedup tables + migrations)
  - `src/firecloud/controller.py` (dedup lookup/store flow)
  - `tests/test_storage_metadata.py` (dedup CRUD/refcount)
  - `tests/test_controller.py` (cross-file dedup behavior)
- Done when:
  - Upload skips duplicate chunk storage.
  - Deleting/unreferencing updates refcount correctly.

### FC-303 Adaptive compression
- [ ] Add type-aware compression decisions and metadata flags.
- Files:
  - `src/firecloud/compression.py` (new; zstd strategies)
  - `src/firecloud/controller.py` (compress-before-encrypt path)
  - `src/firecloud/metadata.py` (per-chunk compression metadata)
  - `tests/test_compression.py` (new)
  - `tests/test_controller.py` (compress/decompress integrity)
- Done when:
  - Controller applies compression policy by file type/content signal.
  - Download fully restores original bytes.

### FC-304 Storage backend abstraction
- [ ] Add backend interface for metadata/chunks (SQLite today, RocksDB/LMDB-ready).
- Files:
  - `src/firecloud/metadata.py` (extract repository interface)
  - `src/firecloud/storage_backend.py` (new abstraction)
  - `tests/test_storage_metadata.py` (contract tests)
- Done when:
  - Controller depends on interface, not SQLite-specific methods.

## Phase 4: Security and Identity

### FC-401 Hierarchical key management
- [ ] Implement Argon2id + HKDF key hierarchy and per-file DEKs.
- Files:
  - `src/firecloud/crypto.py` (keep AEAD primitives)
  - `src/firecloud/keymgmt.py` (new)
  - `src/firecloud/controller.py` (per-file key flow)
  - `src/firecloud/metadata.py` (encrypted DEK metadata)
  - `tests/test_keymgmt.py` (new)
  - `tests/test_controller.py` (per-file key rotation/restart tests)
- Done when:
  - Master key derivation is password-based.
  - File DEKs are unique and encrypted at rest.

### FC-402 Multi-user authentication and authorization
- [ ] Add authenticated user model and ownership checks for file operations.
- Files:
  - `src/firecloud/auth.py` (new)
  - `src/firecloud/api.py` (auth dependencies and scopes)
  - `src/firecloud/metadata.py` (users, sessions, ownership tables)
  - `tests/test_api.py` (auth-required endpoint coverage)
- Done when:
  - File list/upload/download/repair are user-scoped.
  - Unauthorized access returns 401/403.

### FC-403 Asymmetric sharing primitives
- [ ] Add X25519-based shared-key envelopes and Ed25519 signatures for manifests.
- Files:
  - `src/firecloud/sharing.py` (new)
  - `src/firecloud/metadata.py` (share records/signature storage)
  - `src/firecloud/api.py` (`share`, `accept-share`, `list-shares`)
  - `tests/test_sharing.py` (new)
- Done when:
  - User A can share file access with User B without exposing plaintext keys.

### FC-404 Zero-knowledge metadata posture
- [ ] Encrypt sensitive metadata fields at rest and in transit.
- Files:
  - `src/firecloud/metadata.py`
  - `src/firecloud/controller.py`
  - `src/firecloud/api.py`
  - `tests/test_metadata_privacy.py` (new)
- Done when:
  - File names/paths are not stored plaintext in persistent metadata.

## Phase 5: End-to-End Pipeline Completion

### FC-501 Explicit upload pipeline stages
- [ ] Make upload stage transitions explicit and auditable (chunk -> dedup -> compress -> encrypt -> encode -> distribute -> manifest).
- Files:
  - `src/firecloud/controller.py`
  - `src/firecloud/metadata.py` (stage/audit enrichment)
  - `tests/test_controller.py` (stage flow assertions)
- Done when:
  - Upload path has deterministic stage ordering with recoverable errors.

### FC-502 Parallel download reconstruction
- [ ] Add parallel symbol fetch/decoding pipeline with bounded concurrency and retries.
- Files:
  - `src/firecloud/controller.py`
  - `src/firecloud/storage_client.py`
  - `src/firecloud/config.py` (concurrency limits/timeouts)
  - `tests/test_controller.py` (parallel + retry cases)
- Done when:
  - Download can recover from transient symbol fetch failures without full abort.

### FC-503 Local cache management
- [ ] Add local file cache with size limits and LRU eviction.
- Files:
  - `src/firecloud/cache.py` (new)
  - `src/firecloud/controller.py` (cache read/write path)
  - `src/firecloud/config.py` (cache limits)
  - `tests/test_cache.py` (new)
- Done when:
  - Repeated downloads can be served from cache.
  - Cache respects configured max size.

## Phase 6: Distributed Control Plane

### FC-601 Membership and health dissemination
- [ ] Implement membership protocol for online/offline/health state (SWIM-like).
- Files:
  - `src/firecloud/membership.py` (new)
  - `src/firecloud/controller.py` (replace manual status toggles with membership feed)
  - `src/firecloud/api.py` (cluster status endpoints)
  - `tests/test_membership.py` (new)
- Done when:
  - Node health state updates automatically from heartbeat signals.

### FC-602 Routing and manifest discovery
- [ ] Add pluggable manifest locator (local registry first, DHT later).
- Files:
  - `src/firecloud/routing.py` (new)
  - `src/firecloud/controller.py`
  - `src/firecloud/api.py`
  - `tests/test_routing.py` (new)
- Done when:
  - Controller can resolve manifests through routing abstraction.

### FC-603 Durable background jobs for repair/rebalance
- [ ] Move repair from synchronous API call into durable queued jobs.
- Files:
  - `src/firecloud/jobs.py` (new)
  - `src/firecloud/controller.py` (enqueue, status query)
  - `src/firecloud/api.py` (`POST /jobs/repair`, `GET /jobs/{id}`)
  - `tests/test_repair_jobs.py` (new)
- Done when:
  - Repair continues across process restarts.
  - Job status is queryable and auditable.

## Phase 7: Client/Application Surface

### FC-701 API and TUI operational parity
- [ ] Ensure API and TUI expose equivalent file/node/repair/audit actions and errors.
- Files:
  - `src/firecloud/api.py`
  - `src/firecloud/tui/app.py`
  - `tests/test_api.py`
  - `tests/test_cli.py`
- Done when:
  - Core operational workflows are achievable from API, CLI, and TUI consistently.

### FC-702 Multi-process local cluster tooling
- [ ] Add scripts and config for running controller + multiple storage nodes locally.
- Files:
  - `scripts/run-controller.sh` (new)
  - `scripts/run-storage-node.sh` (new)
  - `README.md` (cluster run instructions)
  - `.github/workflows/ci.yml` (smoke integration job)
- Done when:
  - Developer can boot a multi-process local cluster with documented commands.

## Phase 8: Reliability, Observability, and Hardening

### FC-801 Structured logs, metrics, tracing hooks
- [ ] Add observability baseline.
- Files:
  - `src/firecloud/observability.py` (new)
  - `src/firecloud/api.py`
  - `src/firecloud/controller.py`
  - `tests/test_api.py` (health/metrics assertions)
- Done when:
  - Structured request logs and operation metrics are emitted.

### FC-802 Benchmark harness and SLO tracking
- [ ] Add reproducible performance benchmarks mapped to section 8 targets.
- Files:
  - `scripts/bench.py` (new)
  - `tests/perf/` (new benchmark scenarios)
  - `README.md` (benchmark usage and baseline table)
- Done when:
  - Upload/download throughput baselines are versioned in repo.

### FC-901 Fault injection automation
- [ ] Expand fault tests to include process/network-level failures.
- Files:
  - `tests/test_fault_injection.py` (extend)
  - `tests/test_controller.py` (failure matrix cases)
  - `scripts/fault-sim.sh` (new)
- Done when:
  - Automated scenarios cover single-node and multi-node degraded states.

### FC-902 Security hardening and retention policies
- [ ] Add rate-limits, admission controls, audit retention, backup/restore drills.
- Files:
  - `src/firecloud/api.py`
  - `src/firecloud/metadata.py`
  - `scripts/backup.sh` (new)
  - `scripts/restore.sh` (new)
  - `tests/test_api.py` (rate-limit/admission tests)
- Done when:
  - Hardening controls are enabled by default in non-dev mode.

### FC-1001 Horizontal scale readiness checks
- [ ] Add load-testing and scaling guardrails.
- Files:
  - `scripts/load-test.py` (new)
  - `src/firecloud/config.py` (capacity/safety limits)
  - `README.md` (scale test guide)
- Done when:
  - Capacity limits and degradation behavior are documented and tested.

### FC-1002 Deployment/migration tooling
- [ ] Add schema migration and staged deployment support.
- Files:
  - `src/firecloud/migrations/` (new)
  - `scripts/deploy-check.sh` (new)
  - `.github/workflows/ci.yml` (migration checks)
  - `README.md` (upgrade/runbook)
- Done when:
  - Breaking metadata changes have explicit migration paths.

## Suggested Execution Order (first 3 milestones)

Milestone A (highest ROI):
- FC-101, FC-102, FC-103, FC-201

Milestone B (pipeline correctness/perf):
- FC-301, FC-302, FC-303, FC-501, FC-502

Milestone C (security baseline):
- FC-401, FC-402

After Milestone C, prioritize FC-603 (durable repairs) before deeper distributed work (FC-601/FC-602/FC-203).
