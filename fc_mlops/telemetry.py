"""FastAPI metrics endpoint with psutil system monitoring."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
from fastapi import FastAPI
from pydantic import BaseModel

_LOG_PATH = Path.home() / ".fc_mlops" / "telemetry_log.jsonl"

app = FastAPI(title="FireCloud Telemetry", version="0.2.1")


class NodeMetrics(BaseModel):
    """Snapshot of system and node health metrics."""
    node_id: str
    timestamp: datetime
    disk_io_read_mbps: float
    disk_io_write_mbps: float
    chunk_upload_latency_ms: float
    active_connections: int
    storage_used_percent: float
    cpu_percent: float
    memory_percent: float


def _collect_metrics() -> NodeMetrics:
    # sample disk I/O over a short window
    disk1 = psutil.disk_io_counters()
    if disk1:
        time.sleep(0.1)
        disk2 = psutil.disk_io_counters()
        read_mbps = (disk2.read_bytes - disk1.read_bytes) / 0.1 / (1024 * 1024)
        write_mbps = (disk2.write_bytes - disk1.write_bytes) / 0.1 / (1024 * 1024)
    else:
        read_mbps = write_mbps = 0.0

    disk_usage = psutil.disk_usage("/")

    return NodeMetrics(
        node_id="local",
        timestamp=datetime.now(timezone.utc),
        disk_io_read_mbps=round(read_mbps, 2),
        disk_io_write_mbps=round(write_mbps, 2),
        chunk_upload_latency_ms=0.0,
        active_connections=0,
        storage_used_percent=round(disk_usage.percent, 2),
        cpu_percent=round(psutil.cpu_percent(interval=None), 2),
        memory_percent=round(psutil.virtual_memory().percent, 2),
    )


@app.get("/metrics", response_model=NodeMetrics)
def get_metrics() -> NodeMetrics:
    """Collect and return current system metrics."""
    metrics = _collect_metrics()

    # append to JSONL log
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(metrics.model_dump(), default=str) + "\n")

    return metrics


def start_server() -> None:
    """Start the telemetry server on localhost:7475."""
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7475)
