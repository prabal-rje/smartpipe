"""Run-scoped source accounting, independent of emitted work units.

Readers may reject a named source before an :class:`~smartpipe.io.items.Item`
exists, and one OCR source may emit several page work units.  This module owns
those two facts without leaking them into the pure execution engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import SourceCounts

if TYPE_CHECKING:
    from smartpipe.io.items import ItemSource

__all__ = [
    "SourceCounter",
    "SourceGroup",
    "add_counts",
    "discard",
    "new_group",
    "pending_ingestion",
    "record_ingestion_skip",
    "record_local",
    "reset",
    "settle",
]


@dataclass(frozen=True, slots=True)
class SourceGroup:
    """Several emitted work units that belong to one accepted source."""

    run_id: int
    size: int

    def __post_init__(self) -> None:
        if self.run_id < 0:
            raise ValueError("source group id cannot be negative")
        if self.size < 1:
            raise ValueError("source group size must be positive")


@dataclass(slots=True)
class _RunState:
    active: bool = False
    next_group: int = 0
    dropped_skipped: int = 0
    dropped_failed: int = 0
    local: SourceCounts | None = None


_state = _RunState()


def reset() -> None:
    """Start a fresh invocation; called once by the composition root."""
    global _state
    _state = _RunState(active=True)


def discard() -> None:
    """Forget an aborted invocation without leaking counts into the next one."""
    global _state
    _state = _RunState()


def new_group(*, size: int) -> SourceGroup:
    """Allocate one run-local owner for ``size`` emitted stage units."""
    group = SourceGroup(_state.next_group, size)
    _state.next_group += 1
    return group


def record_ingestion_skip(*, failed: bool) -> None:
    """Record a named source rejected before it could yield an item."""
    if not _state.active:
        return
    _state.dropped_skipped += 1
    _state.dropped_failed += int(failed)


def record_local(counts: SourceCounts) -> None:
    """Remember the verb's last local outcome; end-of-run reporting wins."""
    if _state.active:
        _state.local = counts


def pending_ingestion() -> SourceCounts:
    """Snapshot drops before ``settle`` drains them."""
    return SourceCounts(
        succeeded=0,
        skipped=_state.dropped_skipped,
        failed=_state.dropped_failed,
    )


def settle(base: SourceCounts | None = None) -> SourceCounts | None:
    """Merge pending ingestion drops exactly once and close the run ledger."""
    if not _state.active:
        return base
    local = base if base is not None else _state.local
    dropped = pending_ingestion()
    discard()
    if local is None:
        return dropped if dropped.total else None
    return add_counts(local, dropped)


def add_counts(*counts: SourceCounts) -> SourceCounts:
    """Combine independent source ledgers while preserving failure subset law."""
    return SourceCounts(
        succeeded=sum(count.succeeded for count in counts),
        skipped=sum(count.skipped for count in counts),
        failed=sum(count.failed for count in counts),
    )


@dataclass(slots=True)
class _GroupProgress:
    size: int
    seen: int = 0
    skipped: bool = False
    failed: bool = False


@dataclass(slots=True)
class SourceCounter:
    """Fold item outcomes into source outcomes, collapsing OCR page groups."""

    _succeeded: int = 0
    _skipped: int = 0
    _failed: int = 0
    _groups: dict[int, _GroupProgress] | None = None

    def done(self, source: ItemSource) -> None:
        progress = self._progress(source)
        if progress is None:
            self._succeeded += 1

    def skip(self, source: ItemSource, *, failed: bool) -> None:
        progress = self._progress(source)
        if progress is None:
            self._skipped += 1
            self._failed += int(failed)
            return
        progress.skipped = True
        progress.failed = progress.failed or failed

    def _progress(self, source: ItemSource) -> _GroupProgress | None:
        group = source.group
        if group is None:
            return None
        if self._groups is None:
            self._groups = {}
        progress = self._groups.setdefault(group.run_id, _GroupProgress(size=group.size))
        if progress.size != group.size:
            raise ValueError("one source group cannot have two sizes")
        progress.seen += 1
        if progress.seen > progress.size:
            raise ValueError("source group emitted more work units than declared")
        return progress

    @property
    def counts(self) -> SourceCounts:
        groups = () if self._groups is None else self._groups.values()
        group_successes = sum(
            progress.seen == progress.size and not progress.skipped for progress in groups
        )
        groups = () if self._groups is None else self._groups.values()
        group_skips = sum(progress.seen != progress.size or progress.skipped for progress in groups)
        groups = () if self._groups is None else self._groups.values()
        group_failures = sum(progress.failed for progress in groups)
        return SourceCounts(
            succeeded=self._succeeded + group_successes,
            skipped=self._skipped + group_skips,
            failed=self._failed + group_failures,
        )
