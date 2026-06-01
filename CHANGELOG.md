# Changelog

All notable changes to FireCloud will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
