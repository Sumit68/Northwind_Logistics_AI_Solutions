"""Attach upload/provenance metadata so policy agents review parsed receipt content, not abstract claims."""

from __future__ import annotations


def enrich_extraction_for_review(
    extraction: dict,
    *,
    filename: str,
    mime_type: str,
) -> dict:
    """Return a copy of extraction with receipt_source the agents must honor."""
    data = dict(extraction)
    line_items = data.get("line_items") or []
    raw = (data.get("raw_text") or "").strip()
    data["receipt_source"] = {
        "document_provided": True,
        "filename": filename,
        "mime_type": mime_type,
        "extraction_method": "uploaded_file_parsed",
        "line_items_count": len(line_items),
        "has_parseable_text": len(raw) > 0,
        "extraction_confidence": float(data.get("confidence", 0.5)),
    }
    return data


def receipt_provided(extraction: dict) -> bool:
    return bool((extraction.get("receipt_source") or {}).get("document_provided"))


def itemization_satisfied(extraction: dict) -> bool:
    """Uploaded receipt with at least one parsed line item (or positive total + text)."""
    if not receipt_provided(extraction):
        return False
    if len(extraction.get("line_items") or []) >= 1:
        return True
    raw = (extraction.get("raw_text") or "").strip()
    total = float(extraction.get("total") or 0)
    return len(raw) > 80 and total > 0
