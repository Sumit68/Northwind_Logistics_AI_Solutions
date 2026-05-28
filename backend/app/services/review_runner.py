"""Submission pre-review execution (sync DB session for background worker)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.graph.review_workflow import build_extraction_trace, review_line_item_async
from app.graph.workflow_log import trace_entry
from app.models import (
    LineItem,
    Receipt,
    Submission,
    SubmissionStatus,
    Verdict,
    VerdictStatus,
)
from app.services.receipt_context import enrich_extraction_for_review
from app.services.receipt_extractor import extract_receipt
from app.services.review_parallel import run_bounded
from app.services.submission_review import build_trip_context_from_submission, check_submission_level
from pathlib import Path

logger = logging.getLogger(__name__)


def _merge_submission_hit(result: dict, submission_hit: dict | None) -> dict:
    if not submission_hit:
        return result
    agent_results_pre = dict(result.get("agent_results") or {})
    hit_doc = submission_hit.get("policy_doc_id") or "submission"
    agent_results_pre[f"{hit_doc}_submission"] = submission_hit
    result["agent_results"] = agent_results_pre
    sub_sev = {"rejected": 3, "flagged": 2, "needs_review": 1, "compliant": 0}
    cur_status = result.get("status")
    cur_key = cur_status.value if hasattr(cur_status, "value") else str(cur_status)
    cur = sub_sev.get(cur_key, 0)
    hit = sub_sev.get(submission_hit.get("status", "compliant"), 0)
    if hit > cur:
        result["status"] = submission_hit["status"]
        result["reasoning"] = (
            f"{submission_hit.get('reasoning', '')} | {result.get('reasoning', '')}"
        ).strip(" |")
        result["policy_doc_id"] = submission_hit.get("policy_doc_id")
        result["policy_section"] = submission_hit.get("policy_section")
        result["policy_quote"] = submission_hit.get("policy_quote")
    return result


def _verdict_status(result: dict) -> VerdictStatus:
    status = result["status"]
    if isinstance(status, VerdictStatus):
        return status
    return VerdictStatus(status)


async def _extract_one_receipt(receipt: Receipt) -> tuple[Receipt, dict]:
    path = Path(receipt.storage_path)
    extraction = await run_bounded(extract_receipt, path, receipt.mime_type)
    extraction = enrich_extraction_for_review(
        extraction,
        filename=receipt.filename,
        mime_type=receipt.mime_type,
    )
    if extraction.get("ocr_confidence") is not None and extraction["ocr_confidence"] < 0.5:
        extraction["confidence"] = min(extraction.get("confidence", 0.5), 0.55)
    return receipt, extraction


async def execute_submission_review(submission_id: int, db: Session) -> None:
    sub = (
        db.query(Submission)
        .options(joinedload(Submission.employee), joinedload(Submission.receipts))
        .filter(Submission.id == submission_id)
        .first()
    )
    if not sub:
        raise ValueError(f"Submission {submission_id} not found")

    for receipt in sub.receipts:
        for old_li in list(receipt.line_items):
            db.delete(old_li)
    db.flush()

    if sub.receipts:
        parsed = list(await asyncio.gather(*[_extract_one_receipt(r) for r in sub.receipts]))
    else:
        parsed = []

    for receipt, extraction in parsed:
        receipt.raw_text = extraction.get("raw_text", "")
        receipt.extraction_json = extraction

    trip_context = build_trip_context_from_submission(sub, [e for _, e in parsed])
    submission_hit = check_submission_level(trip_context)
    submitter_trace = trace_entry(
        "submitter_context",
        {
            "source": "submission.employee (UI-selected expense owner)",
            "employee_id": trip_context.get("employee_id"),
            "name": trip_context.get("employee_name"),
            "grade": trip_context.get("grade"),
            "title": trip_context.get("title"),
            "department": trip_context.get("department"),
            "manager_id": trip_context.get("manager_id"),
            "submission_total": trip_context.get("submission_total"),
            "approval_authority": trip_context.get("approval_authority"),
        },
    )

    async def _review_one(receipt: Receipt, extraction: dict) -> tuple[Receipt, dict, dict]:
        result = await review_line_item_async(extraction, trip_context)
        return receipt, extraction, result

    if parsed:
        reviewed = list(await asyncio.gather(*[_review_one(r, e) for r, e in parsed]))
    else:
        reviewed = []

    for receipt, extraction, result in reviewed:
        workflow_trace = [submitter_trace, build_extraction_trace(extraction, receipt.filename)]
        result = _merge_submission_hit(result, submission_hit)

        li = LineItem(
            receipt_id=receipt.id,
            vendor=extraction.get("vendor", "Unknown"),
            expense_date=extraction.get("expense_date"),
            category=extraction.get("category_hint", "other"),
            description=", ".join(
                x.get("description", "") for x in extraction.get("line_items", [])
            )
            or receipt.filename,
            amount=float(extraction.get("total") or 0),
            currency=extraction.get("currency", "USD"),
            extraction_confidence=float(extraction.get("confidence", 0.5)),
            ocr_confidence=extraction.get("ocr_confidence"),
        )
        db.add(li)
        db.flush()

        if li.verdict:
            db.delete(li.verdict)
        agent_results = dict(result.get("agent_results") or {})
        agent_results["_workflow_trace"] = workflow_trace
        verdict = Verdict(
            line_item_id=li.id,
            status=_verdict_status(result),
            reasoning=result["reasoning"],
            policy_doc_id=result.get("policy_doc_id"),
            policy_section=result.get("policy_section"),
            policy_quote=result.get("policy_quote"),
            confidence=result.get("confidence", 0.5),
            agent_results=agent_results,
        )
        db.add(verdict)

    sub.status = SubmissionStatus.reviewed


async def run_submission_review_background(submission_id: int) -> None:
    db = SessionLocal()
    try:
        await execute_submission_review(submission_id, db)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Review failed for submission %s", submission_id)
        try:
            sub = db.get(Submission, submission_id)
            if sub:
                sub.status = SubmissionStatus.failed
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not mark submission %s as failed", submission_id)
    finally:
        db.close()
