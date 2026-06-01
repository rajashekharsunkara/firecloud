"""FireCloud CLI — click-based command-line interface.

Provides commands to initialise a network, start/stop a node,
upload/download/delete files, list files and peers, connect to
peers, and sync a folder.
"""

import asyncio
import os
import signal
from pathlib import Path

import click

from firecloud.network import Network
from firecloud.exceptions import FireCloudError


# Default configuration directory
_DEFAULT_DIR = Path.home() / ".firecloud"
_KEYSTORE_FILE = "network.key"
_PID_FILE = "firecloud.pid"


def _config_dir() -> Path:
    """Return (and create) the config directory."""
    d = _DEFAULT_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_network(passphrase: str) -> Network:
    """Load the network from the default keystore."""
    keystore = _config_dir() / _KEYSTORE_FILE
    if not keystore.exists():
        raise click.ClickException(
            "Network not initialised. Run 'firecloud init' first."
        )
    try:
        return Network.load(keystore, passphrase)
    except Exception as exc:
        raise click.ClickException(f"Failed to load network: {exc}") from exc

def _human_size(num_bytes: int) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


# ========================================================================
# CLI Group
# ========================================================================


@click.group()
@click.version_option(version="0.1.0", prog_name="firecloud")
def cli():
    """FireCloud — Private, encrypted, distributed storage."""
    pass


# ========================================================================
# init
# ========================================================================


@cli.command()
@click.option("--join", "join_addr", default=None, help="Join an existing network via peer address (host:port).")
@click.option("--passphrase", prompt=True, hide_input=True, confirmation_prompt=True, help="Passphrase to protect the network key.")
def init(join_addr: str | None, passphrase: str):
    """Initialise a new FireCloud network or join an existing one."""
    cfg = _config_dir()
    keystore = cfg / _KEYSTORE_FILE

    if join_addr:
        # Join mode: connect to peer and get network key
        click.echo(f"Joining network via {join_addr}...")
        # For v1, joining requires the same passphrase — the peer shares the
        # keystore file out-of-band.  We simply create the config directory.
        click.echo(
            click.style("⚠  Copy the network.key file from an existing node to:", fg="yellow")
        )
        click.echo(f"   {keystore}")
        return

    if keystore.exists():
        click.echo(
            click.style("Network already initialised.", fg="yellow")
            + f" Keystore at {keystore}"
        )
        return

    net = Network.create(passphrase)
    net.save(keystore, passphrase)
    click.echo(click.style("✓ Network initialised.", fg="green"))
    click.echo(f"  Network ID : {net.network_id}")
    click.echo(f"  Keystore   : {keystore}")


# ========================================================================
# start
# ========================================================================


@cli.command()
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int, help="TCP port to listen on.")
@click.option("--daemon", is_flag=True, help="Run in the background (Unix only).")
@click.option("--storage", default=None, type=click.Path(), help="Storage directory.")
def start(passphrase: str, port: int, daemon: bool, storage: str | None):
    """Start the FireCloud node."""
    net = _load_network(passphrase)
    storage_path = Path(storage) if storage else _config_dir() / "storage"

    if daemon:
        _start_daemon(net, storage_path, port)
    else:
        _start_foreground(net, storage_path, port)


def _start_foreground(net: Network, storage_path: Path, port: int):
    """Run the node in the foreground."""
    from firecloud.node import Node

    async def _run():
        node = Node(network=net, storage_path=storage_path, port=port)
        await node.start()
        click.echo(click.style(f"✓ Node {node.node_id} running on port {port}", fg="green"))
        click.echo("  Press Ctrl+C to stop.")

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _signal_handler():
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass  # Windows

        await stop_event.wait()
        click.echo("\nStopping node...")
        await node.stop()
        click.echo(click.style("✓ Node stopped.", fg="green"))

    asyncio.run(_run())


def _start_daemon(net: Network, storage_path: Path, port: int):
    """Fork to background and write a PID file (Unix only)."""
    pid_file = _config_dir() / _PID_FILE

    if pid_file.exists():
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)
            click.echo(click.style(f"Node already running (PID {pid}).", fg="yellow"))
            return
        except OSError:
            pid_file.unlink(missing_ok=True)

    try:
        child_pid = os.fork()
    except AttributeError:
        raise click.ClickException("--daemon is not supported on this platform (no os.fork).")

    if child_pid > 0:
        # Parent — write PID and exit
        pid_file.write_text(str(child_pid))
        click.echo(click.style(f"✓ Node daemonised (PID {child_pid}).", fg="green"))
        return

    # Child — detach and run
    os.setsid()
    _start_foreground(net, storage_path, port)


# ========================================================================
# stop
# ========================================================================


@cli.command()
def stop():
    """Stop a running FireCloud daemon."""
    pid_file = _config_dir() / _PID_FILE
    if not pid_file.exists():
        click.echo(click.style("No running daemon found.", fg="yellow"))
        return

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        click.echo(click.style(f"✓ Sent SIGTERM to PID {pid}.", fg="green"))
    except OSError as exc:
        click.echo(click.style(f"Failed to stop PID {pid}: {exc}", fg="red"))
    finally:
        pid_file.unlink(missing_ok=True)


# ========================================================================
# status
# ========================================================================


@cli.command()
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
def status(passphrase: str, port: int, storage: str | None):
    """Show node and network status."""
    net = _load_network(passphrase)
    storage_path = Path(storage) if storage else _config_dir() / "storage"

    from firecloud.node import Node

    async def _run():
        node = Node(network=net, storage_path=storage_path, port=port,
                     enable_discovery=False)
        s = node.status()
        click.echo(click.style("FireCloud Status", fg="cyan", bold=True))
        click.echo(f"  Node ID          : {s['node_id']}")
        click.echo(f"  Network ID       : {s['network_id']}")
        click.echo(f"  Port             : {s['port']}")
        click.echo(f"  Running          : {s['running']}")
        click.echo(f"  Peers connected  : {s['peers_connected']}")
        click.echo(f"  Files stored     : {s['files_stored']}")
        click.echo(f"  Chunks stored    : {s['chunks_stored']}")
        click.echo(f"  Storage used     : {_human_size(s['storage_used'])}")
        click.echo(f"  Storage available: {_human_size(s['storage_available'])}")

    asyncio.run(_run())


# ========================================================================
# upload
# ========================================================================


@cli.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
def upload(path: str, passphrase: str, port: int, storage: str | None):
    """Upload a file to the FireCloud network."""
    net = _load_network(passphrase)
    storage_path = Path(storage) if storage else _config_dir() / "storage"

    from firecloud.node import Node

    async def _run():
        node = Node(network=net, storage_path=storage_path, port=port,
                     enable_discovery=False)
        await node.start()
        try:
            file_id = await node.upload(path)
            click.echo(click.style("✓ Uploaded successfully.", fg="green"))
            click.echo(f"  File ID: {file_id}")
        except FireCloudError as exc:
            raise click.ClickException(str(exc))
        finally:
            await node.stop()

    asyncio.run(_run())


# ========================================================================
# download
# ========================================================================


@cli.command()
@click.argument("file_id")
@click.argument("output", type=click.Path())
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
def download(file_id: str, output: str, passphrase: str, port: int, storage: str | None):
    """Download a file from the FireCloud network."""
    net = _load_network(passphrase)
    storage_path = Path(storage) if storage else _config_dir() / "storage"

    from firecloud.node import Node

    async def _run():
        node = Node(network=net, storage_path=storage_path, port=port,
                     enable_discovery=False)
        await node.start()
        try:
            await node.download(file_id, output)
            click.echo(click.style(f"✓ Downloaded to {output}", fg="green"))
        except FireCloudError as exc:
            raise click.ClickException(str(exc))
        finally:
            await node.stop()

    asyncio.run(_run())


# ========================================================================
# delete
# ========================================================================


@cli.command("delete")
@click.argument("file_id")
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
def delete_file(file_id: str, passphrase: str, port: int, storage: str | None):
    """Delete a file from the FireCloud network."""
    net = _load_network(passphrase)
    storage_path = Path(storage) if storage else _config_dir() / "storage"

    from firecloud.node import Node

    async def _run():
        node = Node(network=net, storage_path=storage_path, port=port,
                     enable_discovery=False)
        await node.start()
        try:
            await node.delete(file_id)
            click.echo(click.style(f"✓ File {file_id[:16]}... deleted.", fg="green"))
        except FireCloudError as exc:
            raise click.ClickException(str(exc))
        finally:
            await node.stop()

    asyncio.run(_run())


# ========================================================================
# list
# ========================================================================


@cli.command("list")
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
def list_files(passphrase: str, port: int, storage: str | None):
    """List all files stored in the FireCloud network."""
    net = _load_network(passphrase)
    storage_path = Path(storage) if storage else _config_dir() / "storage"

    from firecloud.node import Node

    async def _run():
        node = Node(network=net, storage_path=storage_path, port=port,
                     enable_discovery=False)
        files = node.list_files()
        if not files:
            click.echo("No files stored.")
            return

        # Table header
        click.echo(
            click.style(
                f"{'Name':<30} {'Size':>10} {'Chunks':>7} {'FEC':>5} {'Uploaded At':<26} {'File ID':<20}",
                bold=True,
            )
        )
        click.echo("─" * 105)
        for f in files:
            click.echo(
                f"{f['name']:<30} "
                f"{_human_size(f['size']):>10} "
                f"{f['chunk_count']:>7} "
                f"{'yes' if f['fec_enabled'] else 'no':>5} "
                f"{f['uploaded_at']:<26} "
                f"{f['file_id'][:20]}"
            )

    asyncio.run(_run())


# ========================================================================
# peers
# ========================================================================


@cli.command()
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
def peers(passphrase: str, port: int, storage: str | None):
    """List known peers."""
    net = _load_network(passphrase)
    storage_path = Path(storage) if storage else _config_dir() / "storage"

    from firecloud.node import Node

    async def _run():
        node = Node(network=net, storage_path=storage_path, port=port,
                     enable_discovery=False)
        peer_list = node.peers()
        if not peer_list:
            click.echo("No known peers.")
            return

        click.echo(
            click.style(f"{'Node ID':<20} {'Host':<16} {'Port':>6} {'Connected':>10}", bold=True)
        )
        click.echo("─" * 55)
        for p in peer_list:
            click.echo(
                f"{p['node_id']:<20} "
                f"{p['host']:<16} "
                f"{p['port']:>6} "
                f"{'yes' if p['connected'] else 'no':>10}"
            )

    asyncio.run(_run())


# ========================================================================
# connect
# ========================================================================


@cli.command()
@click.argument("address")
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
def connect(address: str, passphrase: str, port: int, storage: str | None):
    """Connect to a peer at ADDRESS (host:port)."""
    net = _load_network(passphrase)
    storage_path = Path(storage) if storage else _config_dir() / "storage"

    from firecloud.node import Node

    async def _run():
        node = Node(network=net, storage_path=storage_path, port=port,
                     enable_discovery=False)
        await node.start()
        try:
            await node.connect(address)
            click.echo(click.style(f"✓ Connected to {address}", fg="green"))
        except FireCloudError as exc:
            raise click.ClickException(str(exc))
        finally:
            await node.stop()

    asyncio.run(_run())


# ========================================================================
# remove-node
# ========================================================================


@cli.command("remove-node")
@click.argument("node_id")
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
def remove_node(node_id: str, passphrase: str, port: int, storage: str | None):
    """Remove a node from the network and trigger re-replication."""
    net = _load_network(passphrase)
    storage_path = Path(storage) if storage else _config_dir() / "storage"

    from firecloud.node import Node

    async def _run():
        node = Node(network=net, storage_path=storage_path, port=port,
                     enable_discovery=False)
        await node.start()
        try:
            await node.remove_node(node_id)
            click.echo(click.style(f"✓ Removed node {node_id[:16]}... and triggered re-replication", fg="green"))
        except FireCloudError as exc:
            raise click.ClickException(str(exc))
        finally:
            await node.stop()

    asyncio.run(_run())


# ========================================================================
# sync
# ========================================================================


@cli.command()
@click.argument("folder", type=click.Path())
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
@click.option("--daemon", is_flag=True, help="Run in the background (Unix only).")
def sync(folder: str, passphrase: str, port: int, storage: str | None, daemon: bool):
    """Sync a local folder with the FireCloud network."""
    net = _load_network(passphrase)
    storage_path = Path(storage) if storage else _config_dir() / "storage"

    from firecloud.node import Node
    from firecloud.sync import FolderSync

    async def _run():
        node = Node(network=net, storage_path=storage_path, port=port)
        await node.start()
        fs = FolderSync(node, Path(folder))
        await fs.start()
        click.echo(click.style(f"✓ Syncing {folder}", fg="green"))
        click.echo("  Press Ctrl+C to stop.")

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _signal_handler():
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        await stop_event.wait()
        click.echo("\nStopping sync...")
        await fs.stop()
        await node.stop()
        click.echo(click.style("✓ Sync stopped.", fg="green"))

    asyncio.run(_run())


# ========================================================================
# Entry point
# ========================================================================


if __name__ == "__main__":
    cli()
