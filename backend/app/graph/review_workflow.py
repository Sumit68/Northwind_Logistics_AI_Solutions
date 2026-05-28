"""Public entrypoint for per-receipt review (LangGraph in review_graph.py)."""

from __future__ import annotations

import asyncio

from app.graph.aggregation import aggregate_results
from app.graph.review_graph import get_review_graph
from app.graph.workflow_log import trace_entry
from app.services.policy_agent import run_policy_agent_async
from app.services.policy_router import route_policies
from app.services.review_parallel import run_bounded

__all__ = ["aggregate_results", "review_line_item", "review_line_item_async"]


def review_line_item(extraction: dict, trip_context: dict) -> dict:
    """
    Run the LangGraph review pipeline for one receipt line item.

    Steps 1–3 (upload, OCR/partition, LLM → JSON) happen in submissions router before this.
    """
    graph = get_review_graph()
    final = graph.invoke(
        {
            "extraction": extraction,
            "trip_context": trip_context,
            "agent_results": [],
            "workflow_trace": [],
        }
    )
    verdict = final.get("verdict") or aggregate_results([], extraction)
    trace = list(final.get("workflow_trace") or [])

    agent_results = dict(verdict.get("agent_results") or {})
    if trace:
        agent_results["_workflow_trace"] = trace
    verdict["agent_results"] = agent_results
    verdict["workflow_trace"] = trace
    return verdict


async def review_line_item_async(extraction: dict, trip_context: dict) -> dict:
    """
    Async review: classify once, then run all policy agents in parallel (asyncio.gather).
    Faster than sequential LangGraph invoke when many policies are routed.
    """
    route = await run_bounded(route_policies, extraction, trip_context)
    policy_ids = list(route.get("policy_ids") or ["TEP-007"])
    if route.get("routing_confidence", 1) < 0.6 and "TEP-007" not in policy_ids:
        policy_ids.append("TEP-007")
    policy_ids = list(dict.fromkeys(policy_ids))

    agent_tasks = [
        run_policy_agent_async(doc_id, extraction, trip_context) for doc_id in policy_ids
    ]
    results = await asyncio.gather(*agent_tasks, return_exceptions=True)

    agent_results: list[dict] = []
    for doc_id, result in zip(policy_ids, results):
        if isinstance(result, Exception):
            agent_results.append(
                {
                    "applicable": True,
                    "status": "needs_review",
                    "reasoning": f"Agent error for {doc_id}: {result}",
                    "policy_doc_id": doc_id,
                    "confidence": 0.4,
                }
            )
        else:
            agent_results.append(result)

    verdict = aggregate_results(agent_results, extraction)
    verdict["agent_results"] = {
        r.get("policy_doc_id", "unknown"): r for r in agent_results
    }
    return verdict


def build_extraction_trace(extraction: dict, filename: str) -> dict:
    """Pre-graph step logged for reviewers."""
    return trace_entry(
        "extract_receipt",
        {
            "filename": filename,
            "vendor": extraction.get("vendor"),
            "category_hint": extraction.get("category_hint"),
            "total": extraction.get("total"),
            "currency": extraction.get("currency"),
            "confidence": extraction.get("confidence"),
            "line_items_count": len(extraction.get("line_items") or []),
        },
    )
