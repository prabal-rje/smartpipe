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

from smartpipe.core.errors import ExitCode, SetupFault, TransportError
from smartpipe.engine.coalesce import BatchSettings
from smartpipe.io.writers import (
    OutputFormat,
    RenderMode,
    WriterConfig,
    make_writer,
)
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.models.coalesce import CoalescingChatModel
from smartpipe.verbs.extend import ExtendRequest, run_extend
from smartpipe.verbs.filter import FilterRequest, run_filter
from smartpipe.verbs.map import MapRequest, run_map

if TYPE_CHECKING:
    from collections.abc import Callable

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
    """A MapContext/FilterContext with batching ON (tiny window for tests)."""

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
            settings if settings is not None else BatchSettings(size=size, window_seconds=0.005)
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
    return CoalescingChatModel(model, settings=context.settings)


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
    code, batched_out, inner = await _run_map("Extract {shout}", stdin, _extract, enabled=True)
    assert code == ExitCode.OK
    # ceil(8/4)=2 packed calls for the full groups + the final straggler solo
    assert len(inner.calls) == 3
    _off_code, solo_out, solo_inner = await _run_map(
        "Extract {shout}", stdin, _extract, enabled=False
    )
    assert len(solo_inner.calls) == 9
    assert batched_out == solo_out  # identical final outputs, batching on or off


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


async def test_breaker_trips_on_batch_failure_backed_by_real_calls() -> None:
    class Down:
        def __init__(self) -> None:
            self.ref = ModelRef("ollama", "fake")
            self.calls = 0

        async def complete(self, request: CompletionRequest) -> str:
            self.calls += 1
            raise TransportError("connection refused")

    inner = Down()
    context = BatchContext(inner, size=6, enabled=True)  # type: ignore[arg-type]
    model: ChatModel = CoalescingChatModel(inner, settings=context.settings)  # type: ignore[arg-type]
    context.model = model
    request = MapRequest(
        prompt="Extract {shout}",
        schema_path=None,
        model_flag=None,
        output=OutputFormat.AUTO,
        concurrency_flag=None,
    )
    stdin = io.StringIO("".join(f"row{n}\n" for n in range(6)))
    try:
        await run_map(request, context, stdin=stdin, stdout=io.StringIO())
        raise AssertionError("the provider-down screen should have been raised")
    except SetupFault:
        pass
    # one failed packed call, then real solo failures until the breaker's five
    assert inner.calls >= 1 + 5


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
    model: ChatModel = CoalescingChatModel(inner, settings=context.settings, stop=stop)  # type: ignore[arg-type]
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
    assert code == ExitCode.OK  # everything that started finished cleanly
    lines = out.getvalue().splitlines()
    assert len(lines) == 3  # the drained batch, in order; intake stopped after
    assert json.loads(lines[0])["shout"] == "ROW0"
