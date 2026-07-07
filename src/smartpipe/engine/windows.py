"""Window bookkeeping for streaming ``reduce`` (stage-08, spec §4.2) — pure.

Semantics (pinned): the first window emits at item ``size`` (a full window);
thereafter one window per ``every`` new items, each containing the last ``size``
items. ``flush()`` returns whatever arrived after the last emission as a
``partial=True`` window — Ctrl+C never silently discards buffered lines. With
``every == size`` this is tumbling; smaller ``every`` slides. ``every > size``
would skip items, so the policy rejects it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Generic, TypeVar

__all__ = ["Window", "WindowBuffer", "WindowPolicy"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class WindowPolicy:
    size: int
    every: int  # == size → tumbling; < size → sliding

    def __post_init__(self) -> None:
        if self.size < 1:
            raise ValueError(f"window size must be >= 1, got {self.size}")
        if not 1 <= self.every <= self.size:
            raise ValueError(
                f"--every must be between 1 and the window size ({self.size}), got {self.every}"
            )


@dataclass(frozen=True, slots=True)
class Window(Generic[T]):
    items: tuple[T, ...]
    end_index: int  # 1-based ordinal of the last item, over the whole stream
    partial: bool


@dataclass(slots=True)
class WindowBuffer(Generic[T]):
    """Tiny mutable state machine (documented Spinner-style exception to the
    frozen-by-default rule): push items, get a Window when a boundary lands."""

    policy: WindowPolicy
    _buffer: deque[T] = field(init=False)
    _seen: int = 0
    _since_emit: int = 0
    _emitted_any: bool = False

    def __post_init__(self) -> None:
        self._buffer = deque(maxlen=self.policy.size)

    def push(self, item: T) -> Window[T] | None:
        self._buffer.append(item)
        self._seen += 1
        self._since_emit += 1
        boundary = (
            self._seen >= self.policy.size and self._since_emit >= self.policy.every
            if self._emitted_any
            else self._seen == self.policy.size
        )
        if not boundary:
            return None
        self._emitted_any = True
        self._since_emit = 0
        return Window(items=tuple(self._buffer), end_index=self._seen, partial=False)

    def flush(self) -> Window[T] | None:
        """The trailing partial window: everything that arrived after the last
        emission (or the whole short stream, if nothing ever emitted)."""
        if self._since_emit == 0 or self._seen == 0:
            return None
        tail = list(self._buffer)[-self._since_emit :] if self._emitted_any else list(self._buffer)
        return Window(items=tuple(tail), end_index=self._seen, partial=True)
