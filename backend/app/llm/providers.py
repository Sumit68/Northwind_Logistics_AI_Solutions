"""Multi-provider LLM adapters: OpenRouter, OpenAI, Anthropic, Google, NVIDIA NIM."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import APIStatusError, OpenAI, RateLimitError

from app.config import settings
from app.llm.resolve import normalize_provider, resolve_llm_provider

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


def is_rate_limit_error(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code == 429:
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "rate_limit" in msg or "429" in msg


def parse_json_response(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    match = _JSON_FENCE.search(text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def _unique_models(*models: str) -> list[str]:
    out: list[str] = []
    for m in models:
        if m and m not in out:
            out.append(m)
    return out


def chat_model_chain(provider: str | None = None) -> list[str]:
    p = normalize_provider(provider) if provider else resolve_llm_provider()
    if p == "openai":
        return _unique_models(settings.openai_model, settings.openai_model_fallback)
    if p == "anthropic":
        return _unique_models(settings.anthropic_model, settings.anthropic_model_fallback)
    if p == "google":
        return _unique_models(settings.google_model, settings.google_model_fallback)
    if p == "nvidia":
        return _unique_models(settings.nvidia_model, settings.nvidia_model_fallback)
    return _unique_models(
        settings.openrouter_model,
        settings.openrouter_model_fallback,
        settings.openrouter_model_fallback_2,
    )


def _openai_compatible_complete(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    *,
    json_mode: bool = True,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system + "\nRespond with valid JSON only."},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception:
        if json_mode:
            kwargs.pop("response_format", None)
            resp = client.chat.completions.create(**kwargs)
        else:
            raise
    return parse_json_response(resp.choices[0].message.content or "{}")


def _complete_openrouter(model: str, system: str, user: str) -> dict[str, Any]:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
    )
    return _openai_compatible_complete(client, model, system, user)


def _complete_nvidia(model: str, system: str, user: str) -> dict[str, Any]:
    if not settings.nvidia_api_key:
        raise RuntimeError("NVIDIA_API_KEY is required when LLM_PROVIDER=nvidia")
    client = OpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
    )
    return _openai_compatible_complete(client, model, system, user, json_mode=False)


def _complete_openai(model: str, system: str, user: str) -> dict[str, Any]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
    client = OpenAI(api_key=settings.openai_api_key)
    return _openai_compatible_complete(client, model, system, user)


def _complete_anthropic(model: str, system: str, user: str) -> dict[str, Any]:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        system=system + "\nRespond with valid JSON only.",
        messages=[{"role": "user", "content": user}],
    )
    parts = []
    for block in msg.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return parse_json_response("".join(parts))


def _complete_google(model: str, system: str, user: str) -> dict[str, Any]:
    if not settings.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY is required when LLM_PROVIDER=google")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.google_api_key)
    resp = client.models.generate_content(
        model=model,
        contents=f"{system}\n\nRespond with valid JSON only.\n\n{user}",
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return parse_json_response(resp.text or "{}")


def _complete_with_model(provider: str, model: str, system: str, user: str) -> dict[str, Any]:
    if provider == "openai":
        return _complete_openai(model, system, user)
    if provider == "anthropic":
        return _complete_anthropic(model, system, user)
    if provider == "google":
        return _complete_google(model, system, user)
    if provider == "nvidia":
        return _complete_nvidia(model, system, user)
    return _complete_openrouter(model, system, user)


def complete_json_with_fallback(system: str, user: str) -> dict[str, Any]:
    provider = resolve_llm_provider()
    models = chat_model_chain(provider)
    if not models:
        raise RuntimeError(f"No chat models configured for provider {provider}")

    last_error: BaseException | None = None
    for i, model in enumerate(models):
        try:
            return _complete_with_model(provider, model, system, user)
        except Exception as exc:
            last_error = exc
            if is_rate_limit_error(exc) and i < len(models) - 1:
                logger.warning(
                    "[%s] rate limit on %s — trying %s",
                    provider,
                    model,
                    models[i + 1],
                )
                continue
            raise
    if last_error:
        raise last_error
    return {}


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Policy RAG embeddings — always local CPU (all-MiniLM-L6-v2 by default)."""
    from app.llm.embeddings import embed_texts as embed_local

    return embed_local(texts)
