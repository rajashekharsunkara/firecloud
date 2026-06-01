"""Search the local Qdrant collection for relevant chunks."""

from pydantic import BaseModel, ConfigDict
from qdrant_client import QdrantClient

from fc_rag.config import get_settings
from fc_rag.embedder import embed_chunks


class RetrievalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    filename: str
    score: float
    chunk_index: int


def retrieve(query: str, top_k: int | None = None) -> list[RetrievalResult]:
    """Embed *query* and return the closest chunks from Qdrant."""
    settings = get_settings()
    k = top_k if top_k is not None else settings.top_k

    vectors = embed_chunks([query])
    if not vectors:
        return []
    query_vector = vectors[0]

    client = QdrantClient(path=str(settings.qdrant_path))

    response = client.query_points(
        collection_name=settings.collection_name,
        query=query_vector,
        limit=k,
    )
    results = response.points

    return [
        RetrievalResult(
            content=hit.payload.get("content", ""),
            filename=hit.payload.get("filename", "unknown"),
            score=hit.score,
            chunk_index=hit.payload.get("chunk_index", 0),
        )
        for hit in results
    ]
