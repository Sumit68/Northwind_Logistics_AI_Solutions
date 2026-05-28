"""Index policy PDFs into Weaviate for RAG-powered policy Q&A."""

from __future__ import annotations

import re
import time

from app.config import settings
from app.llm.client import llm_client
from app.services.unstructured_io import partition_file
from app.services.vector_store import (
    VECTOR_DIM,
    chunk_count,
    clear_collection,
    collection_vector_dim,
    insert_chunks,
    search_chunks,
    weaviate_client,
    PolicyChunkHit,
)

EXPENSE_DOC_PREFIXES = ("TEP-", "SEC-301")


def _split_policy_documents(full_text: str) -> list[dict]:
    chunks: list[dict] = []
    current_doc = "UNKNOWN"
    current_section = ""
    buffer: list[str] = []

    def flush():
        if not buffer:
            return
        body = "\n".join(buffer).strip()
        if len(body) < 40:
            return
        if not any(current_doc.startswith(p) for p in EXPENSE_DOC_PREFIXES):
            return
        for i in range(0, len(body), 1200):
            piece = body[i : i + 1200]
            chunks.append(
                {
                    "doc_id": current_doc,
                    "section": current_section,
                    "content": piece,
                }
            )

    for line in full_text.splitlines():
        if line.startswith("Document: TEP-") or line.startswith("Document: SEC-"):
            flush()
            buffer = []
            parts = line.split()
            current_doc = parts[1] if len(parts) > 1 else current_doc
            current_section = ""
        elif re.match(r"^\d+\.\s", line.strip()):
            current_section = line.strip()[:32]
        buffer.append(line)
    flush()
    return chunks


def extract_policy_corpus() -> list[dict]:
    all_chunks: list[dict] = []
    for pdf in sorted(settings.policies_path.glob("*.pdf")):
        text_content = partition_file(pdf)
        all_chunks.extend(_split_policy_documents(text_content))
    return all_chunks


def wait_for_weaviate(max_seconds: int = 90) -> bool:
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            with weaviate_client() as client:
                client.is_ready()
                return True
        except Exception:
            time.sleep(2)
    return False


def index_policies(force: bool = False) -> int:
    if not wait_for_weaviate():
        raise RuntimeError(f"Weaviate not ready at {settings.weaviate_url}")

    with weaviate_client() as client:
        existing = chunk_count(client)
        stored_dim = collection_vector_dim(client)
        if stored_dim is not None and stored_dim != VECTOR_DIM:
            logger.warning(
                "Policy index dimension %s != %s (local %s) — rebuilding index",
                stored_dim,
                VECTOR_DIM,
                settings.local_embedding_model,
            )
            clear_collection(client)
            existing = 0
        if existing > 0 and not force:
            return existing

        if force:
            clear_collection(client)

        raw_chunks = extract_policy_corpus()
        if not raw_chunks:
            return 0

        texts = [c["content"] for c in raw_chunks]
        batch_size = 32
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            embeddings.extend(llm_client.embed(texts[i : i + batch_size]))

        return insert_chunks(client, raw_chunks, embeddings)


def search_policy_chunks(query: str, top_k: int | None = None) -> list[tuple[PolicyChunkHit, float]]:
    k = top_k or settings.policy_rag_top_k
    query_emb = llm_client.embed([query])[0]
    hits = search_chunks(query, query_emb, k)
    return [(h, h.score) for h in hits]
