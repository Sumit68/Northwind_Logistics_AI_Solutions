"""Bounded asyncio parallelism for receipt extraction and policy review."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.config import settings

T = TypeVar("T")

_semaphore: asyncio.Semaphore | None = None


def _review_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        n = max(1, settings.review_max_concurrency)
        _semaphore = asyncio.Semaphore(n)
    return _semaphore


async def run_bounded(func: Callable[..., T], *args, **kwargs) -> T:
    """Run a blocking callable in a thread pool with a global concurrency cap."""
    async with _review_semaphore():
        return await asyncio.to_thread(func, *args, **kwargs)


async def gather_bounded(
    coros: list[Awaitable[T]],
) -> list[T]:
    """Run awaitables concurrently (each should use run_bounded for LLM work)."""
    return list(await asyncio.gather(*coros))
