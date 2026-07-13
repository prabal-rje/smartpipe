"""Compose the real resilient chat stack for verb-unit fakes.

Mirrors the composition root's chat wiring (``container._resilient_chat_core`` +
``_wrap_outer``): a fake's plain primary ``ChatModel`` wrapped in the run's
``Breaker`` + concurrency gate with the configured fallback armed LAZILY
underneath it, then the shared OUTER coalescer when the fake runs batched. It
returns the ``WiredChat`` seam the migrated verbs consume, so a fake's
``resilient_chat_model`` is one call to this — composing the fake's existing
``fallback_ref``/``fallback_chat_model`` without re-implementing the swap.

The layering is the load-bearing one (cache is out of scope for unit fakes):
coalescer → breaker+gate → adapter. The fallback is passed as the RAW adapter
factory (no coalescer of its own); the shared outer coalescer re-packs the
runner's replayed window onto whatever the breaker routes to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from smartpipe.io import diagnostics
from smartpipe.models.resilience import Breaker, Cooldown, ResilientChatModel, WiredChat

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable

    from smartpipe.engine.coalesce import BatchSettings
    from smartpipe.models.base import ChatModel, ModelRef

__all__ = ["build_wired"]


def build_wired(
    primary: ChatModel,
    *,
    concurrency: int,
    breaker_limit: int,
    fallback_factory: Callable[[], Awaitable[ChatModel]] | None = None,
    fallback_ref: ModelRef | None = None,
    batching: BatchSettings | None = None,
    stop: asyncio.Event | None = None,
) -> WiredChat:
    """Build the ``WiredChat`` a migrated verb runs on, exactly as the container
    composes it: ``primary`` budgeted-free (fakes carry no belt) inside the run's
    breaker + concurrency gate, the fallback armed lazily, then the shared outer
    coalescer when ``batching`` is on."""
    resilient = ResilientChatModel(
        primary,
        breaker=Breaker(limit=breaker_limit),
        concurrency=concurrency,
        cooldown=Cooldown(),
        fallback_factory=fallback_factory,
        fallback_ref=fallback_ref,
        announce=diagnostics.warn,
        note=diagnostics.note,
    )
    model: ChatModel = resilient
    if batching is not None:
        from smartpipe.models.coalesce import CoalescingChatModel

        model = CoalescingChatModel(resilient, settings=batching, stop=stop)
    return WiredChat(
        model=model,
        route=resilient.route,
        primary_ref=primary.ref,
        fallback_ref=fallback_ref,
    )
