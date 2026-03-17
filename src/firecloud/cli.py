from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .api import create_api
from .config import FECConfig, FireCloudConfig
from .controller import FireCloudController
from .tui.app import FireCloudTUI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FireCloud Python MVP")
    parser.add_argument("--root-dir", default=".firecloud", help="State directory")
    parser.add_argument("--nodes", type=int, default=5, help="Number of local simulated nodes")
    parser.add_argument("--source-symbols", type=int, default=3, help="RaptorQ source symbols (k)")
    parser.add_argument("--total-symbols", type=int, default=5, help="RaptorQ total symbols (n)")
    parser.add_argument(
        "--symbol-size", type=int, default=64 * 1024, help="RaptorQ symbol size in bytes"
    )

    sub = parser.add_subparsers(dest="command", required=True)
    upload = sub.add_parser("upload", help="Upload a file")
    upload.add_argument("path", help="File path")

    download = sub.add_parser("download", help="Download a file")
    download.add_argument("file_id", help="File id")
    download.add_argument("destination", help="Destination path")

    set_node = sub.add_parser("node", help="Set node status")
    set_node.add_argument("node_id")
    set_node.add_argument("status", choices=["online", "offline"])

    repair = sub.add_parser("repair", help="Repair a file's symbol redundancy")
    repair.add_argument("file_id")

    sub.add_parser("list-files", help="List stored files")
    sub.add_parser("list-nodes", help="List node status")
    sub.add_parser("verify-audit", help="Verify audit hash chain")

    api = sub.add_parser("run-api", help="Run FastAPI server")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8080)

    sub.add_parser("run-tui", help="Run Textual TUI")
    return parser


def _controller_from_args(args: argparse.Namespace) -> FireCloudController:
    fec = FECConfig(
        source_symbols=args.source_symbols,
        total_symbols=args.total_symbols,
        symbol_size=args.symbol_size,
    )
    config = FireCloudConfig(root_dir=Path(args.root_dir), node_count=args.nodes, fec=fec)
    return FireCloudController(config=config)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    controller = _controller_from_args(args)

    if args.command == "upload":
        file_id = controller.upload_file(Path(args.path))
        print(file_id)
        return
    if args.command == "download":
        output = controller.download_file(file_id=args.file_id, destination_path=Path(args.destination))
        print(output)
        return
    if args.command == "node":
        controller.set_node_online(args.node_id, args.status == "online")
        print(f"{args.node_id} -> {args.status}")
        return
    if args.command == "repair":
        repaired = controller.repair_file(args.file_id)
        print(repaired)
        return
    if args.command == "list-files":
        for item in controller.list_files():
            print(f"{item.file_id}\t{item.file_name}\t{item.file_size}\t{item.created_at}")
        return
    if args.command == "list-nodes":
        for node in controller.list_nodes():
            print(f"{node.node_id}\tonline={node.online}\tsymbols={node.symbol_count}")
        return
    if args.command == "verify-audit":
        valid, details = controller.verify_audit_chain()
        print(f"valid={valid} {details}")
        return
    if args.command == "run-api":
        app = create_api(controller)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return
    if args.command == "run-tui":
        app = FireCloudTUI(controller=controller)
        app.run()
        return

    raise ValueError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
