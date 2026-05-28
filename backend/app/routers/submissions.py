import asyncio
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models import (
    Employee,
    LineItem,
    Override,
    Receipt,
    Submission,
    SubmissionStatus,
    VerdictStatus,
)
from app.schemas import (
    OverrideCreate,
    OverrideOut,
    ReviewStartResponse,
    SubmissionCreate,
    SubmissionDetailOut,
    SubmissionOut,
    VerdictOut,
)
from app.services.review_runner import run_submission_review_background

router = APIRouter(prefix="/submissions", tags=["submissions"])

_review_tasks: dict[int, asyncio.Task] = {}


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


@router.post("/{submission_id}/review", response_model=ReviewStartResponse)
async def start_review(submission_id: int, db: Session = Depends(get_db)):
    sub = db.get(Submission, submission_id)
    if not sub:
        raise HTTPException(404, "Submission not found")
    if sub.status == SubmissionStatus.processing:
        return ReviewStartResponse(submission_id=submission_id, status="processing")

    sub.status = SubmissionStatus.processing
    db.commit()

    task = asyncio.create_task(run_submission_review_background(submission_id))
    _review_tasks[submission_id] = task

    def _done(t: asyncio.Task) -> None:
        _review_tasks.pop(submission_id, None)
        if not t.cancelled() and t.exception():
            pass

    task.add_done_callback(_done)
    return ReviewStartResponse(submission_id=submission_id, status="processing")


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
