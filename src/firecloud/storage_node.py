from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .storage_api import create_storage_api


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FireCloud storage node service")
    parser.add_argument("--node-id", required=True, help="Node identifier")
    parser.add_argument("--root-dir", required=True, help="Node storage directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    app = create_storage_api(node_id=args.node_id, root_dir=Path(args.root_dir))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

