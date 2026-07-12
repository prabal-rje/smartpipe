"""The coalescer's async plumbing (item 62): windows, flights, salvage, stop."""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import (
    CircuitOpenTransport,
    ItemError,
    RetryableError,
    SchemaRejected,
    SetupFault,
    TransportError,
    UnsentError,
)
from smartpipe.engine.coalesce import BatchSettings
from smartpipe.engine.schema import shorthand_to_schema
from smartpipe.models.base import BatchHint, CompletionRequest, ImageData, ModelRef
from smartpipe.models.budget import CallBudget, budgeted_chat
from smartpipe.models.coalesce import (
    STOPPED_BEFORE_SEND,
    CoalescingChatModel,
    OutboundCallPolicy,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_SCHEMA = shorthand_to_schema(["vendor"])
_BLOCK = re.compile(r'<input id="(r\d+)">\n(.*?)\n</input>', re.DOTALL)


def _request(body: str, *, instruction: str = "Extract vendor") -> CompletionRequest:
    payload = f"<input>\n{body}\n</input>"
    return CompletionRequest(
        system="extract",
        user=f"{instruction}\n\n{payload}",
        json_schema=_SCHEMA,
        batch=BatchHint(instruction, payload),
    )


class PackedFake:
    """Answers packed requests per labeled block; solo requests from the body."""

    def __init__(self, *, mangle: Mapping[str, object] | None = None) -> None:
        self.ref = ModelRef("ollama", "fake")
        self.calls: list[CompletionRequest] = []
        self.mangle = dict(mangle or {})  # label -> replacement value (or "__DROP__")

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        blocks = _BLOCK.findall(request.user)
        if not blocks:  # a solo request: the body is the fenced line of the user text
            lines = request.user.split("\n")
            body = lines[-2] if len(lines) >= 2 else request.user
            return json.dumps({"vendor": f"solo:{body}"})
        answers: dict[str, object] = {}
        for label, body in blocks:
            if label in self.mangle:
                if self.mangle[label] == "__DROP__":
                    continue
                answers[label] = self.mangle[label]
            else:
                answers[label] = {"vendor": f"batched:{body}"}
        return json.dumps(answers)


def _coalescer(
    inner: object,
    *,
    size: int = 4,
    window: float = 0.005,
    stop: asyncio.Event | None = None,
    concurrency: int = 4,
    breaker_limit: int = 5,
) -> CoalescingChatModel:
    from smartpipe.models.base import ChatModel

    model: ChatModel = inner  # type: ignore[assignment]
    return CoalescingChatModel(
        model,
        settings=BatchSettings(size=size, window_seconds=window),
        stop=stop,
        calls=OutboundCallPolicy(concurrency=concurrency, breaker_limit=breaker_limit),
    )


async def test_cloud_model_with_large_measured_gap_withholds_packing() -> None:
    inner = PackedFake()
    inner.ref = ModelRef("ollama", "glm-5.2:cloud")
    model = _coalescer(inner, size=4)
    replies = await asyncio.gather(*(model.complete(_request(f"row{n}")) for n in range(4)))
    assert len(inner.calls) == 4
    assert all(json.loads(reply)["vendor"].startswith("solo:") for reply in replies)
    assert model.packed_calls == 0
    assert model.packed_items == 0


async def test_k_reached_flies_one_packed_call() -> None:
    inner = PackedFake()
    model = _coalescer(inner, size=4)
    replies = await asyncio.gather(*(model.complete(_request(f"row{n}")) for n in range(4)))
    assert len(inner.calls) == 1
    assert [json.loads(reply)["vendor"] for reply in replies] == [
        "batched:row0",
        "batched:row1",
        "batched:row2",
        "batched:row3",
    ]
    assert model.packed_calls == 1
    assert model.packed_items == 4


async def test_window_flushes_a_partial_group() -> None:
    inner = PackedFake()
    model = _coalescer(inner, size=10, window=0.005)
    replies = await asyncio.gather(*(model.complete(_request(f"row{n}")) for n in range(2)))
    assert len(inner.calls) == 1  # fewer than K: the window flew them together
    assert json.loads(replies[0])["vendor"] == "batched:row0"


async def test_streams_stay_live_across_windows() -> None:
    inner = PackedFake()
    model = _coalescer(inner, size=10, window=0.005)
    first = await model.complete(_request("early"))
    await asyncio.sleep(0.02)
    second = await model.complete(_request("late"))
    assert len(inner.calls) == 2  # two windows, two flights — no indefinite wait
    assert json.loads(first)["vendor"] == "solo:early"
    assert json.loads(second)["vendor"] == "solo:late"


async def test_a_group_of_one_flies_as_the_original_request() -> None:
    inner = PackedFake()
    model = _coalescer(inner, size=10, window=0.005)
    await model.complete(_request("alone"))
    (call,) = inner.calls
    assert call.user == _request("alone").user  # not packed, byte-identical
    assert model.packed_calls == 0  # a solo flight is not batching


async def test_ineligible_requests_pass_straight_through() -> None:
    inner = PackedFake()
    model = _coalescer(inner)
    plain = CompletionRequest(system="s", user="just ask", json_schema=_SCHEMA)
    await model.complete(plain)
    assert inner.calls == [plain]


async def test_media_requests_pass_straight_through() -> None:
    inner = PackedFake()
    model = _coalescer(inner)
    seeing = CompletionRequest(
        system="s",
        user="look",
        json_schema=_SCHEMA,
        media=(ImageData(data=b"png", mime="image/png"),),
        batch=BatchHint("look", "<input>\nx\n</input>"),
    )
    await model.complete(seeing)
    assert inner.calls == [seeing]


async def test_different_shapes_form_different_groups() -> None:
    inner = PackedFake()
    model = _coalescer(inner, size=2)
    other_schema = shorthand_to_schema(["total"])
    other = CompletionRequest(
        system="extract",
        user="Extract total\n\n<input>\nx\n</input>",
        json_schema=other_schema,
        batch=BatchHint("Extract total", "<input>\nx\n</input>"),
    )
    await asyncio.gather(
        model.complete(_request("a")),
        model.complete(_request("b")),
        model.complete(other),
    )
    assert len(inner.calls) == 2  # one packed pair + one solo straggler


async def test_token_budget_splits_a_group() -> None:
    from smartpipe.models.base import ChatModel

    inner = PackedFake()
    typed: ChatModel = inner  # type: ignore[assignment]
    model = CoalescingChatModel(
        typed,
        settings=BatchSettings(size=10, window_seconds=0.005),
        budget_tokens=100,  # pinned via the seam — no 20k-char payloads needed
        calls=OutboundCallPolicy(concurrency=4),
    )
    wide = "x" * 150  # ≈46 tokens per submission: two fit, the third overflows
    await asyncio.gather(*(model.complete(_request(wide + str(n))) for n in range(3)))
    assert len(inner.calls) == 2  # one packed pair + the overflow straggler solo


async def test_missing_key_reruns_only_the_named_item() -> None:
    inner = PackedFake(mangle={"r2": "__DROP__"})
    model = _coalescer(inner, size=3)
    replies = await asyncio.gather(*(model.complete(_request(f"row{n}")) for n in range(3)))
    assert len(inner.calls) == 2  # the packed call + ONE solo re-run
    solo = inner.calls[1]
    assert "row1" in solo.user  # the named item, nobody else
    assert json.loads(replies[0])["vendor"] == "batched:row0"
    assert json.loads(replies[1])["vendor"] == "solo:row1"
    assert json.loads(replies[2])["vendor"] == "batched:row2"
    assert model.packed_items == 3  # all members attempted on the packed wire
    assert model.solo_recoveries == 1


async def test_invalid_key_salvages_the_valid_ones() -> None:
    inner = PackedFake(mangle={"r1": {"wrong": True}})
    model = _coalescer(inner, size=2)
    replies = await asyncio.gather(model.complete(_request("a")), model.complete(_request("b")))
    assert len(inner.calls) == 2
    assert json.loads(replies[0])["vendor"] == "solo:a"
    assert json.loads(replies[1])["vendor"] == "batched:b"


class GarbageFake:
    """First call answers garbage; solo re-runs answer properly."""

    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        if len(self.calls) == 1:
            return "not json at all"
        body = request.user.rsplit("\n", 2)[-2]
        return json.dumps({"vendor": f"solo:{body}"})


async def test_unreadable_packed_reply_reruns_everyone_solo() -> None:
    inner = GarbageFake()
    model = _coalescer(inner, size=3)
    replies = await asyncio.gather(*(model.complete(_request(f"row{n}")) for n in range(3)))
    assert len(inner.calls) == 4  # 1 packed + 3 solos
    assert [json.loads(reply)["vendor"] for reply in replies] == [
        "solo:row0",
        "solo:row1",
        "solo:row2",
    ]
    assert model.packed_calls == 1
    assert model.packed_items == 3
    assert model.solo_recoveries == 3


async def test_packed_schema_rejection_reruns_every_member_solo_once() -> None:
    class PackedShapeRejected(PackedFake):
        async def complete(self, request: CompletionRequest) -> str:
            if 'id="r' in request.user:
                self.calls.append(request)
                raise SchemaRejected("packed response schema rejected")
            return await super().complete(request)

    inner = PackedShapeRejected()
    model = _coalescer(inner, size=3, concurrency=2)
    replies = await asyncio.gather(*(model.complete(_request(f"row{n}")) for n in range(3)))
    assert len(inner.calls) == 4  # one rejected pack + one solo per member, exactly once
    assert [json.loads(reply)["vendor"] for reply in replies] == [
        "solo:row0",
        "solo:row1",
        "solo:row2",
    ]
    assert model.packed_calls == 1
    assert model.solo_recoveries == 3


class DownFake:
    """Every call fails at the wire."""

    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake")
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> str:
        self.calls += 1
        raise TransportError("connection refused")


async def test_failed_packed_transport_fans_out_without_amplification() -> None:
    inner = DownFake()
    model = _coalescer(inner, size=3)
    outcomes = await asyncio.gather(
        *(model.complete(_request(f"row{n}")) for n in range(3)), return_exceptions=True
    )
    assert inner.calls == 1  # the packed call's bounded ladder is the one real failure
    assert all(isinstance(outcome, TransportError) for outcome in outcomes)
    assert model.packed_calls == 1  # failed attempts remain visible in the receipt
    assert model.packed_items == 3
    assert model.solo_recoveries == 0


async def test_exhausted_packed_429_fans_out_without_solo_retry_ladders() -> None:
    class RateLimited:
        ref = ModelRef("openai", "limited")

        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request: CompletionRequest) -> str:
            self.calls += 1
            raise RetryableError("openai error 429: slow down")

    inner = RateLimited()
    model = _coalescer(inner, size=3)
    outcomes = await asyncio.gather(
        *(model.complete(_request(f"row{n}")) for n in range(3)), return_exceptions=True
    )

    assert inner.calls == 1
    assert all(isinstance(outcome, RetryableError) for outcome in outcomes)
    assert {outcome.call_id for outcome in outcomes if isinstance(outcome, RetryableError)} == {1}
    assert model.packed_calls == 1
    assert model.solo_recoveries == 0


class FatalFake:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake")

    async def complete(self, request: CompletionRequest) -> str:
        raise SetupFault("error: key rejected")


async def test_fatal_wire_faults_reach_every_waiter() -> None:
    model = _coalescer(FatalFake(), size=2)
    outcomes = await asyncio.gather(
        model.complete(_request("a")), model.complete(_request("b")), return_exceptions=True
    )
    assert all(isinstance(outcome, SetupFault) for outcome in outcomes)


async def test_solo_rerun_failure_reaches_its_waiter() -> None:
    class HalfDown(PackedFake):
        async def complete(self, request: CompletionRequest) -> str:
            if "id=" not in request.user:
                raise ItemError("model declined")
            return await super().complete(request)

    inner = HalfDown(mangle={"r1": "__DROP__"})
    model = _coalescer(inner, size=2)
    first, second = await asyncio.gather(
        model.complete(_request("a")), model.complete(_request("b")), return_exceptions=True
    )
    assert isinstance(first, ItemError)
    assert isinstance(second, str)


async def test_max_calls_counts_batches_not_items() -> None:
    inner = PackedFake()
    budget = CallBudget(limit=1, stop=asyncio.Event())
    model = _coalescer(budgeted_chat(inner, budget), size=4)  # type: ignore[arg-type]
    replies = await asyncio.gather(*(model.complete(_request(f"row{n}")) for n in range(4)))
    assert budget.calls == 1  # a batch of four items IS one call
    assert len(replies) == 4


async def test_exhausted_budget_names_the_belt_on_every_member() -> None:
    inner = PackedFake()
    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    budget.charge()  # the belt is already spent
    stop.clear()  # the coalescer must not confuse the budget stop with Ctrl-C here
    model = _coalescer(budgeted_chat(inner, budget), size=2)  # type: ignore[arg-type]
    outcomes = await asyncio.gather(
        model.complete(_request("a")), model.complete(_request("b")), return_exceptions=True
    )
    assert all(isinstance(outcome, ItemError) for outcome in outcomes)
    assert all("call budget" in str(outcome) for outcome in outcomes)
    assert inner.calls == []  # the belt blocked the wire before a byte was sent


async def test_stop_before_submit_never_enqueues() -> None:
    inner = PackedFake()
    stop = asyncio.Event()
    stop.set()
    model = _coalescer(inner, stop=stop)
    with pytest.raises(UnsentError, match="not sent"):
        await model.complete(_request("a"))
    assert inner.calls == []


async def test_stop_during_the_window_fails_queued_items_without_a_call() -> None:
    inner = PackedFake()
    stop = asyncio.Event()
    gate = asyncio.Event()

    async def held_sleep(_seconds: float) -> None:
        await gate.wait()

    from smartpipe.models.base import ChatModel

    typed: ChatModel = inner  # type: ignore[assignment]
    model = CoalescingChatModel(
        typed,
        settings=BatchSettings(size=10, window_seconds=1.0),
        stop=stop,
        sleep=held_sleep,
        calls=OutboundCallPolicy(concurrency=4),
    )
    waiters = [asyncio.create_task(model.complete(_request(f"row{n}"))) for n in range(2)]
    await asyncio.sleep(0)  # let both enqueue
    stop.set()  # Ctrl-C lands while the window is open
    gate.set()  # the window elapses into a stopped run
    outcomes = await asyncio.gather(*waiters, return_exceptions=True)
    assert all(
        isinstance(outcome, ItemError) and STOPPED_BEFORE_SEND in str(outcome)
        for outcome in outcomes
    )
    assert inner.calls == []  # queued-but-unflown spends nothing


async def test_stop_during_salvage_stops_the_solo_reruns() -> None:
    stop = asyncio.Event()

    class StopAfterPacked(PackedFake):
        async def complete(self, request: CompletionRequest) -> str:
            reply = await super().complete(request)
            stop.set()  # the interrupt lands right as the packed reply arrives
            return reply

    inner = StopAfterPacked(mangle={"r1": "__DROP__"})
    model = _coalescer(inner, size=2, stop=stop)
    first, second = await asyncio.gather(
        model.complete(_request("a")), model.complete(_request("b")), return_exceptions=True
    )
    assert isinstance(first, ItemError)  # the named re-run obeys the stop
    assert isinstance(second, str)  # the salvaged answer still lands
    assert len(inner.calls) == 1


async def test_a_late_timer_never_redispatches_a_flown_group() -> None:
    # a sleep that swallows its cancellation: the timer wakes AFTER its group
    # already flew by K — the group-identity check must make it a no-op
    async def stubborn_sleep(_seconds: float) -> None:
        import contextlib

        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.Event().wait()

    from smartpipe.models.base import ChatModel

    inner = PackedFake()
    typed: ChatModel = inner  # type: ignore[assignment]
    model = CoalescingChatModel(
        typed,
        settings=BatchSettings(size=2, window_seconds=9.0),
        sleep=stubborn_sleep,
        calls=OutboundCallPolicy(concurrency=4),
    )
    first = asyncio.create_task(model.complete(_request("a")))
    for _ in range(3):
        await asyncio.sleep(0)  # the timer task enters its (stubborn) sleep
    second = asyncio.create_task(model.complete(_request("b")))  # K reached — flies now
    replies = await asyncio.gather(first, second)
    for _ in range(3):
        await asyncio.sleep(0)  # the cancelled-but-stubborn timer wakes and looks
    assert len(inner.calls) == 1  # the late timer found its group gone and did nothing
    assert all(isinstance(reply, str) for reply in replies)


def _flight_tasks() -> list[asyncio.Task[object]]:
    return [
        task
        for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "__qualname__", "").endswith("._fly")
    ]


async def test_teardown_cancel_mid_packed_call_frees_the_waiters() -> None:
    started = asyncio.Event()

    class Hanging:
        def __init__(self) -> None:
            self.ref = ModelRef("ollama", "fake")

        async def complete(self, request: CompletionRequest) -> str:
            started.set()
            await asyncio.Event().wait()  # hangs until cancelled
            raise AssertionError("unreachable")

    model = _coalescer(Hanging(), size=2)
    waiters = [asyncio.create_task(model.complete(_request(f"row{n}"))) for n in range(2)]
    await started.wait()
    for flight in _flight_tasks():
        flight.cancel()  # loop teardown cancels the coalescer's own tasks
    outcomes = await asyncio.gather(*waiters, return_exceptions=True)
    assert all(
        isinstance(outcome, ItemError) and STOPPED_BEFORE_SEND in str(outcome)
        for outcome in outcomes
    )


async def test_teardown_cancel_mid_solo_flight_frees_the_waiter() -> None:
    started = asyncio.Event()

    class Hanging:
        def __init__(self) -> None:
            self.ref = ModelRef("ollama", "fake")

        async def complete(self, request: CompletionRequest) -> str:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    model = _coalescer(Hanging(), size=10, window=0.005)  # a group of one flies solo
    waiter = asyncio.create_task(model.complete(_request("alone")))
    await started.wait()
    for flight in _flight_tasks():
        flight.cancel()
    with pytest.raises(ItemError, match="not sent"):
        await waiter


async def test_a_cancelled_waiter_never_breaks_its_flightmates() -> None:
    gate = asyncio.Event()

    class Gated(PackedFake):
        async def complete(self, request: CompletionRequest) -> str:
            await gate.wait()  # the flight is on the wire when one waiter dies
            return await super().complete(request)

    model = _coalescer(Gated(), size=2)
    doomed = asyncio.create_task(model.complete(_request("a")))
    kept = asyncio.create_task(model.complete(_request("b")))
    for _ in range(2):
        await asyncio.sleep(0)
    doomed.cancel()  # its future is done (cancelled) when the fan-out arrives
    gate.set()
    reply = await kept
    assert json.loads(reply)["vendor"] == "batched:b"  # the flightmate is untouched
    with pytest.raises(asyncio.CancelledError):
        await doomed


async def test_a_cancelled_waiter_never_breaks_the_fatal_fanout() -> None:
    gate = asyncio.Event()

    class GatedFatal:
        def __init__(self) -> None:
            self.ref = ModelRef("ollama", "fake")

        async def complete(self, request: CompletionRequest) -> str:
            await gate.wait()
            raise SetupFault("error: key rejected")

    model = _coalescer(GatedFatal(), size=2)
    doomed = asyncio.create_task(model.complete(_request("a")))
    kept = asyncio.create_task(model.complete(_request("b")))
    for _ in range(2):
        await asyncio.sleep(0)
    doomed.cancel()
    gate.set()
    with pytest.raises(SetupFault):
        await kept
    with pytest.raises(asyncio.CancelledError):
        await doomed


async def test_all_cancelled_waiters_are_removed_before_the_window_flies() -> None:
    inner = PackedFake()
    model = _coalescer(inner, size=10, window=0.01)
    waiters = [asyncio.create_task(model.complete(_request(f"row{n}"))) for n in range(2)]
    await asyncio.sleep(0)  # both submissions are queued under the open window
    for waiter in waiters:
        waiter.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)
    await asyncio.sleep(0.02)
    assert inner.calls == []
    assert model.pending_groups == 0


async def test_close_resolves_queued_items_and_joins_their_timer() -> None:
    gate = asyncio.Event()

    async def held_sleep(_seconds: float) -> None:
        await gate.wait()

    inner = PackedFake()
    from smartpipe.models.base import ChatModel

    typed: ChatModel = inner  # type: ignore[assignment]
    model = CoalescingChatModel(
        typed,
        settings=BatchSettings(size=10, window_seconds=60.0),
        sleep=held_sleep,
        calls=OutboundCallPolicy(concurrency=1),
    )
    waiters = [asyncio.create_task(model.complete(_request(f"row{n}"))) for n in range(2)]
    await asyncio.sleep(0)
    await model.aclose()
    outcomes = await asyncio.gather(*waiters, return_exceptions=True)
    assert all(isinstance(outcome, ItemError) for outcome in outcomes)
    assert inner.calls == []
    assert model.pending_tasks == 0


def test_outbound_policy_configuration_is_one_time_and_idempotent() -> None:
    calls = OutboundCallPolicy(concurrency=4)
    calls.configure(concurrency=1, breaker_limit=3)
    calls.configure(concurrency=1, breaker_limit=3)
    with pytest.raises(RuntimeError, match="already configured"):
        calls.configure(concurrency=2, breaker_limit=3)


async def test_outbound_policy_reuses_one_trip_id_and_stops_new_calls() -> None:
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=2)
    ref = ModelRef("ollama", "down")
    calls = 0

    async def down() -> str:
        nonlocal calls
        calls += 1
        raise TransportError("connection refused")

    with pytest.raises(TransportError) as first:
        await policy.execute(ref, down)
    assert not isinstance(first.value, CircuitOpenTransport)
    with pytest.raises(CircuitOpenTransport) as tripped:
        await policy.execute(ref, down)
    with pytest.raises(CircuitOpenTransport) as already_open:
        await policy.execute(ref, down)
    assert already_open.value.trip_id == tripped.value.trip_id
    assert first.value.series_id == tripped.value.trip_id
    assert calls == 2


async def test_outbound_policy_resets_on_a_nontransport_response_and_keys_by_ref() -> None:
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=2)
    primary = ModelRef("ollama", "primary")
    fallback = ModelRef("ollama", "fallback")

    async def down() -> str:
        raise TransportError("connection refused")

    async def declined() -> str:
        raise ItemError("content declined")

    with pytest.raises(TransportError) as before_reset:
        await policy.execute(primary, down)
    with pytest.raises(ItemError):
        await policy.execute(primary, declined)
    with pytest.raises(TransportError) as after_reset:
        await policy.execute(primary, down)
    assert not isinstance(after_reset.value, CircuitOpenTransport)
    assert before_reset.value.series_id != after_reset.value.series_id
    assert await policy.execute(fallback, lambda: asyncio.sleep(0, result="ok")) == "ok"


def test_ref_mirrors_the_inner_wire() -> None:
    model = _coalescer(PackedFake())
    assert str(model.ref) == "ollama/fake"


def test_measured_gap_withholds_packing_only_above_twenty_points() -> None:
    from smartpipe.models.coalesce import packing_withheld

    assert packing_withheld(solo_success=100, packed_success=0)
    assert packing_withheld(solo_success=71, packed_success=50)
    assert not packing_withheld(solo_success=70, packed_success=50)
    assert not packing_withheld(solo_success=40, packed_success=60)


async def test_only_measured_model_withholds_packing_and_unknown_cloud_retains_it() -> None:
    unknown = PackedFake()
    unknown.ref = ModelRef("ollama", "ministral-3:14b-cloud")
    unknown_model = _coalescer(unknown, size=2)
    await asyncio.gather(
        unknown_model.complete(_request("a")), unknown_model.complete(_request("b"))
    )
    assert len(unknown.calls) == 1
    assert unknown_model.packed_calls == 1

    local = PackedFake()
    local.ref = ModelRef("ollama", "qwen3:0.6b")
    local_model = _coalescer(local, size=2)
    await asyncio.gather(local_model.complete(_request("a")), local_model.complete(_request("b")))
    assert len(local.calls) == 1
    assert local_model.packed_calls == 1
