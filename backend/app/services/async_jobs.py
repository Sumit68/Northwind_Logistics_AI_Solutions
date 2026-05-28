"""In-memory async job store for long-running API work (Cloudflare tunnel–safe)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Callable

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    processing = "processing"
    completed = "completed"
    failed = "failed"


@dataclass
class JobRecord:
    status: JobStatus
    result: dict[str, Any] | None = None
    error: str | None = None


_jobs: dict[str, JobRecord] = {}
_lock = Lock()


def create_job() -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = JobRecord(status=JobStatus.processing)
    return job_id


def get_job(job_id: str) -> JobRecord | None:
    with _lock:
        return _jobs.get(job_id)


def complete_job(job_id: str, result: dict[str, Any]) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id] = JobRecord(status=JobStatus.completed, result=result)


def fail_job(job_id: str, error: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id] = JobRecord(status=JobStatus.failed, error=error)


async def run_in_background(job_id: str, fn: Callable[[], dict[str, Any]]) -> None:
    try:
        result = await asyncio.to_thread(fn)
        complete_job(job_id, result)
    except Exception as exc:
        logger.exception("Background job %s failed", job_id)
        fail_job(job_id, str(exc))
