"""Unified LLM client — OpenRouter, OpenAI, Anthropic, Google, or NVIDIA NIM."""

from __future__ import annotations

from typing import Any

from app.llm.providers import complete_json_with_fallback, embed_texts
from app.llm.resolve import resolve_llm_provider


class LLMClient:
    """Reviewers set one API key (LLM_PROVIDER=auto); models are preconfigured per provider."""

    @property
    def provider(self) -> str:
        return resolve_llm_provider()

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        return complete_json_with_fallback(system, user)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return embed_texts(texts)


llm_client = LLMClient()
