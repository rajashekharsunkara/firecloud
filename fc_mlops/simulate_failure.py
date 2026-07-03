"""Standalone failure simulation demo.

Generates synthetic telemetry, injects anomalies, and runs detection.
Run via: python -m fc_mlops.simulate_failure
"""

import json
import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from fc_mlops.anomaly import check_anomaly

console = Console()


def _write_reading(log_path: Path, reading: dict) -> None:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(reading, default=str) + "\n")


def _normal_reading() -> dict:
    return {
        "node_id": "sim-node",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "disk_io_read_mbps": round(random.uniform(50, 150), 2),
        "disk_io_write_mbps": round(random.uniform(30, 100), 2),
        "chunk_upload_latency_ms": round(random.uniform(20, 50), 2),
        "active_connections": random.randint(1, 5),
        "storage_used_percent": round(random.uniform(30, 60), 2),
        "cpu_percent": round(random.uniform(10, 30), 2),
        "memory_percent": round(random.uniform(40, 60), 2),
    }


def _anomalous_reading() -> dict:
    return {
        "node_id": "sim-node",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "disk_io_read_mbps": round(random.uniform(5, 15), 2),
        "disk_io_write_mbps": round(random.uniform(1, 5), 2),
        "chunk_upload_latency_ms": round(random.uniform(400, 600), 2),
        "active_connections": random.randint(0, 1),
        "storage_used_percent": round(random.uniform(85, 98), 2),
        "cpu_percent": round(random.uniform(85, 95), 2),
        "memory_percent": round(random.uniform(80, 95), 2),
    }


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="fc_mlops_sim_"))
    log_path = tmp_dir / "telemetry_log.jsonl"

    # baseline
    console.print("[bold cyan][Phase 1][/bold cyan] Generating 60 baseline readings...")
    for _ in range(60):
        _write_reading(log_path, _normal_reading())

    # inject failures
    console.print("[bold yellow][Phase 2][/bold yellow] Injecting failure signatures...")
    for _ in range(3):
        _write_reading(log_path, _anomalous_reading())

    # detect
    console.print("[bold magenta][Phase 3][/bold magenta] Running anomaly detection...")
    result = check_anomaly(log_path=log_path)

    if isinstance(result, dict):
        console.print(f"[red]Insufficient data: {result}[/red]")
        console.print("[bold red]✗ FAIL: Not enough readings for detection[/bold red]")
        return

    table = Table(title="Anomaly Detection Results", show_header=True)
    table.add_column("Metric", style="cyan", width=25)
    table.add_column("Value", style="white", width=50)

    table.add_row("Anomaly detected", "[red]Yes[/red]" if result.is_anomaly else "[green]No[/green]")
    table.add_row("Anomaly score", str(round(result.anomaly_score, 4)))
    table.add_row("Flagged metrics", ", ".join(result.flagged_metrics) if result.flagged_metrics else "None")
    table.add_row("Recommendation", result.recommendation)

    console.print()
    console.print(table)
    console.print()

    if result.is_anomaly:
        console.print("[bold green]✓ PASS: Anomaly correctly detected[/bold green]")
    else:
        console.print(
            "[bold red]✗ FAIL: Anomaly not detected, "
            "check the contamination parameter[/bold red]"
        )


if __name__ == "__main__":
    main()
