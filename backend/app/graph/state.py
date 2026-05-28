"""LangGraph state for per-receipt review."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class ReviewState(TypedDict, total=False):
    extraction: dict[str, Any]
    trip_context: dict[str, Any]
    route: dict[str, Any]
    policy_ids: list[str]
    agent_results: Annotated[list[dict[str, Any]], operator.add]
    workflow_trace: Annotated[list[dict[str, Any]], operator.add]
    verdict: dict[str, Any]


class PolicyAgentState(TypedDict, total=False):
    doc_id: str
    extraction: dict[str, Any]
    trip_context: dict[str, Any]
    agent_results: Annotated[list[dict[str, Any]], operator.add]
    workflow_trace: Annotated[list[dict[str, Any]], operator.add]
