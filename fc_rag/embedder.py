"""Local text chunking and embedding via fastembed.

Default model: BAAI/bge-small-en-v1.5 (384-dim, CPU-only).
"""

from functools import lru_cache
from fastembed import TextEmbedding
from fc_rag.config import get_settings


@lru_cache(maxsize=1)
def _get_model() -> TextEmbedding:
    settings = get_settings()
    return TextEmbedding(model_name=settings.embedding_model)


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
    """Split *text* into overlapping chunks on whitespace boundaries."""
    if not text or not text.strip():
        return []

    words = text.split()
    chunks: list[str] = []
    current_words: list[str] = []
    current_len = 0

    for word in words:
        word_len = len(word)
        addition = word_len if not current_words else word_len + 1

        if current_len + addition > chunk_size and current_words:
            chunks.append(" ".join(current_words))

            # keep tail words for overlap
            overlap_words: list[str] = []
            overlap_len = 0
            for w in reversed(current_words):
                candidate = len(w) if not overlap_words else len(w) + 1
                if overlap_len + candidate > overlap:
                    break
                overlap_words.insert(0, w)
                overlap_len += candidate

            current_words = overlap_words
            current_len = overlap_len

        current_words.append(word)
        current_len += addition

    if current_words:
        chunks.append(" ".join(current_words))

    return chunks


def embed_chunks(chunks: list[str]) -> list[list[float]]:
    """Batch-embed text chunks using the local fastembed model."""
    if not chunks:
        return []
    model = _get_model()
    embeddings = list(model.embed(chunks))
    return [emb.tolist() for emb in embeddings]
