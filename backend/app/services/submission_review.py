"""Submission-level policy checks (cumulative totals, timeliness, same-day meals)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.services.deterministic_rules import SEVERITY, check_deterministic
from app.services.grade_authority import enrich_employee_approval_context

# Re-export for routers: trip_context always reflects the employee chosen in the submission UI.
__all__ = [
    "build_submission_context",
    "build_trip_context_from_submission",
    "check_submission_level",
]


def _parse_trip_end(trip_dates: str) -> datetime | None:
    if not trip_dates:
        return None
    m = re.search(r"to\s+(\d{4}-\d{2}-\d{2})", trip_dates, re.I)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    m = re.search(r"(\d{4}-\d{2}-\d{2})", trip_dates)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    return None


def build_submission_context(
    *,
    trip_purpose: str,
    trip_dates: str,
    employee_name: str,
    grade: int,
    title: str = "",
    department: str = "",
    employee_id: str = "",
    manager_id: str = "",
    receipt_extractions: list[dict],
) -> dict:
    """Enrich trip_context with cumulative submission fields for TEP-001 / TEP-002 / TEP-009."""
    submission_total = sum(float(e.get("total") or 0) for e in receipt_extractions)
    meal_by_date: dict[str, int] = {}
    for ext in receipt_extractions:
        if ext.get("category_hint") != "meal":
            continue
        d = ext.get("expense_date") or "unknown"
        meal_by_date[d] = meal_by_date.get(d, 0) + 1

    max_same_day = max(meal_by_date.values()) if meal_by_date else 0
    trip_end = _parse_trip_end(trip_dates)

    ctx: dict = {
        "trip_purpose": trip_purpose,
        "trip_dates": trip_dates,
        "employee_name": employee_name,
        "grade": grade,
        "title": title,
        "department": department,
        "employee_id": employee_id,
        "manager_id": manager_id,
        "submission_total": submission_total,
        "same_day_meal_count": max_same_day,
        "trip_end_date": trip_end.isoformat() if trip_end else None,
        "submission_reviewed_at": datetime.utcnow().isoformat(),
    }
    ctx.update(
        enrich_employee_approval_context(
            grade=grade,
            title=title,
            name=employee_name,
            employee_id=employee_id,
            department=department,
            manager_id=manager_id,
            submission_total=submission_total,
        )
    )
    # Explicit alias for policy agents (TEP-009): expense owner / submitter from UI selection.
    ctx["submitter"] = dict(ctx["employee"])
    ctx["submitter_note"] = (
        "Submitter is the employee selected when the submission was created in the UI "
        "(grade/title drive TEP-009 approval authority — not the finance reviewer)."
    )
    return ctx


def build_trip_context_from_submission(sub, receipt_extractions: list[dict]) -> dict:
    """
    Build trip_context from the Submission's linked Employee (UI-selected expense owner).

    Flow: New Submission page → employee dropdown → POST /submissions { employee_id }
          → review loads sub.employee → this function → every policy_agent call.
    """
    emp = sub.employee
    if emp is None:
        raise ValueError(
            "Submission has no linked employee. Select an employee in the UI before review."
        )
    return build_submission_context(
        trip_purpose=sub.trip_purpose,
        trip_dates=sub.trip_dates,
        employee_name=emp.name,
        grade=emp.grade,
        title=emp.title,
        department=emp.department,
        employee_id=emp.employee_id,
        manager_id=emp.manager_id,
        receipt_extractions=receipt_extractions,
    )


def check_submission_level(trip_context: dict) -> dict | None:
    """Run submission-level rules (TEP-001 thresholds + TEP-009 grade authority)."""
    empty_extraction: dict = {"category_hint": "other", "total": 0, "raw_text": ""}
    best: dict | None = None
    best_sev = -1
    for doc_id in ("TEP-001", "TEP-009"):
        hit = check_deterministic(doc_id, empty_extraction, trip_context)
        if not hit:
            continue
        sev = SEVERITY.get(hit.get("status", "compliant"), 0)
        if sev > best_sev:
            best = hit
            best_sev = sev
    return best
