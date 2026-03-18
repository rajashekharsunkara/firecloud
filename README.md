# firecloud

Python MVP of a local-cluster distributed storage system with:

- XChaCha20-Poly1305 encryption
- RaptorQ forward error correction
- tamper-evident hash-chained audit log
- FastAPI backend + Textual TUI

## Quickstart

```bash
./scripts/bootstrap.sh
.venv/bin/firecloud --help
```

## Run tests

```bash
./scripts/test.sh
```

## Run services

```bash
# API
./scripts/run-api.sh --host 127.0.0.1 --port 8080

# Storage node API
./scripts/run-storage-node.sh --node-id node-1 --node-root-dir .firecloud/nodes/node-1 --port 8091

# TUI
./scripts/run-tui.sh

# Audit verification
./scripts/verify-audit.sh
```

## CI

GitHub Actions CI runs on push/PR to `main` and executes:

- dependency install (`pip install -e ".[dev]"`)
- full test suite (`pytest -q`)

## Production-track next steps

Current codebase is a hardened MVP. To align with the full original production vision, next milestones are:

1. Separate controller and storage nodes into independent networked services (not in-process simulation).
2. Add authenticated multi-user identity and key-management lifecycle.
3. Add durable background job queue for repair/rebalance.
4. Add observability stack (structured logs, metrics, health probes, traces).
5. Add distributed control-plane components (membership, routing, replication policy).
6. Add security hardening (rate limits, admission controls, audit retention policy, backup/restore drills).
7. Add staged deployment and migration tooling for safe upgrades.

Detailed phased backlog with file-level implementation tasks:
- [TODO.md](TODO.md)
```
