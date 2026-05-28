import json
from pathlib import Path

from app.config import settings

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "policy_registry.json"
RULES_DIR = Path(__file__).resolve().parent.parent / "policy_rules"


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text())


def load_policy_rules(doc_id: str) -> dict | None:
    path = RULES_DIR / f"{doc_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def list_policy_rule_ids() -> list[str]:
    return sorted(p.stem for p in RULES_DIR.glob("TEP-*.json")) + sorted(
        p.stem for p in RULES_DIR.glob("SEC-*.json")
    )


def load_all_policy_chunks() -> list[dict]:
    """Legacy helper — prefer policy_indexer + DB for RAG."""
    from app.services.policy_indexer import extract_policy_corpus

    return [
        {"doc_id": c["doc_id"], "text": c["content"], "section": c.get("section", "")}
        for c in extract_policy_corpus()
    ]
