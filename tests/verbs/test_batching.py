"""Batching through the verbs (item 62): fewer calls, identical results.

The fakes here answer packed requests properly (per labeled block) AND solo
requests, so the same corpus can run batching-on and batching-off — the
outputs must match byte for byte; only the call count may differ.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import (
    ExitCode,
    SchemaRejected,
    TransportError,
    UnsentError,
)
from smartpipe.engine.coalesce import BatchSettings
from smartpipe.engine.runner import FailurePolicy
from smartpipe.io import manifest
from smartpipe.io.writers import (
    OutputFormat,
    RenderMode,
    WriterConfig,
    make_writer,
)
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.models.coalesce import CoalescingChatModel, OutboundCallPolicy
from smartpipe.verbs.extend import ExtendRequest, run_extend
from smartpipe.verbs.filter import FilterRequest, run_filter
from smartpipe.verbs.map import MapRequest, run_map

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from smartpipe.io.writers import ResultWriter, TextSink
    from smartpipe.models.base import ChatModel

_BLOCK = re.compile(r'<input id="(r\d+)">\n(.*?)\n</input>', re.DOTALL)


class PackedCapable:
    """Deterministic wire: answers each input (packed or solo) from its body."""

    def __init__(self, answer: Callable[[str, CompletionRequest], object]) -> None:
        self.answer = answer  # body -> the per-item reply value
        self.ref = ModelRef("ollama", "fake")
        self.calls: list[CompletionRequest] = []

    def _body(self, user: str) -> str:
        opened = user.find("<input>\n")
        if opened == -1:
            return user.rsplit("\n\n", 1)[-1]  # a plain line (no fence on empty payloads)
        return user[opened + len("<input>\n") :].removesuffix("\n</input>")

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        blocks = _BLOCK.findall(request.user)
        if not blocks:
            value = self.answer(self._body(request.user), request)
            return value if isinstance(value, str) else json.dumps(value)
        packed = {
            label: self.answer(body.partition("instruction: ")[0] or body, request)
            for label, body in blocks
        }
        return json.dumps(packed)


class BatchContext:
    """A MapContext/FilterContext with batching ON.

    Every test here fills its groups exactly (item count divides by ``size``),
    so dispatch is always the synchronous size-cap path and never the window
    timer. The window is a belt that must never fire first: on Windows <= 3.12
    the event-loop clock ticks at ~15.6 ms, so any sub-tick window is treated
    as already due and flushes stragglers as SOLO flights mid-enqueue - the
    2026-07-10 matrix hang (a solo-only run never sets ``seen_packed``) and
    seven call-count failures. Window semantics are covered in
    ``tests/models/test_coalesce.py``, where enqueues share one ready-queue
    burst and no timer can interleave.
    """

    def __init__(
        self,
        model: ChatModel,
        *,
        size: int = 4,
        concurrency: int = 4,
        settings: BatchSettings | None = None,
        enabled: bool = True,
    ) -> None:
        self.model = model
        self.concurrency_value = concurrency
        self.settings = (
            settings if settings is not None else BatchSettings(size=size, window_seconds=60.0)
        )
        self.enabled = enabled

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        return self.model

    def fallback_ref(self, flag: str | None = None) -> ModelRef | None:
        return None

    async def fallback_chat_model(self, ref: object) -> ChatModel:
        raise AssertionError("fallback never resolved without a configured ref")

    def concurrency(self, flag: int | None = None) -> int:
        return self.concurrency_value

    def failure_policy(self, provider: str) -> FailurePolicy:
        from smartpipe.cli import screens

        return FailurePolicy(
            transport_limit=5,
            transport_screen=screens.provider_down(provider, 5),
        )

    def batching(self) -> BatchSettings | None:
        return self.settings if self.enabled else None

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def document_parser(self, flag: str | None = None) -> None:
        return None

    async def context_window(self, ref: object) -> int | None:
        return None

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextSink,
        fields: tuple[str, ...] | None = None,
        bare: bool = False,
        full: bool = False,
    ) -> ResultWriter:
        mode = RenderMode.NDJSON if structured else RenderMode.TEXT
        return make_writer(WriterConfig(mode=mode, color=False, width=80, fields=fields), stdout)


def _wrap(model: ChatModel, context: BatchContext) -> ChatModel:
    """The container's wiring for these tests: the coalescer around the wire."""
    assert context.settings is not None
    return CoalescingChatModel(
        model,
        settings=context.settings,
        calls=OutboundCallPolicy(concurrency=context.concurrency_value),
    )


def _extract(body: str, _request: CompletionRequest) -> object:
    return {"shout": body.upper()}


def _translate(body: str, _request: CompletionRequest) -> object:
    return f"hola {body}"


def _judge_keep(body: str, _request: CompletionRequest) -> object:
    return {"match": "keep" in body}


async def _run_map(
    prompt: str,
    stdin: str,
    answer: Callable[[str, CompletionRequest], object],
    *,
    enabled: bool,
    size: int = 4,
) -> tuple[ExitCode, str, PackedCapable]:
    inner = PackedCapable(answer)
    context = BatchContext(inner, size=size, enabled=enabled)
    model: ChatModel = _wrap(inner, context) if enabled else inner  # type: ignore[assignment]
    context.model = model
    out = io.StringIO()
    request = MapRequest(
        prompt=prompt,
        schema_path=None,
        model_flag=None,
        output=OutputFormat.AUTO,
        concurrency_flag=None,
    )
    code = await run_map(request, context, stdin=io.StringIO(stdin), stdout=out)
    return code, out.getvalue(), inner


async def test_map_structured_nine_items_fly_in_three_calls() -> None:
    stdin = "".join(f"row{n}\n" for n in range(9))
    code, batched_out, inner = await _run_map(
        "Extract {shout}", stdin, _extract, enabled=True, size=3
    )
    assert code == ExitCode.OK
    # 9/3 = three full groups, each dispatched by the size cap - no timer in play
    assert len(inner.calls) == 3
    _off_code, solo_out, solo_inner = await _run_map(
        "Extract {shout}", stdin, _extract, enabled=False
    )
    assert len(solo_inner.calls) == 9
    assert batched_out == solo_out  # identical final outputs, batching on or off


async def test_batch_six_concurrency_one_makes_three_sequential_calls() -> None:
    class PeakPacked(PackedCapable):
        def __init__(self) -> None:
            super().__init__(_extract)
            self.active = 0
            self.peak = 0

        async def complete(self, request: CompletionRequest) -> str:
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.005)
            try:
                return await super().complete(request)
            finally:
                self.active -= 1

    inner = PeakPacked()
    context = BatchContext(inner, size=6, concurrency=1, enabled=True)
    model: ChatModel = _wrap(inner, context)  # type: ignore[assignment]
    context.model = model
    out = io.StringIO()
    request = MapRequest(
        prompt="Extract {shout}",
        schema_path=None,
        model_flag=None,
        output=OutputFormat.AUTO,
        concurrency_flag=1,
    )
    code = await run_map(
        request,
        context,
        stdin=io.StringIO("".join(f"row{n}\n" for n in range(18))),
        stdout=out,
    )
    assert code == ExitCode.OK
    assert len(inner.calls) == 3
    assert inner.peak == 1
    assert len(out.getvalue().splitlines()) == 18


async def test_batch_workers_fill_every_concurrent_api_call_slot() -> None:
    class PeakPacked(PackedCapable):
        def __init__(self) -> None:
            super().__init__(_extract)
            self.active = 0
            self.peak = 0

        async def complete(self, request: CompletionRequest) -> str:
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.01)
            try:
                return await super().complete(request)
            finally:
                self.active -= 1

    inner = PeakPacked()
    context = BatchContext(inner, size=6, concurrency=3, enabled=True)
    context.model = _wrap(inner, context)
    out = io.StringIO()
    request = MapRequest(
        prompt="Extract {shout}",
        schema_path=None,
        model_flag=None,
        output=OutputFormat.AUTO,
        concurrency_flag=3,
    )

    code = await run_map(
        request,
        context,
        stdin=io.StringIO("".join(f"row{n}\n" for n in range(18))),
        stdout=out,
    )

    assert code == ExitCode.OK
    assert len(inner.calls) == 3
    assert inner.peak == 3
    assert len(out.getvalue().splitlines()) == 18


async def test_map_plain_mode_batches_too() -> None:
    stdin = "uno\ndos\ntres\n"
    code, batched_out, inner = await _run_map("translate", stdin, _translate, enabled=True, size=3)
    assert code == ExitCode.OK
    assert len(inner.calls) == 1
    packed = inner.calls[0]
    assert packed.json_schema is not None  # plain items answer as labeled strings
    _code, solo_out, _inner = await _run_map("translate", stdin, _translate, enabled=False)
    assert batched_out == solo_out == "hola uno\nhola dos\nhola tres\n"


async def test_filter_batches_judgments_and_keeps_the_subset() -> None:
    stdin = "keep one\ndrop two\nkeep three\ndrop four\nkeep five\nsix\n"

    async def run(enabled: bool) -> tuple[str, int]:
        inner = PackedCapable(_judge_keep)
        context = BatchContext(inner, size=3, enabled=enabled)
        model: ChatModel = _wrap(inner, context) if enabled else inner  # type: ignore[assignment]
        context.model = model
        out = io.StringIO()
        request = FilterRequest(
            condition="worth keeping", invert=False, model_flag=None, concurrency_flag=None
        )
        code = await run_filter(request, context, stdin=io.StringIO(stdin), stdout=out)
        assert code == ExitCode.OK
        return out.getvalue(), len(inner.calls)

    batched_out, batched_calls = await run(enabled=True)
    solo_out, solo_calls = await run(enabled=False)
    assert batched_calls == 2  # six judgments in two packed calls
    assert solo_calls == 6
    assert batched_out == solo_out == "keep one\nkeep three\nkeep five\n"


async def test_filter_interpolated_conditions_ride_inside_blocks() -> None:
    stdin = (
        json.dumps({"name": "alice", "note": "keep"})
        + "\n"
        + json.dumps({"name": "bob", "note": "keep"})
        + "\n"
    )
    inner = PackedCapable(_judge_keep)
    context = BatchContext(inner, size=2, enabled=True)
    model: ChatModel = _wrap(inner, context)  # type: ignore[assignment]
    context.model = model
    out = io.StringIO()
    request = FilterRequest(
        condition="{name} sounds friendly", invert=False, model_flag=None, concurrency_flag=None
    )
    code = await run_filter(request, context, stdin=io.StringIO(stdin), stdout=out)
    assert code == ExitCode.OK
    (packed,) = inner.calls
    # field-interpolated conditions vary per item, so each rides its own block
    assert "instruction: Condition: alice sounds friendly" in packed.user
    assert "instruction: Condition: bob sounds friendly" in packed.user


async def test_extend_merges_batched_fields_onto_the_records() -> None:
    stdin = (
        json.dumps({"id": 1, "text": "keep a"}) + "\n" + json.dumps({"id": 2, "text": "b"}) + "\n"
    )

    async def run(enabled: bool) -> tuple[str, int]:
        inner = PackedCapable(_extract)
        context = BatchContext(inner, size=2, enabled=enabled)
        model: ChatModel = _wrap(inner, context) if enabled else inner  # type: ignore[assignment]
        context.model = model
        out = io.StringIO()
        request = ExtendRequest(
            prompt="Add {shout}",
            schema_path=None,
            model_flag=None,
            output=OutputFormat.AUTO,
            concurrency_flag=None,
        )
        code = await run_extend(request, context, stdin=io.StringIO(stdin), stdout=out)
        assert code == ExitCode.OK
        return out.getvalue(), len(inner.calls)

    batched_out, batched_calls = await run(enabled=True)
    solo_out, solo_calls = await run(enabled=False)
    assert batched_calls == 1
    assert solo_calls == 2
    assert batched_out == solo_out
    first = json.loads(batched_out.splitlines()[0])
    assert first["id"] == 1  # the base record survives
    assert first["shout"] == "ID: 1\nTEXT: KEEP A"  # the extracted field lands beside it


async def test_keep_invalid_still_works_for_solo_retried_items() -> None:
    class BadSecond(PackedCapable):
        async def complete(self, request: CompletionRequest) -> str:
            self.calls.append(request)
            blocks = _BLOCK.findall(request.user)
            if blocks:  # the packed call: r2 comes back invalid
                answers: dict[str, object] = {}
                for label, body in blocks:
                    good: object = {"shout": body.upper()}
                    answers[label] = good if label != "r2" else {"wrong": True}
                return json.dumps(answers)
            return "still not the schema"  # the solo re-run AND its repair both fail

    inner = BadSecond(_extract)
    context = BatchContext(inner, size=3, enabled=True)
    model: ChatModel = _wrap(inner, context)  # type: ignore[assignment]
    context.model = model
    out = io.StringIO()
    request = MapRequest(
        prompt="Extract {shout}",
        schema_path=None,
        model_flag=None,
        output=OutputFormat.AUTO,
        concurrency_flag=None,
        keep_invalid=True,
    )
    code = await run_map(request, context, stdin=io.StringIO("a\nb\nc\n"), stdout=out)
    assert code == ExitCode.OK
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert rows[0]["shout"] == "A"
    assert rows[2]["shout"] == "C"
    assert rows[1]["__invalid"] is True  # the failure became data, in input order
    # packed call + solo re-run + its one repair = three calls
    assert len(inner.calls) == 3


async def test_one_packed_failure_does_not_multiply_retry_ladders() -> None:
    class Down:
        def __init__(self) -> None:
            self.ref = ModelRef("ollama", "fake")
            self.calls = 0

        async def complete(self, request: CompletionRequest) -> str:
            self.calls += 1
            raise TransportError("connection refused")

    inner = Down()
    context = BatchContext(inner, size=6, concurrency=1, enabled=True)  # type: ignore[arg-type]
    model: ChatModel = CoalescingChatModel(  # type: ignore[arg-type]
        inner,
        settings=context.settings,
        calls=OutboundCallPolicy(concurrency=1, breaker_limit=5),
    )
    context.model = model
    request = MapRequest(
        prompt="Extract {shout}",
        schema_path=None,
        model_flag=None,
        output=OutputFormat.AUTO,
        concurrency_flag=None,
    )
    stdin = io.StringIO("".join(f"row{n}\n" for n in range(6)))
    code = await run_map(request, context, stdin=stdin, stdout=io.StringIO())
    assert code is ExitCode.ALL_FAILED
    assert inner.calls == 1  # one packed ladder fans out; no K solo ladders


async def test_one_packed_trip_replays_every_waiter_once_on_fallback() -> None:
    class Down:
        def __init__(self) -> None:
            self.ref = ModelRef("ollama", "primary")
            self.calls = 0

        async def complete(self, request: CompletionRequest) -> str:
            self.calls += 1
            raise TransportError("connection refused")

    primary = Down()
    fallback = PackedCapable(_extract)
    fallback.ref = ModelRef("ollama", "fallback")
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=2)
    settings = BatchSettings(size=4, window_seconds=0.001)
    primary_model = CoalescingChatModel(primary, settings=settings, calls=policy)  # type: ignore[arg-type]
    fallback_model = CoalescingChatModel(fallback, settings=settings, calls=policy)  # type: ignore[arg-type]

    class FallbackContext(BatchContext):
        def fallback_ref(self, flag: str | None = None) -> ModelRef | None:
            return fallback.ref

        async def fallback_chat_model(self, ref: object) -> ChatModel:
            assert ref == fallback.ref
            return fallback_model

    context = FallbackContext(primary_model, settings=settings, concurrency=1)
    out = io.StringIO()
    code = await run_map(
        MapRequest(
            prompt="Extract {shout}",
            schema_path=None,
            model_flag=None,
            output=OutputFormat.AUTO,
            concurrency_flag=1,
            fallback_flag="ollama/fallback",
        ),
        context,
        stdin=io.StringIO("a\nb\nc\nd\ne\nf\ng\nh\n"),
        stdout=out,
    )
    assert code == ExitCode.OK
    assert primary.calls == 2  # two packed actual calls reach the threshold
    assert len(fallback.calls) == 2  # eight replayed waiters re-form two packed calls
    assert [json.loads(line)["shout"] for line in out.getvalue().splitlines()] == [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
        "H",
    ]


async def test_reverse_transport_completion_replays_the_whole_breaker_series() -> None:
    class ReverseDown:
        ref = ModelRef("ollama", "primary")

        def __init__(self) -> None:
            self.calls = 0
            self.high_failed = asyncio.Event()

        async def complete(self, request: CompletionRequest) -> str:
            self.calls += 1
            if _BLOCK.findall(request.user):
                raise SchemaRejected("packed schema rejected")  # recover each key once
            if "\na\n</input>" in request.user:
                await self.high_failed.wait()
                raise TransportError("low index failed second")  # availability call 2: trip
            self.high_failed.set()
            raise TransportError("high index failed first")  # availability call 1

    primary = ReverseDown()
    fallback = PackedCapable(_extract)
    fallback.ref = ModelRef("ollama", "fallback")
    policy = OutboundCallPolicy(concurrency=2, breaker_limit=2)
    settings = BatchSettings(size=2, window_seconds=0.001)
    primary_model = CoalescingChatModel(primary, settings=settings, calls=policy)  # type: ignore[arg-type]
    fallback_model = CoalescingChatModel(fallback, settings=settings, calls=policy)  # type: ignore[arg-type]

    class FallbackContext(BatchContext):
        def fallback_ref(self, flag: str | None = None) -> ModelRef | None:
            return fallback.ref

        async def fallback_chat_model(self, ref: object) -> ChatModel:
            assert ref == fallback.ref
            return fallback_model

    context = FallbackContext(primary_model, settings=settings, concurrency=2)
    out = io.StringIO()
    code = await run_map(
        MapRequest(
            prompt="Extract {shout}",
            schema_path=None,
            model_flag=None,
            output=OutputFormat.AUTO,
            concurrency_flag=2,
            fallback_flag="ollama/fallback",
        ),
        context,
        stdin=io.StringIO("a\nb\n"),
        stdout=out,
    )
    assert code == ExitCode.OK
    assert primary.calls == 3
    assert len(fallback.calls) == 1
    assert [json.loads(line)["shout"] for line in out.getvalue().splitlines()] == ["A", "B"]


@pytest.mark.parametrize("surface", ("map", "filter", "extend"))
async def test_unsent_rows_are_skipped_but_not_failed_in_manifests(
    surface: str,
    tmp_path: Path,
) -> None:
    class OneUnsent(PackedCapable):
        async def complete(self, request: CompletionRequest) -> str:
            if "skip" in self._body(request.user):
                self.calls.append(request)
                raise UnsentError("run stopping — not sent")
            return await super().complete(request)

    answer = _judge_keep if surface == "filter" else _extract
    inner = OneUnsent(answer)
    context = BatchContext(inner, concurrency=1, enabled=False)
    out = io.StringIO()
    target = tmp_path / f"{surface}.json"
    manifest.reset()
    manifest.begin(target, verb=surface, argv=(surface,))
    if surface == "map":
        code = await run_map(
            MapRequest("Extract {shout}", None, None, OutputFormat.AUTO, 1),
            context,
            stdin=io.StringIO("keep\nskip\n"),
            stdout=out,
        )
    elif surface == "filter":
        code = await run_filter(
            FilterRequest("worth keeping", False, None, 1),
            context,
            stdin=io.StringIO("keep\nskip\n"),
            stdout=out,
        )
    else:
        code = await run_extend(
            ExtendRequest("Add {shout}", None, None, OutputFormat.AUTO, 1),
            context,
            stdin=io.StringIO('{"text":"keep"}\n{"text":"skip"}\n'),
            stdout=out,
        )
    manifest.finish(code)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 2, "succeeded": 1, "skipped": 1, "failed": 0}


async def test_interrupt_drains_the_inflight_batch() -> None:
    gate = asyncio.Event()
    seen_packed = asyncio.Event()

    class Held(PackedCapable):
        async def complete(self, request: CompletionRequest) -> str:
            if _BLOCK.findall(request.user):
                seen_packed.set()
                await gate.wait()  # the batch is on the wire when Ctrl-C lands
            return await super().complete(request)

    inner = Held(_extract)
    stop = asyncio.Event()
    context = BatchContext(inner, size=3, concurrency=3, enabled=True)
    model: ChatModel = CoalescingChatModel(  # type: ignore[arg-type]
        inner,
        settings=context.settings,
        stop=stop,
        calls=OutboundCallPolicy(concurrency=3),
    )
    context.model = model
    out = io.StringIO()
    request = MapRequest(
        prompt="Extract {shout}",
        schema_path=None,
        model_flag=None,
        output=OutputFormat.AUTO,
        concurrency_flag=None,
    )
    stdin = io.StringIO("".join(f"row{n}\n" for n in range(9)))
    run = asyncio.create_task(run_map(request, context, stdin=stdin, stdout=out, stop=stop))
    await seen_packed.wait()
    stop.set()  # Ctrl-C: the in-flight batch drains; nothing new flies
    gate.set()
    code = await run
    # Intake and the interrupt are concurrent: a fast scheduler may have
    # accepted later rows before the stop (those become unsent, so exit 1),
    # while a slower one accepts only this completed batch (normal exit 0).
    # Both are the pinned drained-interrupt contract; output must not differ.
    assert code in (ExitCode.OK, ExitCode.PARTIAL)
    lines = out.getvalue().splitlines()
    assert len(lines) == 3  # the drained batch, in order; intake stopped after
    assert json.loads(lines[0])["shout"] == "ROW0"
