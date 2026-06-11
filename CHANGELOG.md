# Changelog

All notable changes to FireCloud will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- **Erasure-coded files are now downloadable after the cluster shrinks.**
  Download derived the placement strategy from the *live* peer count, so a
  file uploaded with erasure coding became unreadable once fewer than 5
  nodes were online — even with enough shares reachable. The strategy and
  reconstruction threshold now come from the manifest entry.
- Manifest merges with equal Lamport timestamps now converge on a
  deterministic winner (tombstone first, then uploader node ID) instead of
  each node keeping its own version forever.
- Chunk stores on peers are now acknowledged; placement metadata
  (`stored_on`) only records nodes that actually persisted the chunk, and
  upload falls back to other nodes when a peer is full.
- Chunk retrieval responses are matched to requests by chunk ID, so a late
  reply can no longer be delivered to the wrong request; retrieval and
  handshake have timeouts instead of hanging forever on a silent peer.
- Frame length on the wire is capped (64 MiB) so a corrupt or hostile
  length prefix cannot trigger a multi-gigabyte allocation.
- Corrupt FEC shares are detected via their integrity hash and skipped
  during reconstruction instead of poisoning the decoded file.
- Chunk writes are atomic (write-temp-then-rename) and storage usage is
  tracked incrementally instead of re-walking the chunk tree on every store.
- Manifest and artifact-store JSON files are written atomically.
- Auth token comparison is constant-time.
- Re-saving an `fc-ml` artifact with the same name+version replaces the
  record instead of duplicating it.
- `fc-rag` re-indexing no longer duplicates vector points (deterministic
  point IDs + per-file cleanup), and indexing/retrieval share one embedded
  Qdrant client.

### Added
- `firecloud verify [FILE_ID]` — checks chunk availability and integrity
  across the network and reports each file as healthy / degraded /
  unrecoverable (non-zero exit on unrecoverable files).
- `FIRECLOUD_PASSPHRASE`, `FIRECLOUD_DATA_DIR`, and
  `FIRECLOUD_MAX_STORAGE_GB` environment variables are honored by the CLI
  (docker-compose already set them; the code now reads them).
- `--bootstrap host:port` option on `start`, `upload`, `download`,
  `delete`, `verify`, and `sync` to connect to a peer without mDNS.
- Peer-side store/has-chunk protocol messages (`STORE_OK`, `STORE_FAIL`,
  `HAS_CHUNK`) backing the fixes above.

## [0.1.0] - 2025-06-01

### Added
- XChaCha20-Poly1305 chunk encryption with HMAC-SHA-256 keyed addressing
- FastCDC content-defined chunking
- zfec erasure coding (configurable k/n)
- mDNS peer discovery via zeroconf with config file fallback
- TLS binary RPC transport with handshake and heartbeat
- Manifest with Lamport timestamps and tombstone support
- Watchdog-based folder sync (outbound upload, inbound download)
- Click CLI (`firecloud` entry point) — init, start, upload, download, status, peers
- Docker Compose multi-node setup with health checks
- GitHub Actions CI (lint → test → build)
- `fc-rag`: local RAG pipeline (fastembed + Qdrant + Ollama)
- `fc-ml`: ML artifact versioning, telemetry server, anomaly detection
