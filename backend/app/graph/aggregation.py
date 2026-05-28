from app.models import VerdictStatus
from app.services.policy_loader import load_policy_rules

SEVERITY = {
    VerdictStatus.rejected: 4,
    VerdictStatus.flagged: 3,
    VerdictStatus.needs_review: 2,
    VerdictStatus.compliant: 1,
}


def _status_from_str(s: str) -> VerdictStatus:
    try:
        return VerdictStatus(s)
    except ValueError:
        return VerdictStatus.needs_review


def validate_quote(quote: str | None, doc_id: str | None) -> bool:
    if not quote or not doc_id:
        return True
    rules = load_policy_rules(doc_id)
    if not rules:
        return False
    qnorm = quote.strip().lower().replace("\u2014", "-").replace("—", "-")
    for rule in rules.get("rules", []):
        sq = (rule.get("source_quote") or "").lower().replace("\u2014", "-").replace("—", "-")
        if qnorm in sq or sq in qnorm or (len(qnorm) >= 12 and qnorm[:12] in sq):
            return True
    return False


def aggregate_results(agent_results: list[dict], extraction: dict) -> dict:
    applicable = [r for r in agent_results if r.get("applicable", True) and r.get("status") != "compliant"]
    if not applicable:
        confs = [r.get("confidence", 0.8) for r in agent_results if r.get("applicable", True)] or [0.8]
        return {
            "status": VerdictStatus.compliant,
            "reasoning": "All applicable policy checks passed.",
            "policy_doc_id": None,
            "policy_section": None,
            "policy_quote": None,
            "confidence": min(confs),
            "agent_results": {r.get("policy_doc_id", "unknown"): r for r in agent_results},
        }

    best = max(applicable, key=lambda r: SEVERITY.get(_status_from_str(r.get("status", "compliant")), 0))
    status = _status_from_str(best.get("status", "needs_review"))
    quote = best.get("policy_quote")
    doc_id = best.get("policy_doc_id")

    if quote and doc_id and not validate_quote(quote, doc_id):
        status = VerdictStatus.needs_review
        best["reasoning"] = (best.get("reasoning") or "") + " Citation could not be verified."

    if extraction.get("confidence", 1) < 0.6:
        status = VerdictStatus.needs_review

    reasons = [r.get("reasoning", "") for r in applicable]
    return {
        "status": status,
        "reasoning": " | ".join(filter(None, reasons)),
        "policy_doc_id": doc_id,
        "policy_section": best.get("policy_section"),
        "policy_quote": quote,
        "confidence": best.get("confidence", 0.5),
        "agent_results": {r.get("policy_doc_id", f"agent_{i}"): r for i, r in enumerate(agent_results)},
    }
