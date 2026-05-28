"""Unstructured.io API for PDF/image/text partitioning."""

from __future__ import annotations

import io
from pathlib import Path

from app.config import settings


def partition_file(path: Path, content_type: str | None = None) -> str:
    """Return plain text from a document via Unstructured API."""
    if not settings.unstructured_api_key:
        return _fallback_text(path)

    try:
        from unstructured_client import UnstructuredClient
        from unstructured_client.models import operations, shared
    except ImportError:
        return _fallback_text(path)

    client = UnstructuredClient(
        api_key_auth=settings.unstructured_api_key,
        server_url=settings.unstructured_api_url,
    )

    with path.open("rb") as f:
        data = f.read()

    req = operations.PartitionRequest(
        partition_parameters=shared.PartitionParameters(
            files=shared.Files(
                content=data,
                file_name=path.name,
            ),
            strategy=shared.Strategy.AUTO,
            languages=["eng"],
        )
    )

    elements = client.general.partition(request=req)
    parts = []
    for el in elements:
        text = getattr(el, "text", None) or (el if isinstance(el, str) else "")
        if text:
            parts.append(str(text).strip())
    return "\n".join(parts) if parts else _fallback_text(path)


def _fallback_text(path: Path) -> str:
    import fitz

    if path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".pdf":
        doc = fitz.open(path)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text
    return path.read_text(encoding="utf-8", errors="ignore")
