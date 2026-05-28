from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import PolicyChatRequest, PolicyChatResponse, PolicyCitation
from app.services.policy_chat import policy_chat

router = APIRouter(prefix="/policy", tags=["policy"])


@router.post("/chat", response_model=PolicyChatResponse)
def chat(payload: PolicyChatRequest, _db: Session = Depends(get_db)):
    result = policy_chat(payload.message)
    citations = [PolicyCitation(**c) for c in result.get("citations", [])]
    return PolicyChatResponse(
        answer=result.get("answer", ""),
        citations=citations,
        refused=result.get("refused", False),
        retrieval_confidence=result.get("retrieval_confidence", 0.0),
    )
