"""Tests for firecloud.cli — click CLI commands."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from firecloud.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_config_dir(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    with patch("firecloud.cli._DEFAULT_DIR", d):
        yield d


# ---------------------------------------------------------------------------
# --help and --version
# ---------------------------------------------------------------------------


class TestHelpAndVersion:

    def test_main_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "FireCloud" in result.output

    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_init_help(self, runner):
        result = runner.invoke(cli, ["init", "--help"])
        assert result.exit_code == 0
        assert "passphrase" in result.output.lower()

    def test_start_help(self, runner):
        result = runner.invoke(cli, ["start", "--help"])
        assert result.exit_code == 0
        assert "daemon" in result.output.lower()

    def test_stop_help(self, runner):
        result = runner.invoke(cli, ["stop", "--help"])
        assert result.exit_code == 0

    def test_status_help(self, runner):
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0

    def test_upload_help(self, runner):
        result = runner.invoke(cli, ["upload", "--help"])
        assert result.exit_code == 0

    def test_download_help(self, runner):
        result = runner.invoke(cli, ["download", "--help"])
        assert result.exit_code == 0

    def test_delete_help(self, runner):
        result = runner.invoke(cli, ["delete", "--help"])
        assert result.exit_code == 0

    def test_list_help(self, runner):
        result = runner.invoke(cli, ["list", "--help"])
        assert result.exit_code == 0

    def test_peers_help(self, runner):
        result = runner.invoke(cli, ["peers", "--help"])
        assert result.exit_code == 0

    def test_connect_help(self, runner):
        result = runner.invoke(cli, ["connect", "--help"])
        assert result.exit_code == 0

    def test_sync_help(self, runner):
        result = runner.invoke(cli, ["sync", "--help"])
        assert result.exit_code == 0

    def test_remove_node_help(self, runner):
        result = runner.invoke(cli, ["remove-node", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# init command
# ---------------------------------------------------------------------------


class TestInit:

    def test_init_creates_keystore(self, runner, mock_config_dir):
        result = runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        assert result.exit_code == 0
        assert "initialised" in result.output.lower() or "✓" in result.output
        keystore = mock_config_dir / "network.key"
        assert keystore.exists()

    def test_init_already_exists(self, runner, mock_config_dir):
        # First init
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        # Second init
        result = runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        assert result.exit_code == 0
        assert "already" in result.output.lower()

    def test_init_join_mode(self, runner, mock_config_dir):
        result = runner.invoke(
            cli, ["init", "--join", "192.168.1.5:7474"],
            input="testpass\ntestpass\n"
        )
        assert result.exit_code == 0
        assert "copy" in result.output.lower() or "joining" in result.output.lower()


# ---------------------------------------------------------------------------
# stop command (no daemon running)
# ---------------------------------------------------------------------------


class TestStop:

    def test_stop_no_daemon(self, runner, mock_config_dir):
        result = runner.invoke(cli, ["stop"])
        assert result.exit_code == 0
        assert "no running" in result.output.lower()


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


class TestStatus:

    def test_status_without_init_fails(self, runner, mock_config_dir):
        result = runner.invoke(cli, ["status"], input="wrongpass\n")
        assert result.exit_code != 0
        combined = (result.output + (result.stderr or "")).lower()
        assert "not initialised" in combined or "error" in combined

    def test_status_with_init(self, runner, mock_config_dir):
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        storage = str(mock_config_dir / "storage")
        result = runner.invoke(
            cli,
            ["status", "--port", "0", "--storage", storage],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "node id" in result.output.lower()


# ---------------------------------------------------------------------------
# list command (empty)
# ---------------------------------------------------------------------------


class TestList:

    def test_list_empty(self, runner, mock_config_dir):
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        storage = str(mock_config_dir / "storage")
        result = runner.invoke(
            cli,
            ["list", "--port", "0", "--storage", storage],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "no files" in result.output.lower()


# ---------------------------------------------------------------------------
# peers command (empty)
# ---------------------------------------------------------------------------


class TestPeers:

    def test_peers_empty(self, runner, mock_config_dir):
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        storage = str(mock_config_dir / "storage")
        result = runner.invoke(
            cli,
            ["peers", "--port", "0", "--storage", storage],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "no known peers" in result.output.lower()


# ---------------------------------------------------------------------------
# upload / download / delete round-trip via CLI
# ---------------------------------------------------------------------------


class TestUploadDownloadDelete:

    def test_upload_download_roundtrip(self, runner, mock_config_dir, tmp_path):
        # Init
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        storage = str(mock_config_dir / "storage")

        # Create test file
        src = tmp_path / "testfile.bin"
        content = os.urandom(5000)
        src.write_bytes(content)

        # Upload
        result = runner.invoke(
            cli,
            ["upload", str(src), "--port", "0", "--storage", storage],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "uploaded" in result.output.lower() or "✓" in result.output

        # Extract file_id from output
        for line in result.output.splitlines():
            if "file id" in line.lower():
                file_id = line.split(":")[-1].strip()
                break
        else:
            pytest.fail("Could not find file_id in upload output")

        # Download
        dest = tmp_path / "downloaded.bin"
        result = runner.invoke(
            cli,
            ["download", file_id, str(dest), "--port", "0", "--storage", storage],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert dest.read_bytes() == content

        # List should show one file
        result = runner.invoke(
            cli,
            ["list", "--port", "0", "--storage", storage],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "testfile.bin" in result.output

        # Delete
        result = runner.invoke(
            cli,
            ["delete", file_id, "--port", "0", "--storage", storage],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "deleted" in result.output.lower() or "✓" in result.output

        # List should be empty now
        result = runner.invoke(
            cli,
            ["list", "--port", "0", "--storage", storage],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "no files" in result.output.lower()


# ---------------------------------------------------------------------------
# Additional command tests & error path coverage
# ---------------------------------------------------------------------------


class TestAdditionalCommands:

    def test_status_with_wrong_passphrase(self, runner, mock_config_dir):
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        storage = str(mock_config_dir / "storage")
        result = runner.invoke(
            cli,
            ["status", "--port", "0", "--storage", storage],
            input="wrongpass\n",
        )
        assert result.exit_code != 0
        combined = (result.output + (result.stderr or "")).lower()
        assert "failed to load network" in combined or "error" in combined

    @patch("firecloud.cli._start_foreground")
    def test_start_foreground_mock(self, mock_start_fg, runner, mock_config_dir):
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        result = runner.invoke(
            cli,
            ["start", "--port", "7474", "--storage", str(mock_config_dir / "storage")],
            input="testpass\n",
        )
        assert result.exit_code == 0
        mock_start_fg.assert_called_once()

    @patch("firecloud.cli._start_daemon")
    def test_start_daemon_mock(self, mock_start_daemon, runner, mock_config_dir):
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        result = runner.invoke(
            cli,
            ["start", "--port", "7474", "--daemon", "--storage", str(mock_config_dir / "storage")],
            input="testpass\n",
        )
        assert result.exit_code == 0
        mock_start_daemon.assert_called_once()

    @patch("firecloud.node.Node.start")
    @patch("firecloud.node.Node.stop")
    @patch("asyncio.Event.wait")
    def test_start_foreground_logic(self, mock_wait, mock_node_stop, mock_node_start, runner, mock_config_dir):
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        result = runner.invoke(
            cli,
            ["start", "--port", "7474", "--storage", str(mock_config_dir / "storage")],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "running on port 7474" in result.output.lower() or "✓" in result.output
        mock_node_start.assert_called_once()
        mock_node_stop.assert_called_once()

    @patch("os.kill")
    def test_stop_daemon_running(self, mock_kill, runner, mock_config_dir):
        pid_file = mock_config_dir / "firecloud.pid"
        pid_file.write_text("12345")
        result = runner.invoke(cli, ["stop"])
        assert result.exit_code == 0
        assert "sent sigterm" in result.output.lower() or "✓" in result.output
        mock_kill.assert_called_once()
        assert not pid_file.exists()

    @patch("firecloud.node.Node.start")
    @patch("firecloud.node.Node.connect")
    @patch("firecloud.node.Node.stop")
    def test_connect_command(self, mock_stop, mock_connect, mock_start, runner, mock_config_dir):
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        result = runner.invoke(
            cli,
            ["connect", "127.0.0.1:8000", "--port", "0", "--storage", str(mock_config_dir / "storage")],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "connected to 127.0.0.1:8000" in result.output.lower() or "✓" in result.output
        mock_connect.assert_called_once_with("127.0.0.1:8000")

    @patch("firecloud.node.Node.start")
    @patch("firecloud.node.Node.stop")
    @patch("firecloud.sync.FolderSync.start")
    @patch("firecloud.sync.FolderSync.stop")
    @patch("asyncio.Event.wait")
    def test_sync_command_logic(self, mock_wait, mock_sync_stop, mock_sync_start, mock_node_stop, mock_node_start, runner, mock_config_dir, tmp_path):
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        sync_folder = tmp_path / "sync_folder"
        sync_folder.mkdir()
        result = runner.invoke(
            cli,
            ["sync", str(sync_folder), "--port", "0", "--storage", str(mock_config_dir / "storage")],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "syncing" in result.output.lower() or "✓" in result.output
        mock_node_start.assert_called_once()
        mock_sync_start.assert_called_once()
        mock_sync_stop.assert_called_once()
        mock_node_stop.assert_called_once()

    @patch("firecloud.node.Node.start")
    @patch("firecloud.node.Node.remove_node")
    @patch("firecloud.node.Node.stop")
    def test_remove_node_command(self, mock_stop, mock_remove_node, mock_start, runner, mock_config_dir):
        runner.invoke(cli, ["init"], input="testpass\ntestpass\n")
        result = runner.invoke(
            cli,
            ["remove-node", "dummy-node-id", "--port", "0", "--storage", str(mock_config_dir / "storage")],
            input="testpass\n",
        )
        assert result.exit_code == 0
        assert "removed node dummy-node-id" in result.output.lower() or "✓" in result.output
        mock_remove_node.assert_called_once_with("dummy-node-id")
