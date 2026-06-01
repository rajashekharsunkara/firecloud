"""Index local files into a Qdrant vector store for RAG retrieval."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from rich.progress import Progress

from fc_rag.config import get_settings
from fc_rag.embedder import chunk_text, embed_chunks

_SUPPORTED_EXTENSIONS = {".txt", ".md", ".py", ".json"}
_VECTOR_DIM = 384  # BAAI/bge-small-en-v1.5


def _ensure_collection(client: QdrantClient, name: str) -> None:
    """Create the collection if it doesn't exist yet."""
    existing = [c.name for c in client.get_collections().collections]
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=_VECTOR_DIM, distance=Distance.COSINE),
        )


def _safety_check(path: Path) -> None:
    # block indexing of firecloud's encrypted chunk storage — those are
    # ciphertext and would just pollute the vector store with garbage
    resolved = path.resolve()
    parts = resolved.parts

    for i, part in enumerate(parts):
        if part == "chunks" and i > 0 and "firecloud" in parts[i - 1].lower():
            raise ValueError(
                "Do not index encrypted chunk storage. "
                "Pass your original files before encryption."
            )

    resolved_str = str(resolved).lower()
    if "/firecloud/" in resolved_str and "chunks" in resolved_str:
        raise ValueError(
            "Do not index encrypted chunk storage. "
            "Pass your original files before encryption."
        )

    if resolved.name == "chunks":
        parent_name = resolved.parent.name.lower() if resolved.parent else ""
        if "firecloud" in parent_name or "storage" in parent_name:
            raise ValueError(
                "Do not index encrypted chunk storage. "
                "Pass your original files before encryption."
            )


def index_path(path: Path) -> int:
    """Index files at *path* into the local Qdrant collection.

    Only .txt, .md, .py, and .json files are processed.
    Returns the total number of chunks indexed.
    """
    path = Path(path).resolve()
    _safety_check(path)

    settings = get_settings()
    settings.qdrant_path.mkdir(parents=True, exist_ok=True)

    client = QdrantClient(path=str(settings.qdrant_path))
    _ensure_collection(client, settings.collection_name)

    if path.is_file():
        files = [path] if path.suffix in _SUPPORTED_EXTENSIONS else []
    elif path.is_dir():
        files = sorted(
            f for f in path.rglob("*")
            if f.is_file() and f.suffix in _SUPPORTED_EXTENSIONS
        )
    else:
        return 0

    total_chunks = 0

    with Progress() as progress:
        task = progress.add_task("[cyan]Indexing files…", total=len(files))

        for filepath in files:
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                progress.advance(task)
                continue

            chunks = chunk_text(content)
            if not chunks:
                progress.advance(task)
                continue

            vectors = embed_chunks(chunks)
            now = datetime.now(timezone.utc).isoformat()

            points = [
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vec,
                    payload={
                        "filename": filepath.name,
                        "filepath": str(filepath),
                        "chunk_index": i,
                        "content": chunk,
                        "indexed_at": now,
                    },
                )
                for i, (chunk, vec) in enumerate(zip(chunks, vectors))
            ]

            client.upsert(collection_name=settings.collection_name, points=points)
            total_chunks += len(points)
            progress.advance(task)

    return total_chunks
