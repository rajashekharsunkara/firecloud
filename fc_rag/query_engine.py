"""RAG pipeline: retrieve context → build prompt → query Ollama."""

import json
import sys
import time
from datetime import datetime, timezone

import ollama
from fc_rag.config import get_settings
from fc_rag.retriever import retrieve


def query(user_question: str) -> str:
    """Run the full RAG pipeline and return the LLM's answer."""
    settings = get_settings()
    start = time.monotonic()

    results = retrieve(user_question)

    # build context block from retrieved chunks
    if results:
        ctx_parts = []
        for i, r in enumerate(results, 1):
            ctx_parts.append(f"[{i}] (source: {r.filename})\n{r.content}")
        context = "\n\n".join(ctx_parts)
    else:
        context = "(No relevant context found.)"

    messages = [
        {
            "role": "system",
            "content": (
                "Answer only from the provided context. "
                "If the context does not contain the answer, say so."
            ),
        },
        {
            "role": "user",
            "content": f"Context:\n{context}\n\nQuestion: {user_question}",
        },
    ]

    answer: str | None = None
    success = False

    for attempt in range(1, settings.max_retries + 1):
        try:
            response = ollama.chat(
                model=settings.ollama_model,
                messages=messages,
            )
            answer = response["message"]["content"]
            success = True
            break
        except (ollama.ResponseError, ConnectionError) as exc:
            print(
                f"[attempt {attempt}/{settings.max_retries}] Ollama error: {exc}",
                file=sys.stderr,
            )
            if attempt < settings.max_retries:
                time.sleep(2)

    if not success:
        answer = "Local LLM unavailable. Start Ollama with: ollama serve"

    elapsed_ms = (time.monotonic() - start) * 1000

    # append to query log
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(settings.log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question_length": len(user_question),
            "chunks_retrieved": len(results),
            "latency_ms": round(elapsed_ms, 2),
            "success": success,
        }, default=str) + "\n")

    return answer
