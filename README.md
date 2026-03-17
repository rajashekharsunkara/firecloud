# firecloud

Python MVP of a local-cluster distributed storage system with:

- XChaCha20-Poly1305 encryption
- RaptorQ forward error correction
- tamper-evident hash-chained audit log
- FastAPI backend + Textual TUI

## Quickstart

```bash
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/firecloud --help
```

## Run tests

```bash
.venv/bin/pytest -q
```
