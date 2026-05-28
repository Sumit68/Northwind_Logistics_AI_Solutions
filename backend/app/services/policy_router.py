"""LLM policy classifier (step 4) with rule-based hints and registry always_with."""

from __future__ import annotations

import json

from app.llm.client import llm_client
from app.services.policy_loader import load_registry

VALID_POLICY_PREFIXES = ("TEP-", "SEC-")

ROUTER_SYSTEM = """You are the policy routing classifier for Northwind expense pre-review.

Given structured receipt extraction, trip context, and the full policy_registry, return JSON:
{
  "policy_ids": ["TEP-005", "TEP-007"],
  "expense_category": "air",
  "routing_confidence": 0.0-1.0,
  "reasoning": "short explanation"
}

Rules:
- Choose 2-6 doc_ids that are RELEVANT to this receipt (not every policy).
- Valid ids: TEP-001, TEP-002, TEP-003, TEP-004, TEP-005, TEP-006, TEP-007, TEP-008,
  TEP-009, TEP-010, TEP-012, TEP-013, TEP-014, SEC-301 (no TEP-011).
- Always include TEP-007 for itemization/receipt checks unless clearly inapplicable.
- Include TEP-009 when trip_context includes employee/submitter grade (approval authority / grade ladder).
- Include TEP-003 when alcohol_detected is true.
- Include TEP-013 for international destinations; SEC-301 for high-risk/sanctions signals.
- Use rule_based_hints as suggestions but apply your judgment.
- Do not include HR-only noise policies (not in registry)."""


def _rule_based_hints(extraction: dict, trip_context: dict | None = None) -> list[str]:
    """Deterministic routing suggestions passed to the LLM classifier."""
    registry = load_registry()
    category = extraction.get("category_hint", "other")
    vendor = (extraction.get("vendor") or "").lower()
    text_blob = json.dumps(extraction).lower()

    policy_ids: list[str] = []
    for p in registry["policies"]:
        types = p.get("types", [])
        signals = p.get("receipt_signals", [])
        if category not in types and category != "other":
            continue
        if signals and not any(s in vendor or s in text_blob for s in signals):
            if category not in types:
                continue
        policy_ids.append(p["doc_id"])

    bundles: dict[str, list[str]] = {
        "meal": ["TEP-002", "TEP-007", "TEP-010", "TEP-012"],
        "lodging": ["TEP-004", "TEP-007", "TEP-010"],
        "air": ["TEP-005", "TEP-007", "TEP-010"],
        "ground": ["TEP-006", "TEP-007", "TEP-010"],
        "conference": ["TEP-014", "TEP-007"],
        "entertainment": ["TEP-012", "TEP-002", "TEP-007"],
        "other": ["TEP-007", "TEP-001"],
    }
    policy_ids = list(dict.fromkeys(bundles.get(category, ["TEP-007"]) + policy_ids))

    if extraction.get("alcohol_detected"):
        policy_ids.append("TEP-003")
    if any(k in text_blob for k in ("international", "customs", "london", "tokyo")):
        policy_ids.append("TEP-013")
    if any(k in text_blob for k in ("per diem", "per-diem")):
        policy_ids.append("TEP-008")

    if trip_context and trip_context.get("grade") is not None:
        policy_ids.append("TEP-009")
    if any(k in text_blob for k in ("high-risk", "international sos")):
        policy_ids.append("SEC-301")

    policy_ids = [p for p in policy_ids if p.startswith(VALID_POLICY_PREFIXES)]
    return list(dict.fromkeys(policy_ids))


def _apply_always_with(policy_ids: list[str], extraction: dict) -> list[str]:
    registry = load_registry()
    ids = list(policy_ids)
    for p in registry["policies"]:
        if p["doc_id"] not in ids:
            continue
        extra = p.get("always_with") or []
        when = p.get("always_with_when")
        if when == "has_alcohol" and extraction.get("alcohol_detected"):
            ids.extend(extra)
        elif not when and extra:
            ids.extend(extra)
    return list(dict.fromkeys(ids))


def _sanitize_policy_ids(policy_ids: list[str] | None, fallback: list[str]) -> list[str]:
    if not policy_ids:
        return fallback
    clean = [p for p in policy_ids if p.startswith(VALID_POLICY_PREFIXES)]
    return list(dict.fromkeys(clean)) or fallback


def route_policies(extraction: dict, trip_context: dict | None = None) -> dict:
    """LLM classifier: extraction + registry → policy_ids (step 4)."""
    registry = load_registry()
    hints = _rule_based_hints(extraction, trip_context)

    payload_extraction = {k: v for k, v in extraction.items() if k != "raw_text"}
    payload_extraction["receipt_text_excerpt"] = (extraction.get("raw_text") or "")[:2000]

    user = json.dumps(
        {
            "extraction": payload_extraction,
            "trip_context": trip_context or {},
            "policy_registry": registry["policies"],
            "rule_based_hints": hints,
        }
    )

    llm_route = llm_client.complete_json(ROUTER_SYSTEM, user)
    policy_ids = _sanitize_policy_ids(llm_route.get("policy_ids"), hints)
    policy_ids = _apply_always_with(policy_ids, extraction)

    if "TEP-007" not in policy_ids:
        policy_ids.append("TEP-007")

    return {
        "policy_ids": list(dict.fromkeys(policy_ids)),
        "expense_category": llm_route.get("expense_category") or extraction.get("category_hint", "other"),
        "routing_confidence": float(llm_route.get("routing_confidence", 0.85)),
        "reasoning": llm_route.get("reasoning") or "LLM policy classification.",
        "rule_based_hints": hints,
    }
