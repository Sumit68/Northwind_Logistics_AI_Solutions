import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class VerdictStatus(str, enum.Enum):
    compliant = "compliant"
    flagged = "flagged"
    rejected = "rejected"
    needs_review = "needs_review"


class SubmissionStatus(str, enum.Enum):
    draft = "draft"
    processing = "processing"
    reviewed = "reviewed"
    failed = "failed"


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    grade: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(128))
    department: Mapped[str] = mapped_column(String(128))
    manager_id: Mapped[str] = mapped_column(String(32))
    home_base: Mapped[str] = mapped_column(String(128))
    trip_purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    trip_dates: Mapped[str | None] = mapped_column(String(64), nullable=True)

    submissions: Mapped[list["Submission"]] = relationship(back_populates="employee")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    trip_purpose: Mapped[str] = mapped_column(Text)
    trip_dates: Mapped[str] = mapped_column(String(64))
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus), default=SubmissionStatus.draft
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    employee: Mapped["Employee"] = relationship(back_populates="submissions")
    receipts: Mapped[list["Receipt"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    filename: Mapped[str] = mapped_column(String(256))
    mime_type: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(String(512))
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    submission: Mapped["Submission"] = relationship(back_populates="receipts")
    line_items: Mapped[list["LineItem"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"))
    vendor: Mapped[str] = mapped_column(String(256))
    expense_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    receipt: Mapped["Receipt"] = relationship(back_populates="line_items")
    verdict: Mapped["Verdict | None"] = relationship(
        back_populates="line_item", uselist=False, cascade="all, delete-orphan"
    )
    overrides: Mapped[list["Override"]] = relationship(
        back_populates="line_item", cascade="all, delete-orphan"
    )


class Verdict(Base):
    __tablename__ = "verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_item_id: Mapped[int] = mapped_column(ForeignKey("line_items.id"), unique=True)
    status: Mapped[VerdictStatus] = mapped_column(Enum(VerdictStatus))
    reasoning: Mapped[str] = mapped_column(Text)
    policy_doc_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_section: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    agent_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    line_item: Mapped["LineItem"] = relationship(back_populates="verdict")


class Override(Base):
    __tablename__ = "overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_item_id: Mapped[int] = mapped_column(ForeignKey("line_items.id"))
    new_status: Mapped[VerdictStatus] = mapped_column(Enum(VerdictStatus))
    comment: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(128), default="finance_reviewer")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    line_item: Mapped["LineItem"] = relationship(back_populates="overrides")
