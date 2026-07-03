![CI](https://github.com/rajashekharsunkara/firecloud/actions/workflows/ci.yml/badge.svg)

# FireCloud

Private, encrypted, distributed storage across machines you own.

Files are chunked and encrypted on the client before anything touches the
network, so storage nodes only ever hold ciphertext. With five or more nodes
a file is erasure coded and survives losing a third of the cluster. S3 means
renting someone else's disks, Syncthing has no erasure coding, and IPFS
announces content on a public DHT. FireCloud is for a LAN of machines you
already run yourself.

## Install

```bash
pip install git+https://github.com/rajashekharsunkara/firecloud.git

# optional extras
pip install "firecloud-devnet[rag]"      # local RAG pipeline
pip install "firecloud-devnet[mlops]"    # ML artifact store
```

## Quickstart with containers

Works with podman (rootless is fine) or docker; swap the command name.

```bash
git clone https://github.com/rajashekharsunkara/firecloud.git
cd firecloud
cp .env.example .env                # set FIRECLOUD_PASSPHRASE in .env

# create the network keystore the containers share
podman build -t firecloud .
mkdir -p test_config/.firecloud
podman run --rm --env-file .env \
  -v "$PWD/test_config/.firecloud:/root/.firecloud:z" firecloud init

# bootstrap node + 3 storage nodes
podman compose build
podman compose up -d
```

Note for podman: `podman compose up` reuses service images if they already
exist, so run `podman compose build` after pulling changes.

Upload from one node, download from another:

```bash
podman exec firecloud-bootstrap sh -c 'echo "hello cluster" > /data/hello.txt'
podman exec firecloud-bootstrap firecloud upload /data/hello.txt \
  --bootstrap 127.0.0.1:7474 --storage /tmp/cli

podman exec firecloud-node-1 firecloud download <file_id> /tmp/hello.txt \
  --bootstrap 127.0.0.1:7475 --storage /tmp/cli

podman exec firecloud-bootstrap firecloud verify \
  --bootstrap 127.0.0.1:7474 --storage /tmp/cli
```

`--storage /tmp/cli` gives the one-shot command its own scratch directory so
it doesn't share state with the daemon in the same container. Stop a
container (`podman stop firecloud-node-3`) and the download still works;
`verify` reports the file as degraded until the node returns.

## Run it on real machines

Install the package on each machine. The network key *is* the network:
nodes holding the same key can talk, everyone else fails the handshake.

```bash
# machine A
firecloud init                        # writes ~/.firecloud/network.key
firecloud start --port 7474

# every other machine: copy the key over first
scp userA@machine-a:~/.firecloud/network.key ~/.firecloud/
firecloud start --port 7474 --bootstrap <machine-a-ip>:7474
```

One `--bootstrap` peer is enough; a joining node asks it for the rest of the
cluster, connects to them, and pulls the file catalog. Nodes listen on all
interfaces, so the only thing to check is the firewall on each machine
(`sudo firewall-cmd --add-port=7474/tcp` on Fedora, `sudo ufw allow 7474/tcp`
on Ubuntu).

Then from any machine on the network:

```bash
firecloud upload ./photos.zip      --bootstrap <machine-a-ip>:7474
firecloud list                     --bootstrap <machine-a-ip>:7474
firecloud download <file_id> ./out --bootstrap <machine-a-ip>:7474
firecloud verify                   --bootstrap <machine-a-ip>:7474
```

To try multiple nodes on a single machine, give each its own port and
storage directory:

```bash
export FIRECLOUD_PASSPHRASE=test-pass
firecloud init
firecloud start --port 7474 --storage /tmp/fc/n0 &
for i in 1 2 3 4; do
  firecloud start --port $((7474 + i)) --storage /tmp/fc/n$i \
    --bootstrap 127.0.0.1:7474 &
done
```

### CLI reference

```bash
firecloud init                      # create a network keystore
firecloud start                     # run a node
firecloud upload <path>             # chunk, encrypt, distribute
firecloud download <id> <out>       # retrieve, verify, reassemble
firecloud verify [<id>]             # healthy / degraded / unrecoverable
firecloud list                      # file catalog across the network
firecloud sync <folder>             # two-way folder sync
firecloud status / peers / delete <id>
```

Environment variables, all optional: `FIRECLOUD_PASSPHRASE` (skip the
prompt), `FIRECLOUD_DATA_DIR` (storage directory), `FIRECLOUD_MAX_STORAGE_GB`
(chunk store quota), `FIRECLOUD_BOOTSTRAP` (comma-separated peers to join on
start), `FC_RAG_MODEL` (Ollama model for the RAG layer).

## Architecture

```
┌─────────────────────────────────────────┐
│  fc-rag (private RAG, opt-in)           │
│  fc-mlops (artifact store, opt-in)      │
│  containers + GitHub Actions CI         │
│  core: storage, crypto, P2P transport   │
└─────────────────────────────────────────┘
```

The core does XChaCha20-Poly1305 chunk encryption, FastCDC content-defined
chunking, zfec erasure coding, and mDNS peer discovery. Manifests use
Lamport timestamps with last-writer-wins merges. Nodes talk a small binary
RPC protocol over TLS.

`fc-mlops` stores version-tracked ML artifacts through the `Node` API and
ships a FastAPI telemetry endpoint with IsolationForest anomaly detection.
`fc-rag` indexes documents with fastembed, searches them in an embedded
Qdrant, and generates answers with a local Ollama model.

## Security

Chunk IDs are HMAC-SHA-256 under a key derived from the network key rather
than a plain content hash. Someone who suspects you store a particular file
can't confirm it by hashing the plaintext themselves, because valid chunk
addresses require the network key.

Chunks are encrypted before they leave the client, so storage nodes hold
authenticated ciphertext only. The transport caps frame sizes, times out
handshakes and requests, and compares auth tokens in constant time.

Devnet limitations to know about: node TLS certificates are self-signed and
clients skip verification, so the TLS layer stops passive snooping but not
an active man-in-the-middle on your LAN. The chunk payloads stay end-to-end
encrypted either way. Run this on networks you control.

## Private RAG (`fc-rag`)

Index documents and ask questions against a local model. Embeddings
(fastembed), vector search (embedded Qdrant), and generation (Ollama) all
run on your machine.

```bash
pip install "firecloud-devnet[rag]"
ollama pull llama3.2:3b             # any local model; set FC_RAG_MODEL to change
fc-rag index ./docs
fc-rag query "How does FireCloud handle node departure?"
```

If Ollama isn't running, the query still prints the retrieved passages and
sources; only the generated answer is skipped.

## MLOps artifact store (`fc-ml`)

```bash
pip install "firecloud-devnet[mlops]"
fc-ml save ./model.pt --name resnet --version 1.0.0 --type model --metric accuracy=0.94
fc-ml simulate-failure
```

## Development

```bash
git clone https://github.com/rajashekharsunkara/firecloud.git
cd firecloud
pip install -e ".[dev]"
pytest tests/ -v
ruff check firecloud fc_rag fc_mlops
```

## License

MIT, see [LICENSE](LICENSE).
