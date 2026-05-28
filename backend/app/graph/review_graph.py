"""LangGraph review workflow: classify → parallel policy agents → aggregate."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.graph.aggregation import aggregate_results
from app.graph.state import PolicyAgentState, ReviewState
from app.graph.workflow_log import trace_step
from app.services.policy_agent import run_policy_agent
from app.services.policy_router import route_policies

_compiled_graph = None


def classify_node(state: ReviewState) -> dict:
    """Step 4: LLM classifier chooses which policy agents to invoke."""
    route = route_policies(state["extraction"], state.get("trip_context"))
    policy_ids = list(route.get("policy_ids") or ["TEP-007"])
    if route.get("routing_confidence", 1) < 0.6 and "TEP-007" not in policy_ids:
        policy_ids.append("TEP-007")
    policy_ids = list(dict.fromkeys(policy_ids))
    log = trace_step(
        "classify",
        policy_ids=policy_ids,
        expense_category=route.get("expense_category"),
        routing_confidence=route.get("routing_confidence"),
        reasoning=route.get("reasoning"),
    )
    return {
        "route": route,
        "policy_ids": policy_ids,
        **log,
    }


def dispatch_policy_agents(state: ReviewState) -> list[Send]:
    """Fan-out: one graph invocation per selected policy agent."""
    return [
        Send(
            "policy_agent",
            {
                "doc_id": doc_id,
                "extraction": state["extraction"],
                "trip_context": state.get("trip_context") or {},
            },
        )
        for doc_id in state.get("policy_ids") or ["TEP-007"]
    ]


def policy_agent_node(state: PolicyAgentState) -> dict:
    """Step 5: deterministic rules + LLM with policy_rules/{doc_id}.json."""
    doc_id = state["doc_id"]
    try:
        result = run_policy_agent(
            doc_id,
            state["extraction"],
            state.get("trip_context") or {},
        )
    except Exception as exc:
        result = {
            "applicable": True,
            "status": "needs_review",
            "reasoning": f"Agent error for {doc_id}: {exc}",
            "policy_doc_id": doc_id,
            "confidence": 0.4,
        }
    log = trace_step(
        f"policy_agent:{doc_id}",
        status=result.get("status"),
        applicable=result.get("applicable"),
        deterministic=result.get("deterministic", False),
        policy_doc_id=result.get("policy_doc_id"),
        reasoning=(result.get("reasoning") or "")[:500],
        confidence=result.get("confidence"),
    )
    return {"agent_results": [result], **log}


def aggregate_node(state: ReviewState) -> dict:
    """Combine agent outputs into a single line-item verdict."""
    results = state.get("agent_results") or []
    verdict = aggregate_results(results, state["extraction"])
    status = verdict.get("status")
    status_str = status.value if hasattr(status, "value") else str(status)
    log = trace_step(
        "aggregate",
        status=status_str,
        policy_doc_id=verdict.get("policy_doc_id"),
        confidence=verdict.get("confidence"),
        agents_invoked=len(results),
    )
    return {"verdict": verdict, **log}


def build_review_graph():
    builder = StateGraph(ReviewState)
    builder.add_node("classify", classify_node)
    builder.add_node("policy_agent", policy_agent_node)
    builder.add_node("aggregate", aggregate_node)

    builder.add_edge(START, "classify")
    builder.add_conditional_edges("classify", dispatch_policy_agents, ["policy_agent"])
    builder.add_edge("policy_agent", "aggregate")
    builder.add_edge("aggregate", END)
    return builder.compile()


def get_review_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_review_graph()
    return _compiled_graph
