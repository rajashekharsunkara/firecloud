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


def test_cli_upload_download_verify_flow(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    state = tmp_path / "state"
    source = tmp_path / "input.txt"
    source.write_text("cli-flow")

    upload = _run_cli(project_root, state, "upload", str(source))
    assert upload.returncode == 0, upload.stderr
    file_id = upload.stdout.strip()
    assert file_id

    listing = _run_cli(project_root, state, "list-files")
    assert listing.returncode == 0, listing.stderr
    assert file_id in listing.stdout

    output = tmp_path / "output.txt"
    download = _run_cli(project_root, state, "download", file_id, str(output))
    assert download.returncode == 0, download.stderr
    assert output.read_text() == "cli-flow"

    verify = _run_cli(project_root, state, "verify-audit")
    assert verify.returncode == 0, verify.stderr
    assert "valid=True" in verify.stdout


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
        "--nodes",
        "2",
        "list-nodes",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert "node_count must be >= total_symbols" in result.stderr
