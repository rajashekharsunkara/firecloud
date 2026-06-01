"""fc-ml CLI — artifact management, telemetry, and anomaly detection."""

from pathlib import Path

import click


@click.group()
def cli():
    """fc-ml — MLOps extensions for FireCloud."""
    pass


# --- Artifact commands ---

@cli.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--name", "-n", required=True, help="Artifact name.")
@click.option("--version", "-v", required=True, help="Artifact version.")
@click.option(
    "--type", "artifact_type",
    type=click.Choice(["model", "dataset", "checkpoint"]),
    required=True,
    help="Artifact type.",
)
@click.option(
    "--metric", "-m",
    multiple=True,
    help="Metric in key=value format (repeatable).",
)
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
def save(
    path: str,
    name: str,
    version: str,
    artifact_type: str,
    metric: tuple[str, ...],
    passphrase: str,
    port: int,
    storage: str | None,
):
    """Save an artifact to the FireCloud network."""
    import asyncio
    from firecloud import Network, Node
    from fc_mlops.artifact_store import save_artifact

    metrics = {}
    for m in metric:
        if "=" in m:
            k, v = m.split("=", 1)
            metrics[k.strip()] = float(v.strip())

    storage_path = Path(storage) if storage else Path.home() / ".firecloud" / "storage"

    async def _run():
        net = Network.load(Path.home() / ".firecloud" / "network.key", passphrase)
        node = Node(network=net, storage_path=storage_path, port=port, enable_discovery=False)
        await node.start()
        try:
            meta = await save_artifact(
                node, Path(path), name, version, artifact_type, metrics, []
            )
            click.echo(click.style("✓ Artifact saved.", fg="green"))
            click.echo(f"  Name      : {meta.name}")
            click.echo(f"  Version   : {meta.version}")
            click.echo(f"  File ID   : {meta.firecloud_file_id}")
            click.echo(f"  Size      : {meta.file_size_bytes} bytes")
        finally:
            await node.stop()

    asyncio.run(_run())


@cli.command()
@click.argument("name")
@click.option("--version", "-v", required=True, help="Artifact version.")
@click.option("--dest", "-d", required=True, type=click.Path(), help="Destination path.")
@click.option("--passphrase", prompt=True, hide_input=True, help="Network passphrase.")
@click.option("--port", default=7474, type=int)
@click.option("--storage", default=None, type=click.Path())
def load(
    name: str,
    version: str,
    dest: str,
    passphrase: str,
    port: int,
    storage: str | None,
):
    """Load an artifact from the FireCloud network."""
    import asyncio
    from firecloud import Network, Node
    from fc_mlops.artifact_store import load_artifact

    storage_path = Path(storage) if storage else Path.home() / ".firecloud" / "storage"

    async def _run():
        net = Network.load(Path.home() / ".firecloud" / "network.key", passphrase)
        node = Node(network=net, storage_path=storage_path, port=port, enable_discovery=False)
        await node.start()
        try:
            result_path = await load_artifact(node, name, version, Path(dest))
            click.echo(click.style(f"✓ Artifact downloaded to {result_path}", fg="green"))
        finally:
            await node.stop()

    asyncio.run(_run())


@cli.command("list")
@click.option(
    "--type", "artifact_type",
    type=click.Choice(["model", "dataset", "checkpoint"]),
    default=None,
    help="Filter by artifact type.",
)
def list_artifacts(artifact_type: str | None):
    """List tracked ML artifacts."""
    from fc_mlops.artifact_store import list_artifacts as _list

    artifacts = _list(artifact_type)
    if not artifacts:
        click.echo("No artifacts found.")
        return

    click.echo(
        click.style(
            f"{'Name':<20} {'Version':<10} {'Type':<12} {'Size':<12} {'File ID':<20}",
            bold=True,
        )
    )
    click.echo("─" * 75)
    for a in artifacts:
        size = f"{a.file_size_bytes:,} B"
        click.echo(
            f"{a.name:<20} {a.version:<10} {a.artifact_type:<12} "
            f"{size:<12} {a.firecloud_file_id[:20]}"
        )


# --- Telemetry ---

@cli.group()
def telemetry():
    """Telemetry server commands."""
    pass

@telemetry.command("start")
def telemetry_start():
    """Start the telemetry metrics server on localhost:7475."""
    from fc_mlops.telemetry import start_server
    click.echo(click.style("Starting telemetry server on http://127.0.0.1:7475", fg="cyan"))
    start_server()


# --- Anomaly detection ---

@cli.group()
def anomaly():
    """Anomaly detection commands."""
    pass

@anomaly.command("check")
def anomaly_check():
    """Run anomaly detection on recent telemetry data."""
    from fc_mlops.anomaly import check_anomaly

    result = check_anomaly()

    if isinstance(result, dict):
        click.echo(f"Insufficient data: {result.get('readings', 0)} readings (need ≥ 50)")
        return

    click.echo(click.style("Anomaly Detection Results", bold=True))
    click.echo(f"  Anomaly detected  : {'Yes' if result.is_anomaly else 'No'}")
    click.echo(f"  Anomaly score     : {result.anomaly_score}")
    click.echo(f"  Flagged metrics   : {', '.join(result.flagged_metrics) or 'None'}")
    click.echo(f"  Recommendation    : {result.recommendation}")


@cli.command("simulate-failure")
def simulate_failure():
    """Run the failure simulation demo."""
    from fc_mlops.simulate_failure import main
    main()


if __name__ == "__main__":
    cli()
