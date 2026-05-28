from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models import SubmissionStatus, VerdictStatus


class EmployeeCreate(BaseModel):
    employee_id: str
    name: str
    grade: int
    title: str
    department: str
    manager_id: str
    home_base: str
    trip_purpose: str | None = None
    trip_dates: str | None = None


class EmployeeOut(EmployeeCreate):
    id: int

    class Config:
        from_attributes = True


class SubmissionCreate(BaseModel):
    employee_id: int
    trip_purpose: str
    trip_dates: str


class SubmissionOut(BaseModel):
    id: int
    employee_id: int
    trip_purpose: str
    trip_dates: str
    status: SubmissionStatus
    created_at: datetime
    employee: EmployeeOut | None = None

    class Config:
        from_attributes = True


class OverrideCreate(BaseModel):
    new_status: VerdictStatus
    comment: str
    reviewer: str = "finance_reviewer"


class OverrideOut(OverrideCreate):
    id: int
    line_item_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class VerdictOut(BaseModel):
    id: int
    status: VerdictStatus
    reasoning: str
    policy_doc_id: str | None
    policy_section: str | None
    policy_quote: str | None
    confidence: float
    agent_results: dict[str, Any] | None = None
    effective_status: VerdictStatus | None = None

    class Config:
        from_attributes = True


class LineItemOut(BaseModel):
    id: int
    vendor: str
    expense_date: str | None
    category: str
    description: str
    amount: float
    currency: str
    extraction_confidence: float
    ocr_confidence: float | None
    verdict: VerdictOut | None = None
    overrides: list[OverrideOut] = []

    class Config:
        from_attributes = True


class ReceiptOut(BaseModel):
    id: int
    filename: str
    mime_type: str
    extraction_json: dict[str, Any] | None
    line_items: list[LineItemOut] = []

    class Config:
        from_attributes = True


class SubmissionDetailOut(SubmissionOut):
    receipts: list[ReceiptOut] = []


class PolicyChatRequest(BaseModel):
    message: str


class PolicyCitation(BaseModel):
    doc_id: str
    section: str
    quote: str


class PolicyChatResponse(BaseModel):
    answer: str
    citations: list[PolicyCitation] = []
    refused: bool = False
    retrieval_confidence: float = 0.0
