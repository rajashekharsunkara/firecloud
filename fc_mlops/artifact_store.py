"""Version-tracked ML artifact storage backed by FireCloud's Node API."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

_MANIFEST_PATH = Path.home() / ".fc_mlops" / "artifacts.json"


class ArtifactMetadata(BaseModel):
    """Immutable metadata record for a stored ML artifact."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    artifact_type: Literal["model", "dataset", "checkpoint"]
    saved_at: datetime
    file_size_bytes: int
    metrics: dict[str, float]
    tags: list[str]
    firecloud_file_id: str


def _load_manifest() -> list[dict]:
    if not _MANIFEST_PATH.exists():
        return []
    try:
        return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_manifest(entries: list[dict]) -> None:
    _MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so a crash mid-write cannot truncate the manifest.
    tmp_path = _MANIFEST_PATH.with_name(_MANIFEST_PATH.name + ".tmp")
    tmp_path.write_text(
        json.dumps(entries, indent=2, default=str),
        encoding="utf-8",
    )
    tmp_path.replace(_MANIFEST_PATH)


async def save_artifact(
    node,
    local_path: Path,
    name: str,
    version: str,
    artifact_type: str,
    metrics: dict[str, float] | None = None,
    tags: list[str] | None = None,
) -> ArtifactMetadata:
    """Upload *local_path* to FireCloud and record it in the artifact manifest."""
    local_path = Path(local_path)
    file_id = await node.upload(local_path)

    metadata = ArtifactMetadata(
        name=name,
        version=version,
        artifact_type=artifact_type,
        saved_at=datetime.now(timezone.utc),
        file_size_bytes=local_path.stat().st_size,
        metrics=metrics or {},
        tags=tags or [],
        firecloud_file_id=file_id,
    )

    entries = _load_manifest()
    # Re-saving the same name+version replaces the old record; otherwise
    # load_artifact would keep returning the stale first match.
    entries = [
        e for e in entries
        if not (e.get("name") == name and e.get("version") == version)
    ]
    entries.append(metadata.model_dump())
    _save_manifest(entries)
    return metadata


async def load_artifact(
    node,
    name: str,
    version: str,
    destination: Path,
) -> Path:
    """Download an artifact by name+version from the manifest."""
    entries = _load_manifest()

    match = None
    for entry in entries:
        if entry["name"] == name and entry["version"] == version:
            match = entry
            break

    if match is None:
        raise ValueError(
            f"Artifact '{name}' version '{version}' not found in manifest"
        )

    destination = Path(destination)
    await node.download(match["firecloud_file_id"], destination)
    return destination.resolve()


def list_artifacts(artifact_type: str | None = None) -> list[ArtifactMetadata]:
    """Return tracked artifacts, optionally filtered by *artifact_type*."""
    entries = _load_manifest()
    results = []
    for entry in entries:
        if artifact_type and entry.get("artifact_type") != artifact_type:
            continue
        results.append(ArtifactMetadata(**entry))
    return results
