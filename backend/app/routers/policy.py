import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    AsyncJobStart,
    AsyncJobStatus,
    PolicyChatRequest,
    PolicyChatResponse,
    PolicyCitation,
)
from app.services import async_jobs
from app.services.policy_chat import policy_chat

router = APIRouter(prefix="/policy", tags=["policy"])


def _to_chat_response(result: dict) -> PolicyChatResponse:
    citations = [PolicyCitation(**c) for c in result.get("citations", [])]
    return PolicyChatResponse(
        answer=result.get("answer", ""),
        citations=citations,
        refused=result.get("refused", False),
        retrieval_confidence=result.get("retrieval_confidence", 0.0),
    )


@router.post("/chat", response_model=AsyncJobStart)
async def start_policy_chat(payload: PolicyChatRequest, _db: Session = Depends(get_db)):
    message = payload.message.strip()
    if not message:
        raise HTTPException(400, "Message is required")

    job_id = async_jobs.create_job()
    asyncio.create_task(async_jobs.run_in_background(job_id, lambda: policy_chat(message)))
    return AsyncJobStart(job_id=job_id, status="processing")


@router.get("/chat/jobs/{job_id}", response_model=AsyncJobStatus)
def get_policy_chat_job(job_id: str):
    job = async_jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    result = _to_chat_response(job.result) if job.result else None
    return AsyncJobStatus(
        job_id=job_id,
        status=job.status.value,
        result=result,
        error=job.error,
    )
