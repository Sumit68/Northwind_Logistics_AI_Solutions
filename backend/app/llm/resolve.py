"""Resolve LLM provider from LLM_PROVIDER=auto and which API key is set."""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)

PROVIDER_ALIASES = {
    "gemini": "google",
    "claude": "anthropic",
    "gpt": "openai",
    "open-ai": "openai",
    "nemotron": "nvidia",
    "nim": "nvidia",
}

# Order when multiple keys are present (first wins; log a warning).
PROVIDER_DETECT_ORDER: list[tuple[str, str]] = [
    ("anthropic", "anthropic_api_key"),
    ("openai", "openai_api_key"),
    ("google", "google_api_key"),
    ("nvidia", "nvidia_api_key"),
    ("openrouter", "openrouter_api_key"),
]

_resolved: str | None = None


def normalize_provider(name: str) -> str:
    p = (name or "auto").strip().lower()
    return PROVIDER_ALIASES.get(p, p)


def _keys_present() -> list[str]:
    found: list[str] = []
    for provider, attr in PROVIDER_DETECT_ORDER:
        if (getattr(settings, attr, "") or "").strip():
            found.append(provider)
    return found


def resolve_llm_provider(*, force: bool = False) -> str:
    """Use LLM_PROVIDER if set (not auto); else pick provider from a single API key."""
    global _resolved
    if _resolved is not None and not force:
        return _resolved

    configured = normalize_provider(settings.llm_provider)
    if configured not in ("", "auto"):
        _resolved = configured
        return _resolved

    present = _keys_present()
    if not present:
        raise RuntimeError(
            "No LLM API key found. Set exactly one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "GOOGLE_API_KEY, NVIDIA_API_KEY, OPENROUTER_API_KEY"
        )

    if len(present) == 1:
        _resolved = present[0]
        logger.info("LLM provider (auto): %s", _resolved)
        return _resolved

    logger.warning(
        "Multiple LLM API keys set (%s); using first in priority order. "
        "Set LLM_PROVIDER to force one provider.",
        ", ".join(present),
    )
    for provider, _attr in PROVIDER_DETECT_ORDER:
        if provider in present:
            _resolved = provider
            logger.info("LLM provider (auto): %s", _resolved)
            return _resolved

    _resolved = present[0]
    return _resolved
