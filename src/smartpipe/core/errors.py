"""Exit codes and the error taxonomy.

Contract: plan/decisions.md D12 and plan/architecture.md "Error taxonomy".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

__all__ = [
    "CircuitOpenTransport",
    "ExcludedError",
    "ExitCode",
    "ItemError",
    "LateSetupFault",
    "RetryableError",
    "SchemaRejected",
    "SempipeError",
    "SetupFault",
    "SourceCounts",
    "TooManyFailures",
    "TransportError",
    "UnsentError",
    "UsageFault",
    "is_recoverable_item_error",
]


class ExitCode(IntEnum):
    OK = 0
    PARTIAL = 1
    SETUP = 2
    ALL_FAILED = 3
    USAGE = 64
    BUG = 70
    INTERRUPTED = 130
    PIPE_CLOSED = 141  # 128 + SIGPIPE: downstream closed the pipe (| head) — die silently


class SempipeError(Exception):
    """Base of all *expected* smartpipe failures. Never raised directly."""


class UsageFault(SempipeError):
    """Bad flags/arguments/grammar misuse → exit 64."""


class SetupFault(SempipeError):
    """No model, unreachable endpoint, missing key/extra, broken config → exit 2.

    The message may be a full multi-line screen from plan/ux.md.
    """


class LateSetupFault(SetupFault):
    """A setup failure discovered only after a results-producing run began.

    Unlike a preflight :class:`SetupFault`, this carries the settled source
    ledger so the CLI can finalize an already-started manifest at exit 2.
    """

    def __init__(self, message: str, *, source_counts: SourceCounts) -> None:
        self.source_counts = source_counts
        super().__init__(message)


class SchemaRejected(SetupFault):
    """The endpoint rejected the request's response schema.

    A solo schema rejection is a setup fault, but a coalescer may recover when
    only its generated packed wrapper was rejected by retrying the original
    per-item schemas exactly once.
    """


class ItemError(SempipeError):
    """One item failed; the runner turns this into a skip-and-warn, never a crash."""


class UnsentError(ItemError):
    """An accepted item was skipped before any model request left the process."""


class ExcludedError(ItemError):
    """An accepted item was excluded before the primary model submission."""


class RetryableError(ItemError):
    """A bounded adapter retry policy was exhausted (for example HTTP 429).

    The run-scoped actual-call policy assigns both identifiers. ``series_id``
    groups one consecutive availability streak through its breaker trip;
    ``call_id`` identifies the single actual call whose failure may fan out to
    several coalesced item waiters.
    """

    def __init__(
        self,
        message: str,
        *,
        series_id: int | None = None,
        call_id: int | None = None,
    ) -> None:
        self.series_id = series_id
        self.call_id = call_id
        super().__init__(message)


class TransportError(RetryableError):
    """The wire failed, not the content: connect errors, timeouts, and 5xx that
    survived the retries. Still a per-item skip — but the runner's circuit
    breaker counts consecutive ones, because a dead provider fails every item
    identically and each failure costs a full retry ladder.

    ``series_id`` is assigned by the run-scoped outbound policy. It groups
    concurrent failures from one consecutive breaker streak so ordered
    emission can replay failures that completed before the trip marker.
    """

    def __init__(
        self,
        message: str,
        *,
        series_id: int | None = None,
        call_id: int | None = None,
    ) -> None:
        super().__init__(message, series_id=series_id, call_id=call_id)


class CircuitOpenTransport(TransportError):
    """The real-call breaker opened on this transport attempt.

    ``trip_id`` identifies one run-scoped breaker event. A packed flight may
    fan this marker to several item waiters, but the runner switches providers
    once for the shared event and replays each affected item exactly once.
    """

    def __init__(self, message: str, *, trip_id: int, call_id: int | None = None) -> None:
        self.trip_id = trip_id
        super().__init__(message, series_id=trip_id, call_id=call_id)


def is_recoverable_item_error(fault: ItemError) -> bool:
    """Whether a content/capability ladder may try an alternate representation.

    Availability exhaustion and explicitly unsent/excluded work are terminal
    for the current item. Treating either as a capability miss can launch a
    second paid call and hide the real outage or budget stop.
    """
    return not isinstance(fault, (RetryableError, UnsentError, ExcludedError))


@dataclass(frozen=True, slots=True)
class SourceCounts:
    """Source-item accounting carried independently of a halt's display units."""

    succeeded: int
    skipped: int
    failed: int

    def __post_init__(self) -> None:
        if self.succeeded < 0 or self.skipped < 0 or self.failed < 0:
            raise ValueError("source counts cannot be negative")
        if self.failed > self.skipped:
            raise ValueError("failed cannot exceed skipped")

    @property
    def total(self) -> int:
        return self.succeeded + self.skipped


class TooManyFailures(SempipeError):
    """The failure policy tripped (>50 % of emitted items failed) → exit 3.

    ``total`` stays the emitted-outcome denominator shown to the user.
    ``source_counts`` is independent because some policies (join's pair judge)
    display call units while manifests account source items. ``consumed`` is
    retained as the backward-compatible source-total view.
    """

    def __init__(
        self,
        failed: int,
        total: int,
        last_reason: str,
        *,
        consumed: int | None = None,
        source_counts: SourceCounts | None = None,
    ) -> None:
        if source_counts is not None:
            consumed_count = source_counts.total
        elif consumed is None:
            consumed_count = total
        else:
            consumed_count = consumed
        if source_counts is None and consumed_count < total:
            raise ValueError("consumed item count cannot be less than emitted total")
        if source_counts is not None and consumed is not None and consumed != source_counts.total:
            raise ValueError("consumed item count must equal the source-count total")
        self.failed = failed
        self.total = total
        self.consumed = consumed_count
        self.source_counts = source_counts
        self.last_reason = last_reason
        super().__init__(f"stopping — {failed} of {total} items failed the same way")
