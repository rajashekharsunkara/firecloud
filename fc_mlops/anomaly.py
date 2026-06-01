"""IsolationForest-based anomaly scoring on telemetry readings."""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict
from sklearn.ensemble import IsolationForest

_LOG_PATH = Path.home() / ".fc_mlops" / "telemetry_log.jsonl"
_ALERTS_PATH = Path.home() / ".fc_mlops" / "alerts.jsonl"

# columns pulled from each telemetry reading
_FEATURES = [
    "disk_io_read_mbps",
    "chunk_upload_latency_ms",
    "cpu_percent",
    "memory_percent",
]


class AnomalyReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    is_anomaly: bool
    anomaly_score: float
    flagged_metrics: list[str]
    recommendation: str


def _append_alert(report: AnomalyReport) -> None:
    _ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_ALERTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(report.model_dump(), default=str) + "\n")


def _load_readings(log_path: Path | None = None, max_lines: int = 200) -> list[dict]:
    path = log_path or _LOG_PATH
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    tail = lines[-max_lines:] if len(lines) > max_lines else lines

    readings = []
    for line in tail:
        try:
            readings.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return readings


def check_anomaly(log_path: Path | None = None) -> AnomalyReport | dict:
    """Run anomaly detection against the latest telemetry data.

    Pass *log_path* to override the default telemetry log location
    (used by the simulation script and tests).
    """
    readings = _load_readings(log_path)

    if len(readings) < 50:
        return {"status": "insufficient_data", "readings": len(readings)}

    # build feature matrix
    data = []
    for r in readings:
        row = [float(r.get(f, 0.0)) for f in _FEATURES]
        data.append(row)
    X = np.array(data)

    clf = IsolationForest(contamination=0.05, random_state=42)
    clf.fit(X)

    latest = X[-1].reshape(1, -1)
    prediction = clf.predict(latest)[0]   # -1 = anomaly, 1 = normal
    score = clf.decision_function(latest)[0]
    is_anomaly = prediction == -1

    # flag anything > 2 stddev from mean
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    flagged: list[str] = []
    for i, feat in enumerate(_FEATURES):
        if stds[i] > 0 and abs(X[-1, i] - means[i]) > 2 * stds[i]:
            flagged.append(feat)

    if not is_anomaly:
        rec = "Node healthy"
    elif "chunk_upload_latency_ms" in flagged:
        rec = "High latency — check network"
    elif "cpu_percent" in flagged:
        rec = "CPU spike — check running processes"
    elif "disk_io_read_mbps" in flagged:
        rec = "Disk I/O degraded — check storage health"
    else:
        rec = "Anomalous reading — investigate node"

    report = AnomalyReport(
        timestamp=datetime.now(timezone.utc),
        is_anomaly=is_anomaly,
        anomaly_score=round(float(score), 4),
        flagged_metrics=flagged,
        recommendation=rec,
    )

    if is_anomaly:
        _append_alert(report)

    return report
