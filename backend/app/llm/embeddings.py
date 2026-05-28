"""Local CPU embeddings for policy RAG (same model regardless of chat LLM provider)."""

from __future__ import annotations

import logging
import threading

from app.config import settings

logger = logging.getLogger(__name__)

# all-MiniLM-L6-v2 output dimension
EMBEDDING_DIM = 384

_model = None
_lock = threading.Lock()


def _model_name() -> str:
    name = (settings.local_embedding_model or "all-MiniLM-L6-v2").strip()
    if "/" not in name:
        return f"sentence-transformers/{name}"
    return name


def get_embedding_model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        from sentence_transformers import SentenceTransformer

        name = _model_name()
        logger.info("Loading local embedding model: %s (CPU)", name)
        _model = SentenceTransformer(name, device="cpu")
        return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = get_embedding_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=min(32, len(texts)),
    )
    return [v.tolist() for v in vectors]
