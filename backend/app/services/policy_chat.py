"""Policy Q&A via Weaviate RAG — capability #6 in candidate brief."""

import json

from app.config import settings
from app.llm.client import llm_client
from app.services.policy_indexer import search_policy_chunks

CHAT_SYSTEM = """You are a policy librarian for Northwind Logistics travel and expense policies ONLY.
Answer ONLY using the provided policy excerpts. Return JSON:
{
  "refused": boolean,
  "answer": string,
  "citations": [{"doc_id": "TEP-002", "section": "§2", "quote": "verbatim excerpt"}],
  "retrieval_confidence": number
}
Set refused=true if the question is about HR/payroll/unrelated topics OR excerpts do not contain the answer.
Do not fabricate policy text — quotes must be copied from excerpts."""


OFF_TOPIC_KEYWORDS = [
    "payroll",
    "salary",
    "pto",
    "vacation",
    "benefits enrollment",
    "stock option",
    "performance review",
]


def policy_chat(message: str) -> dict:
    msg_l = message.lower()
    if any(k in msg_l for k in OFF_TOPIC_KEYWORDS):
        return {
            "refused": True,
            "answer": "I can only answer questions about the Northwind travel and expense policy library (TEP/SEC travel policies).",
            "citations": [],
            "retrieval_confidence": 0.0,
        }

    hits = search_policy_chunks(message)
    if not hits:
        return {
            "refused": True,
            "answer": "I could not find relevant policy content for that question.",
            "citations": [],
            "retrieval_confidence": 0.0,
        }

    best_score = hits[0][1]
    if best_score < settings.policy_rag_refuse_score:
        return {
            "refused": True,
            "answer": "I don't have sufficient policy library evidence to answer that confidently.",
            "citations": [],
            "retrieval_confidence": best_score,
        }

    context_parts = []
    for chunk, score in hits:
        retrieval = getattr(chunk, "retrieval", "hybrid")
        context_parts.append(
            f"[{chunk.doc_id} {chunk.section} score={score:.2f} retrieval={retrieval}]\n{chunk.content}"
        )
    context = "\n\n---\n\n".join(context_parts)

    user = json.dumps({"question": message, "policy_excerpts": context})
    result = llm_client.complete_json(CHAT_SYSTEM, user)
    result["retrieval_confidence"] = max(best_score, float(result.get("retrieval_confidence", 0)))

    if best_score < settings.policy_rag_min_score:
        result["refused"] = True
        result["answer"] = (
            result.get("answer")
            or "Retrieval confidence is below threshold; cannot provide a grounded answer."
        )

    citations = result.get("citations") or []
    verified = []
    for c in citations:
        quote = (c.get("quote") or "").strip()
        if quote and any(quote in ch.content for ch, _ in hits):
            verified.append(c)
    result["citations"] = verified

    return result
