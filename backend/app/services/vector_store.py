"""Weaviate client: hybrid BM25 + vector search for policy Q&A."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

import weaviate
from weaviate.classes.config import Configure, DataType, Property, VectorDistances
from weaviate.classes.query import MetadataQuery

from app.config import settings
from app.llm.embeddings import EMBEDDING_DIM

logger = logging.getLogger(__name__)

COLLECTION_NAME = "PolicyChunk"
VECTOR_DIM = EMBEDDING_DIM  # all-MiniLM-L6-v2 (384)
HYBRID_QUERY_PROPERTIES = ["doc_id", "section", "content"]

TEP_ID_PATTERN = re.compile(r"\b(TEP-\d{3}|SEC-\d{3})\b", re.I)


@dataclass
class PolicyChunkHit:
    doc_id: str
    section: str
    content: str
    score: float
    uuid: str | None = None
    retrieval: str = "hybrid"  # hybrid | vector


def _parse_weaviate_host_port() -> tuple[str, int, bool]:
    url = settings.weaviate_url
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.hostname or "localhost"
    port = parsed.port or 8080
    secure = parsed.scheme == "https"
    return host, port, secure


@contextmanager
def weaviate_client():
    host, port, secure = _parse_weaviate_host_port()
    client = weaviate.connect_to_custom(
        http_host=host,
        http_port=port,
        http_secure=secure,
        grpc_host=host,
        grpc_port=50051,
        grpc_secure=secure,
    )
    try:
        yield client
    finally:
        client.close()


def expand_query_for_policy_ids(query: str) -> str:
    """Boost BM25 leg: repeat explicit TEP/SEC ids so keyword leg matches doc_id field."""
    ids = TEP_ID_PATTERN.findall(query)
    if not ids:
        return query
    extra = " ".join(dict.fromkeys(i.upper() for i in ids))
    return f"{query} {extra}".strip()


def _normalize_hit_scores(hits: list[PolicyChunkHit]) -> list[PolicyChunkHit]:
    if len(hits) < 2:
        return hits
    scores = [h.score for h in hits]
    lo, hi = min(scores), max(scores)
    if hi <= lo:
        return hits
    for h in hits:
        h.score = (h.score - lo) / (hi - lo)
    return hits


def _objects_to_hits(objects, retrieval: str) -> list[PolicyChunkHit]:
    hits: list[PolicyChunkHit] = []
    for obj in objects:
        score = 0.0
        if obj.metadata:
            if obj.metadata.score is not None:
                score = float(obj.metadata.score)
            elif obj.metadata.distance is not None:
                score = max(0.0, 1.0 - float(obj.metadata.distance))
        props = obj.properties or {}
        hits.append(
            PolicyChunkHit(
                doc_id=str(props.get("doc_id", "")),
                section=str(props.get("section", "")),
                content=str(props.get("content", "")),
                score=score,
                uuid=str(obj.uuid) if obj.uuid else None,
                retrieval=retrieval,
            )
        )
    return hits


def ensure_collection(client) -> None:
    if client.collections.exists(COLLECTION_NAME):
        return
    client.collections.create(
        name=COLLECTION_NAME,
        vector_config=Configure.Vectors.self_provided(
            vector_index_config=Configure.VectorIndex.hnsw(
                distance_metric=VectorDistances.COSINE
            )
        ),
        properties=[
            Property(name="doc_id", data_type=DataType.TEXT),
            Property(name="section", data_type=DataType.TEXT),
            Property(name="content", data_type=DataType.TEXT),
        ],
    )


def chunk_count(client) -> int:
    if not client.collections.exists(COLLECTION_NAME):
        return 0
    col = client.collections.get(COLLECTION_NAME)
    return col.aggregate.over_all(total_count=True).total_count


def collection_vector_dim(client) -> int | None:
    """Return embedding dimension of first stored vector, if any."""
    if not client.collections.exists(COLLECTION_NAME):
        return None
    col = client.collections.get(COLLECTION_NAME)
    resp = col.query.fetch_objects(limit=1, include_vector=True)
    if not resp.objects:
        return None
    vec = resp.objects[0].vector
    if vec is None:
        return None
    if isinstance(vec, dict):
        vec = next(iter(vec.values()), None)
    return len(vec) if vec is not None else None


def clear_collection(client) -> None:
    if client.collections.exists(COLLECTION_NAME):
        client.collections.delete(COLLECTION_NAME)
    ensure_collection(client)


def insert_chunks(client, chunks: list[dict], embeddings: list[list[float]]) -> int:
    from weaviate.classes.data import DataObject

    ensure_collection(client)
    col = client.collections.get(COLLECTION_NAME)
    objects = [
        DataObject(
            properties={
                "doc_id": c["doc_id"],
                "section": c.get("section") or "",
                "content": c["content"],
            },
            vector=emb,
        )
        for c, emb in zip(chunks, embeddings)
    ]
    col.data.insert_many(objects)
    return len(objects)


def search_chunks_vector(query_vector: list[float], top_k: int) -> list[PolicyChunkHit]:
    with weaviate_client() as client:
        if not client.collections.exists(COLLECTION_NAME):
            return []
        col = client.collections.get(COLLECTION_NAME)
        response = col.query.near_vector(
            near_vector=query_vector,
            limit=top_k,
            return_metadata=MetadataQuery(distance=True),
        )
        return _normalize_hit_scores(_objects_to_hits(response.objects, "vector"))


def search_chunks_hybrid(
    query_text: str,
    query_vector: list[float],
    top_k: int,
    alpha: float | None = None,
) -> list[PolicyChunkHit]:
    """
    Hybrid search: BM25 (keyword, policy numbers) + vector (semantic).

    alpha: 0.0 = BM25 only, 1.0 = vector only, 0.5 = balanced (default from settings).
    """
    alpha = settings.policy_rag_hybrid_alpha if alpha is None else alpha
    bm25_query = expand_query_for_policy_ids(query_text)

    with weaviate_client() as client:
        if not client.collections.exists(COLLECTION_NAME):
            return []
        col = client.collections.get(COLLECTION_NAME)
        try:
            response = col.query.hybrid(
                query=bm25_query,
                vector=query_vector,
                alpha=alpha,
                limit=top_k,
                query_properties=HYBRID_QUERY_PROPERTIES,
                return_metadata=MetadataQuery(score=True),
            )
            hits = _objects_to_hits(response.objects, "hybrid")
            return _normalize_hit_scores(hits)
        except Exception as exc:
            logger.warning("Hybrid search failed (%s), falling back to vector-only", exc)
            return search_chunks_vector(query_vector, top_k)


def search_chunks(
    query_text: str,
    query_vector: list[float],
    top_k: int,
) -> list[PolicyChunkHit]:
    mode = (settings.policy_rag_search_mode or "hybrid").lower()
    if mode == "vector":
        return search_chunks_vector(query_vector, top_k)
    return search_chunks_hybrid(query_text, query_vector, top_k)
