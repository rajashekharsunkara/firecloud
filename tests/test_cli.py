from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run_cli(project_root: Path, state_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")
    cmd = [
        sys.executable,
        "-m",
        "firecloud.cli",
        "--root-dir",
        str(state_dir),
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def test_cli_upload_requires_network_storage_peers(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    state = tmp_path / "state"
    source = tmp_path / "input.txt"
    source.write_text("cli-flow")

    upload = _run_cli(project_root, state, "upload", str(source))
    assert upload.returncode != 0
    assert "Insufficient network storage peers with capacity" in upload.stderr


def test_cli_unknown_node_returns_failure(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    state = tmp_path / "state"
    result = _run_cli(project_root, state, "node", "node-999", "offline")
    assert result.returncode != 0
    assert "Unknown node" in result.stderr


def test_cli_invalid_config_fails(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root / "src")
    cmd = [
        sys.executable,
        "-m",
        "firecloud.cli",
        "--root-dir",
        str(tmp_path / "state"),
        "list-nodes",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr


def test_cli_node_add_and_remove(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    state = tmp_path / "state"
    node_root = tmp_path / "node-extra"
    node_root.mkdir(parents=True, exist_ok=True)

    add = _run_cli(project_root, state, "node-add", "node-extra", str(node_root), "--kind", "http")
    assert add.returncode == 0, add.stderr

    listing = _run_cli(project_root, state, "list-nodes")
    assert listing.returncode == 0, listing.stderr
    assert "node-extra" in listing.stdout

    remove = _run_cli(project_root, state, "node-remove", "node-extra")
    assert remove.returncode == 0, remove.stderr
