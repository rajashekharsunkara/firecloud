"""Pydantic settings for the fc_rag pipeline."""

from functools import lru_cache
from pathlib import Path
from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    """All paths default to ~/.fc_rag/ so it works out of the box."""

    model_config = ConfigDict(frozen=True)

    ollama_model: str = "llama3.2:3b"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    qdrant_path: Path = Path.home() / ".fc_rag" / "vectors"
    collection_name: str = "firecloud_docs"
    top_k: int = 5
    max_retries: int = 3
    log_path: Path = Path.home() / ".fc_rag" / "query_log.jsonl"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_client():
    """Shared embedded-Qdrant client.

    Embedded Qdrant locks its storage directory per client instance, so
    indexing and retrieval within one process must reuse a single client
    rather than each opening their own.
    """
    from qdrant_client import QdrantClient

    settings = get_settings()
    settings.qdrant_path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(settings.qdrant_path))
