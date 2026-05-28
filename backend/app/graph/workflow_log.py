"""Structured logs for each LangGraph node (reviewer-visible trace)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def trace_entry(node: str, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "node": node,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "output": output,
    }


def trace_step(node: str, **fields: Any) -> dict[str, list[dict[str, Any]]]:
    return {"workflow_trace": [trace_entry(node, fields)]}
