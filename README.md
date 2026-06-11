![CI](https://github.com/rajashekharsunkara/firecloud/actions/workflows/ci.yml/badge.svg)

# FireCloud

Private, encrypted, distributed storage across machines you own.

Unlike S3 (vendor lock-in), Syncthing (no erasure coding), or IPFS (public DHT), FireCloud gives you zero-knowledge peer-to-peer storage where data is encrypted locally before it leaves your machine. Every chunk stored on the network is ciphertext — nodes can't read it.

---

## Install

```bash
# from GitHub (recommended for now)
pip install git+https://github.com/rajashekharsunkara/firecloud.git

# with RAG extensions
pip install "firecloud-devnet[rag]"

# with MLOps extensions
pip install "firecloud-devnet[mlops]"
```

## Quickstart

```bash
# 1. Start a 4-node network via Docker Compose
git clone https://github.com/rajashekharsunkara/firecloud.git
cd firecloud
cp .env.example .env          # set FIRECLOUD_PASSPHRASE in .env
docker compose up -d           # starts bootstrap + 3 storage nodes

# 2. Upload a file
docker exec firecloud-bootstrap firecloud upload /data/my-file.zip

# 3. Download from any node
docker exec firecloud-node-1 firecloud download <file_id> /data/restored.zip

# 4. Check replica/share health across the network
docker exec firecloud-bootstrap firecloud verify
```

### CLI essentials

```bash
firecloud init                          # create a network keystore
firecloud start --bootstrap host:7474   # run a node, optionally joining a peer
firecloud upload <path>                 # chunk → encrypt → distribute
firecloud download <file_id> <output>   # retrieve → verify → reassemble
firecloud verify [file_id]              # health report: healthy / degraded / unrecoverable
firecloud sync <folder>                 # bi-directional folder sync
```

Environment variables (all optional): `FIRECLOUD_PASSPHRASE` (skip the
passphrase prompt), `FIRECLOUD_DATA_DIR` (default storage directory),
`FIRECLOUD_MAX_STORAGE_GB` (chunk-store quota), and `FIRECLOUD_BOOTSTRAP`
(comma-separated peers to connect to on start).

---

## Architecture

```
┌─────────────────────────────────────────┐
│  fc-rag (Private RAG — opt-in)          │  LLMOps
│  fc-mlops (Artifact Store — opt-in)     │  MLOps
│  Docker + GitHub Actions                │  DevOps
│  FireCloud Core (storage, crypto, P2P)  │  Distributed Systems
└─────────────────────────────────────────┘
```

**Distributed Systems** — XChaCha20-Poly1305 encryption, FastCDC content-defined chunking, zfec erasure coding, mDNS peer discovery. Manifest consistency uses Lamport timestamps with last-writer-wins semantics. Node communication runs over TLS-protected binary RPC.

**DevOps** — Multi-node Docker Compose setup with health checks. GitHub Actions CI pipeline (lint → test → build) gates every merge.

**MLOps** — `fc-mlops` provides version-tracked ML artifact storage via FireCloud's `Node` API, a FastAPI telemetry endpoint with psutil system metrics, and IsolationForest-based anomaly detection on telemetry readings.

**LLMOps** — `fc-rag` is a fully local RAG pipeline using fastembed for embeddings, Qdrant (embedded mode) for vector search, and Ollama for local LLM inference — no text ever leaves your machine.

---

## Security

FireCloud uses **HMAC-SHA-256 with a network-derived key** for chunk addressing instead of plain SHA-256. This raises the cost of confirmation-of-file attacks — an attacker who suspects a specific file is stored cannot verify its presence by computing chunk hashes from the plaintext, because valid chunk IDs require the network key. This protection holds as long as the network key remains confidential.

Chunks are encrypted with XChaCha20-Poly1305 before leaving the machine, so storage nodes only ever see authenticated ciphertext. Transport hardening includes a frame-size cap, handshake and request timeouts, and constant-time auth-token comparison.

**Known limitations (devnet):** node TLS certificates are self-signed and clients do not verify them, so the TLS layer protects against passive snooping but not an active man-in-the-middle on your LAN; the chunk payloads themselves remain end-to-end encrypted regardless. Deploy only on networks you control.

---

## AI/ML Extensions

FireCloud stores and retrieves encrypted content. The RAG and artifact layers run entirely on the client — nothing in plaintext crosses the server boundary.

### Private RAG (`fc-rag`)

Index your docs locally and query with a private LLM — no data leaves your machine.

```bash
pip install "firecloud-devnet[rag]"
fc-rag index ./docs
fc-rag query "How does FireCloud handle node departure?"
```

### MLOps Artifact Store (`fc-mlops`)

Version-track ML models, datasets, and checkpoints using FireCloud as the storage backend.

```bash
pip install "firecloud-devnet[mlops]"
fc-ml save ./model.pt --name resnet --version 1.0.0 --type model --metric accuracy=0.94
fc-ml simulate-failure
```

---

## Development

```bash
git clone https://github.com/rajashekharsunkara/firecloud.git
cd firecloud
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).
