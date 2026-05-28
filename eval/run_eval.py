#!/usr/bin/env python3
"""Evaluation harness: JSON fixtures in → metrics out."""

import argparse
import json
import sys
from pathlib import Path

# Allow importing backend when run from repo root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.graph.review_workflow import review_line_item  # noqa: E402
from app.services.receipt_context import enrich_extraction_for_review  # noqa: E402
from app.services.receipt_extractor import extract_receipt  # noqa: E402


def load_fixture(path: Path) -> dict:
    return json.loads(path.read_text())


def run_case(case: dict, submissions_path: Path) -> dict:
    folder = case.get("submission_folder")
    receipt_file = case.get("receipt_file")
    trip_context = case.get("trip_context", {})
    expected_status = case.get("expected_status")
    expected_policy = case.get("expected_policy_refs", [])

    receipt_path = submissions_path / folder / "receipts" / receipt_file
    if not receipt_path.exists():
        return {"error": f"Missing {receipt_path}", "pass": False}

    extraction = extract_receipt(receipt_path, "application/pdf")
    extraction = enrich_extraction_for_review(
        extraction,
        filename=receipt_path.name,
        mime_type="application/pdf",
    )
    result = review_line_item(extraction, trip_context)

    actual_status = result["status"].value if hasattr(result["status"], "value") else result["status"]
    status_ok = actual_status == expected_status
    policy_ok = True
    if expected_policy:
        doc = result.get("policy_doc_id") or ""
        policy_ok = any(p.split("§")[0] in (doc or "") for p in expected_policy)

    quote = result.get("policy_quote") or ""
    quote_ok = bool(quote) if expected_status in ("flagged", "rejected") else True

    return {
        "case_id": case.get("id"),
        "expected_status": expected_status,
        "actual_status": actual_status,
        "status_match": status_ok,
        "policy_match": policy_ok,
        "has_quote": bool(quote),
        "quote_ok": quote_ok,
        "pass": status_ok and policy_ok and quote_ok,
        "reasoning": result.get("reasoning"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default=str(Path(__file__).parent / "fixtures" / "smoke.json"))
    parser.add_argument("--submissions", default=str(ROOT / "submissions"))
    args = parser.parse_args()

    fixture = load_fixture(Path(args.fixture))
    cases = fixture.get("cases", [])
    submissions_path = Path(args.submissions)

    results = [run_case(c, submissions_path) for c in cases]
    passed = sum(1 for r in results if r.get("pass"))
    total = len(results)

    print(json.dumps({"passed": passed, "total": total, "results": results}, indent=2))
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
