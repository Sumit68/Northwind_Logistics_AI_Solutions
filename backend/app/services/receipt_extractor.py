import json
from pathlib import Path

from app.llm.client import llm_client
from app.services.unstructured_io import partition_file

EXTRACT_SYSTEM = """You extract structured data from receipt text into JSON.
Fields: vendor, expense_date (YYYY-MM-DD if possible), category_hint (meal|lodging|air|ground|conference|other),
line_items[{description, amount}], subtotal, tax, tip, total, currency (default USD if $ or unclear),
guest_count (int), alcohol_detected (bool), notes (string array), nights (int, for hotels),
confidence (0-1 float from your certainty), ocr_confidence (null unless provided in input).
"""


def extract_receipt(path: Path, mime_type: str, ocr_confidence: float | None = None) -> dict:
    raw_text = partition_file(path, mime_type)
    ocr_conf = ocr_confidence

    user_payload = json.dumps(
        {
            "raw_text": raw_text[:14000],
            "ocr_confidence": ocr_conf,
            "instruction": "Return ExtractedReceipt JSON.",
        }
    )
    data = llm_client.complete_json(EXTRACT_SYSTEM, user_payload)
    if ocr_conf is not None and data.get("ocr_confidence") is None:
        data["ocr_confidence"] = ocr_conf
    if not data.get("currency") or "$" in raw_text:
        data["currency"] = "USD"
    data["raw_text"] = raw_text
    return data
