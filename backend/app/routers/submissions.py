import asyncio
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.graph.review_workflow import (
    build_extraction_trace,
    review_line_item_async,
)
from app.graph.workflow_log import trace_entry
from app.services.review_parallel import run_bounded
from app.services.submission_review import build_trip_context_from_submission, check_submission_level
from app.models import (
    Employee,
    LineItem,
    Override,
    Receipt,
    Submission,
    SubmissionStatus,
    Verdict,
    VerdictStatus,
)
from app.schemas import (
    OverrideCreate,
    OverrideOut,
    SubmissionCreate,
    SubmissionDetailOut,
    SubmissionOut,
    VerdictOut,
)
from app.services.receipt_context import enrich_extraction_for_review
from app.services.receipt_extractor import extract_receipt

router = APIRouter(prefix="/submissions", tags=["submissions"])


def _effective_status(line_item: LineItem) -> VerdictStatus | None:
    if line_item.overrides:
        return line_item.overrides[-1].new_status
    if line_item.verdict:
        return line_item.verdict.status
    return None


def _line_item_out(li: LineItem) -> dict:
    v = li.verdict
    verdict_out = None
    if v:
        verdict_out = VerdictOut(
            id=v.id,
            status=v.status,
            reasoning=v.reasoning,
            policy_doc_id=v.policy_doc_id,
            policy_section=v.policy_section,
            policy_quote=v.policy_quote,
            confidence=v.confidence,
            agent_results=v.agent_results,
            effective_status=_effective_status(li),
        )
    return {
        "id": li.id,
        "vendor": li.vendor,
        "expense_date": li.expense_date,
        "category": li.category,
        "description": li.description,
        "amount": li.amount,
        "currency": li.currency,
        "extraction_confidence": li.extraction_confidence,
        "ocr_confidence": li.ocr_confidence,
        "verdict": verdict_out,
        "overrides": [OverrideOut.model_validate(o) for o in li.overrides],
    }


@router.get("", response_model=list[SubmissionOut])
def list_submissions(
    employee_id: int | None = None,
    status: SubmissionStatus | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Submission).options(joinedload(Submission.employee))
    if employee_id:
        q = q.filter(Submission.employee_id == employee_id)
    if status:
        q = q.filter(Submission.status == status)
    return q.order_by(Submission.created_at.desc()).all()


@router.post("", response_model=SubmissionOut)
def create_submission(payload: SubmissionCreate, db: Session = Depends(get_db)):
    emp = db.get(Employee, payload.employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    sub = Submission(
        employee_id=payload.employee_id,
        trip_purpose=payload.trip_purpose,
        trip_dates=payload.trip_dates,
        status=SubmissionStatus.draft,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@router.get("/{submission_id}", response_model=SubmissionDetailOut)
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    sub = (
        db.query(Submission)
        .options(
            joinedload(Submission.employee),
            joinedload(Submission.receipts).joinedload(Receipt.line_items).joinedload(LineItem.verdict),
            joinedload(Submission.receipts).joinedload(Receipt.line_items).joinedload(LineItem.overrides),
        )
        .filter(Submission.id == submission_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "Submission not found")
    receipts = []
    for r in sub.receipts:
        receipts.append(
            {
                "id": r.id,
                "filename": r.filename,
                "mime_type": r.mime_type,
                "extraction_json": r.extraction_json,
                "line_items": [_line_item_out(li) for li in r.line_items],
            }
        )
    return {
        "id": sub.id,
        "employee_id": sub.employee_id,
        "trip_purpose": sub.trip_purpose,
        "trip_dates": sub.trip_dates,
        "status": sub.status,
        "created_at": sub.created_at,
        "employee": sub.employee,
        "receipts": receipts,
    }


@router.post("/{submission_id}/receipts")
async def upload_receipts(
    submission_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    sub = db.get(Submission, submission_id)
    if not sub:
        raise HTTPException(404, "Submission not found")
    dest_dir = settings.storage_path / str(submission_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for f in files:
        ext = Path(f.filename or "receipt.pdf").suffix
        name = f"{uuid.uuid4().hex}{ext}"
        path = dest_dir / name
        with path.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        mime = f.content_type or "application/octet-stream"
        receipt = Receipt(
            submission_id=submission_id,
            filename=f.filename or name,
            mime_type=mime,
            storage_path=str(path),
        )
        db.add(receipt)
        db.flush()
        created.append({"receipt_id": receipt.id, "filename": receipt.filename})
    db.commit()
    return {"uploaded": created}


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


@router.post("/{submission_id}/review")
async def run_review(submission_id: int, db: Session = Depends(get_db)):
    sub = (
        db.query(Submission)
        .options(joinedload(Submission.employee), joinedload(Submission.receipts))
        .filter(Submission.id == submission_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "Submission not found")
    sub.status = SubmissionStatus.processing
    db.commit()

    for receipt in sub.receipts:
        for old_li in list(receipt.line_items):
            db.delete(old_li)
    db.flush()

    if sub.receipts:
        parsed = list(
            await asyncio.gather(*[_extract_one_receipt(r) for r in sub.receipts])
        )
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
        reviewed = list(
            await asyncio.gather(*[_review_one(r, e) for r, e in parsed])
        )
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
    db.commit()
    return get_submission(submission_id, db)


@router.post("/line-items/{line_item_id}/override", response_model=OverrideOut)
def override_line_item(
    line_item_id: int,
    payload: OverrideCreate,
    db: Session = Depends(get_db),
):
    li = db.get(LineItem, line_item_id)
    if not li:
        raise HTTPException(404, "Line item not found")
    ov = Override(
        line_item_id=line_item_id,
        new_status=payload.new_status,
        comment=payload.comment,
        reviewer=payload.reviewer,
    )
    db.add(ov)
    db.commit()
    db.refresh(ov)
    return ov
