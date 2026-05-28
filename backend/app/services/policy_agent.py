import json

from app.llm.client import llm_client
from app.services.review_parallel import run_bounded
from app.services.deterministic_rules import check_deterministic
from app.services.policy_loader import load_policy_rules
from app.services.receipt_context import itemization_satisfied, receipt_provided

AGENT_SYSTEM = """You are a Northwind policy specialist agent. Review the expense against ONLY the provided policy JSON.

CRITICAL CONTEXT — read receipt_source first:
- The finance reviewer UPLOADED a receipt file (PDF/image). The `extraction` object was produced by parsing that file.
- receipt_source.document_provided=true means a receipt IS on file. NEVER claim the receipt is "missing", "not attached", or ask for an "original receipt image".
- Judge TEP-007 itemization using extraction.line_items and receipt_text_excerpt from the uploaded document.
- If line_items are present and receipt_source.document_provided=true, do NOT flag solely for lacking itemization.
- Flag real policy issues: caps, alcohol, class of service, amount mismatches, low extraction_confidence, date anomalies vs trip_context.
- SUBMITTER: trip_context.submitter / trip_context.employee is the expense owner selected in the UI when the submission was created (grade, title, department). This is NOT the finance reviewer uploading receipts.
- For TEP-009: match grade_ladder to submitter.grade and compare approval_authority (required_approver_min_grade vs submitter grade; self_travel_limit_usd).
- submission_total is cumulative per submission (TEP-001 §4.4).

Return JSON: applicable (bool), status (compliant|flagged|rejected|needs_review), reasoning, policy_doc_id, policy_section,
policy_quote (verbatim from policy rules), confidence (0-1). If policy does not apply, applicable=false."""


def _build_agent_payload(doc_id: str, extraction: dict, trip_context: dict) -> dict:
    raw = extraction.get("raw_text") or ""
    payload_extraction = {
        k: v
        for k, v in extraction.items()
        if k != "raw_text"
    }
    payload_extraction["receipt_text_excerpt"] = raw[:2500]
    rules_doc = load_policy_rules(doc_id) or {}
    submitter = trip_context.get("submitter") or trip_context.get("employee") or {}
    notes = [
        "Receipt file was uploaded and parsed. Evaluate policy against extracted fields and excerpt only.",
        trip_context.get("submitter_note")
        or "Submitter is trip_context.employee (UI-selected expense owner).",
    ]
    if doc_id == "TEP-009":
        notes.append(
            "Apply TEP-009 grade_ladder to submitter.grade; use approval_authority for TEP-001 threshold checks."
        )
    return {
        "policy": rules_doc,
        "grade_ladder": rules_doc.get("grade_ladder"),
        "definitions": rules_doc.get("definitions"),
        "faq": rules_doc.get("faq"),
        "extraction": payload_extraction,
        "trip_context": trip_context,
        "submitter": submitter,
        "employee": submitter,
        "approval_authority": trip_context.get("approval_authority"),
        "review_notes": " ".join(n for n in notes if n),
    }


def run_policy_agent(doc_id: str, extraction: dict, trip_context: dict) -> dict:
    det = check_deterministic(doc_id, extraction, trip_context)
    if det:
        return det

    # TEP-010 fast-path only when no prohibited/disputed signals are present.
    if doc_id == "TEP-010" and itemization_satisfied(extraction):
        raw = (extraction.get("raw_text") or "").lower()
        risk_signals = [
            "cash advance",
            "personal purchase",
            "gambling",
            "adult entertainment",
            "political contribution",
            "family member",
            "third party",
            "disputed",
            "fraud",
            "awaiting resolution",
        ]
        if not any(s in raw for s in risk_signals):
            return {
                "applicable": True,
                "status": "compliant",
                "reasoning": (
                    f"Itemized receipt on file ({extraction.get('receipt_source', {}).get('filename', 'upload')}); "
                    f"{len(extraction.get('line_items') or [])} line item(s) parsed from document."
                ),
                "policy_doc_id": doc_id,
                "policy_section": "§3.1",
                "policy_quote": "All charges must be supported by itemized receipts per TEP-007.",
                "confidence": 0.92,
                "deterministic": True,
            }

    rules = load_policy_rules(doc_id)
    if not rules:
        return {
            "applicable": False,
            "status": "compliant",
            "reasoning": f"No rules loaded for {doc_id}.",
            "confidence": 0.5,
        }

    user = json.dumps(_build_agent_payload(doc_id, extraction, trip_context))
    result = llm_client.complete_json(
        AGENT_SYSTEM + f" You are the specialist for {doc_id}.",
        user,
    )
    result.setdefault("policy_doc_id", doc_id if result.get("applicable", True) else None)

    if receipt_provided(extraction):
        reasoning = (result.get("reasoning") or "").lower()
        attachment_hallucinations = (
            "not attached",
            "not confirm that an original",
            "receipt image was attached",
            "no receipt",
            "missing receipt",
            "without a receipt",
            "lack of receipt",
        )
        if any(p in reasoning for p in attachment_hallucinations):
            if itemization_satisfied(extraction) and result.get("status") == "flagged":
                result["status"] = "compliant"
                result["reasoning"] = (
                    "Uploaded receipt was parsed with itemized charges; "
                    "no separate attachment check required."
                )
                result["confidence"] = min(float(result.get("confidence", 0.85)), 0.9)

    return result


async def run_policy_agent_async(doc_id: str, extraction: dict, trip_context: dict) -> dict:
    """Thread-pool wrapper for parallel policy agent invocations."""
    return await run_bounded(run_policy_agent, doc_id, extraction, trip_context)
