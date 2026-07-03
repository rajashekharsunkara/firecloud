# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.1] - 2026-07-03

### Fixed
- `firecloud --version` reports the installed package version instead of a
  hardcoded string.
- `__version__`, the mDNS version advertisement, and the telemetry server
  version now track the release.

### Added
- `firecloud init` reads `FIRECLOUD_PASSPHRASE`, so containers can be
  initialised without a prompt.
- Tests pinning the erasure-coding boundaries: reconstruction with exactly
  K shares on disk, verify status at N, K, and K-1 available shares, and
  K inference from the share count.

### Changed
- README rewritten around the rootless podman workflow, including a guide
  for running the network across separate machines.

## [0.2.0] - 2026-07-02

### Fixed
- A node now joins the whole cluster through one `--bootstrap` peer and pulls
  the file catalog on connect. Uploading on one machine and downloading on
  another used to fail with "not found in manifest" because the client only
  ever reached a single peer.
- The erasure-coding threshold K is capped at 170 so N stays inside zfec's
  256-block limit. Files over ~170 chunks crashed on upload before this.
- The handshake advertises the sender's listen port, so peers learn reachable
  addresses instead of the ephemeral source port. A node bound to port 0
  reads back the port it actually got.
- `verify` checks erasure-coded files against the real reconstruction
  threshold, not the chunk count. A file that lost shares but is still
  recoverable now reads "degraded" instead of "unrecoverable".
- Download takes the placement strategy from the manifest entry rather than
  the live peer count, so erasure-coded files stay readable after the
  cluster shrinks below five nodes.
- Manifest merges with equal Lamport timestamps pick a deterministic winner
  (tombstone first, then uploader ID) so nodes converge.
- Peers acknowledge chunk stores; `stored_on` only lists nodes that actually
  persisted the chunk, and uploads fall back to other nodes when one is full.
- Chunk retrieval responses are matched to requests by chunk ID; retrieval
  and handshakes time out instead of hanging on a silent peer.
- Frame length on the wire is capped at 64 MiB.
- Corrupt FEC shares are detected by their integrity hash and skipped during
  reconstruction.
- Chunk writes are atomic (temp file + rename); storage usage is tracked
  incrementally instead of rescanning the tree on every store.
- Manifest and artifact-store JSON writes are atomic.
- Auth token comparison is constant-time.
- Re-saving an `fc-ml` artifact with the same name and version replaces the
  record instead of duplicating it.
- `fc-rag` re-indexing no longer duplicates vector points; indexing and
  retrieval share one embedded Qdrant client.

### Added
- `Node.join()`: connect to a bootstrap peer, fan out to the peers it knows,
  and sync the catalog. Used by `start` and the one-shot commands.
- One-shot commands (`upload`, `download`, `verify`, `delete`, `sync`,
  `list`) bind an ephemeral port by default so they don't collide with a
  daemon on the same machine. `list --bootstrap` shows the network catalog.
- One-shot uploads place shares on the durable peers instead of the exiting
  client, so redundancy survives the command returning.
- Manifest and peer request/response messages backing the mesh join.
- `firecloud verify [FILE_ID]` reports each file as healthy, degraded, or
  unrecoverable; exits non-zero if anything is unrecoverable.
- `FIRECLOUD_PASSPHRASE`, `FIRECLOUD_DATA_DIR`, `FIRECLOUD_MAX_STORAGE_GB`
  and `FIRECLOUD_BOOTSTRAP` environment variables.
- `--bootstrap host:port` on `start`, `upload`, `download`, `delete`,
  `verify`, and `sync`.
- `FC_RAG_MODEL` selects the Ollama model. A query with no reachable LLM
  still prints retrieved passages and sources.
- Store/has-chunk acknowledgement messages (`STORE_OK`, `STORE_FAIL`,
  `HAS_CHUNK`).

## [0.1.0] - 2025-06-01

### Added
- XChaCha20-Poly1305 chunk encryption with HMAC-SHA-256 keyed addressing
- FastCDC content-defined chunking
- zfec erasure coding
- mDNS peer discovery via zeroconf, with a config-file fallback
- TLS binary RPC transport with handshake and heartbeat
- Manifest with Lamport timestamps and tombstones
- Watchdog-based folder sync
- Click CLI: init, start, upload, download, status, peers
- Multi-node compose setup with health checks
- GitHub Actions CI (lint, test, build)
- `fc-rag`: local RAG pipeline (fastembed + Qdrant + Ollama)
- `fc-ml`: artifact versioning, telemetry server, anomaly detection
