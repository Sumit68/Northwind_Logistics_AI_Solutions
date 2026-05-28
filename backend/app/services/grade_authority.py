"""TEP-009 grade ladder and approval authority helpers."""

from __future__ import annotations

GRADE_LADDER: list[dict] = [
    {"grade": 1, "title": "Associate / Coordinator", "self_travel_limit": 0, "direct_report_limit": 0},
    {"grade": 2, "title": "Senior Associate", "self_travel_limit": 0, "direct_report_limit": 0},
    {"grade": 3, "title": "Specialist", "self_travel_limit": 500, "direct_report_limit": 0},
    {"grade": 4, "title": "Senior Specialist", "self_travel_limit": 1000, "direct_report_limit": 0},
    {"grade": 5, "title": "Manager", "self_travel_limit": 0, "direct_report_limit": 2000},
    {"grade": 6, "title": "Senior Manager", "self_travel_limit": 0, "direct_report_limit": 3000},
    {"grade": 7, "title": "Director", "self_travel_limit": 0, "direct_report_limit": 5000},
    {"grade": 8, "title": "Senior Director", "self_travel_limit": 0, "direct_report_limit": 10000},
    {"grade": 9, "title": "Vice President", "self_travel_limit": 0, "direct_report_limit": 25000},
    {"grade": 10, "title": "SVP / C-suite", "self_travel_limit": 0, "direct_report_limit": None},
]

DIRECTOR_MIN_GRADE = 7
VP_MIN_GRADE = 9


def grade_entry(grade: int) -> dict | None:
    for row in GRADE_LADDER:
        if row["grade"] == grade:
            return row
    return None


def self_travel_limit(grade: int) -> float:
    row = grade_entry(grade)
    if not row:
        return 0.0
    return float(row.get("self_travel_limit") or 0)


def direct_report_limit(grade: int) -> float | None:
    row = grade_entry(grade)
    if not row:
        return 0.0
    val = row.get("direct_report_limit")
    if val is None:
        return None
    return float(val)


def submission_approval_requirement(submission_total: float) -> dict:
    """TEP-001 §4 thresholds mapped to TEP-009 approver grades."""
    if submission_total <= 1000:
        return {
            "role": "manager",
            "min_grade": None,
            "label": "direct manager",
            "tep_section": "TEP-001 §4.1",
        }
    if submission_total <= 5000:
        return {
            "role": "director",
            "min_grade": DIRECTOR_MIN_GRADE,
            "label": "Director",
            "tep_section": "TEP-001 §4.2",
        }
    return {
        "role": "vp",
        "min_grade": VP_MIN_GRADE,
        "label": "VP",
        "tep_section": "TEP-001 §4.3",
    }


def enrich_employee_approval_context(
    *,
    grade: int,
    title: str,
    name: str,
    employee_id: str,
    department: str,
    manager_id: str,
    submission_total: float | None = None,
) -> dict:
    """Build employee + approval_authority blocks for policy agents and deterministic rules."""
    row = grade_entry(grade) or {}
    req = submission_approval_requirement(float(submission_total or 0))
    self_limit = self_travel_limit(grade)
    dr_limit = direct_report_limit(grade)

    can_self = submission_total is not None and self_limit > 0 and submission_total <= self_limit
    meets_submission_grade = True
    if req.get("min_grade") is not None:
        meets_submission_grade = grade >= int(req["min_grade"])

    return {
        "employee": {
            "employee_id": employee_id,
            "name": name,
            "grade": grade,
            "title": title,
            "department": department,
            "manager_id": manager_id,
            "typical_title": row.get("title"),
        },
        "approval_authority": {
            "self_travel_limit_usd": self_limit if self_limit > 0 else None,
            "direct_report_approval_limit_usd": dr_limit,
            "submission_total_usd": submission_total,
            "required_approver_role": req["role"],
            "required_approver_label": req["label"],
            "required_approver_min_grade": req.get("min_grade"),
            "submitter_meets_required_grade": meets_submission_grade,
            "submitter_within_self_travel_limit": can_self,
            "vp_approval_definition": "Grade 9+ (Vice President or above)",
            "director_approval_definition": "Grade 7+ (Director or above)",
            "manager_approval_definition": "Employee's direct manager with authority for the amount",
        },
    }
