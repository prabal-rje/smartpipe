"""The extend verb: your record, plus columns (D38/02)."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.io.writers import OutputFormat
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.verbs.extend import ExtendRequest, base_fields, run_extend

if TYPE_CHECKING:
    from typing import TextIO

    from smartpipe.io.writers import ResultWriter


class SentimentModel:
    """Answers every item with the same extraction — enough to test the merge."""

    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake")
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> str:
        self.calls += 1
        return '{"sentiment": "neg"}'


class FakeContext:
    def __init__(self, model: SentimentModel) -> None:
        self.model = model

    async def chat_model(self, flag: str | None = None) -> SentimentModel:
        return self.model

    async def context_window(self, ref: ModelRef) -> int | None:
        return None  # no window info: the gate never trips

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter:
        from smartpipe.io.writers import RenderMode, WriterConfig, make_writer

        config = WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=fields)
        return make_writer(config, stdout)


def _request(prompt: str, **kwargs: object) -> ExtendRequest:
    return ExtendRequest(
        prompt=prompt,
        schema_path=None,
        model_flag=None,
        output=OutputFormat.JSON,
        concurrency_flag=None,
        **kwargs,  # type: ignore[arg-type]
    )


async def _run(
    prompt: str, stdin_text: str, **kwargs: object
) -> tuple[ExitCode, list[dict[str, object]], str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_extend(
            _request(prompt, **kwargs),
            FakeContext(SentimentModel()),
            stdin=io.StringIO(stdin_text),
            stdout=out,
        )
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    return code, rows, err.getvalue()


async def test_merge_preserves_every_input_field() -> None:
    code, rows, _err = await _run(
        "Add {sentiment}", '{"id": 812, "customer": "acme", "body": "crashes"}\n'
    )
    assert code is ExitCode.OK
    assert rows == [{"id": 812, "customer": "acme", "body": "crashes", "sentiment": "neg"}]


async def test_collision_overwrites_and_notes_once() -> None:
    stdin_text = '{"id": 1, "sentiment": "old"}\n{"id": 2, "sentiment": "old"}\n'
    _code, rows, err = await _run("Add {sentiment}", stdin_text)
    assert [row["sentiment"] for row in rows] == ["neg", "neg"]  # extracted wins
    assert err.count("overwriting 'sentiment'") == 1  # disclosed once, not per row


async def test_plain_lines_promote_to_text_records() -> None:
    _code, rows, _err = await _run("Add {sentiment}", "the app keeps crashing\n")
    assert rows == [{"text": "the app keeps crashing", "sentiment": "neg"}]


async def test_plain_prompt_is_a_usage_fault_before_any_call() -> None:
    context = FakeContext(SentimentModel())
    with pytest.raises(UsageFault, match="extend adds fields"):
        await run_extend(
            _request("just summarize"),
            context,
            stdin=io.StringIO("x\n"),
            stdout=io.StringIO(),
        )
    assert context.model.calls == 0


async def test_explode_copies_original_fields_onto_every_row() -> None:
    class RisksModel(SentimentModel):
        async def complete(self, request: CompletionRequest) -> str:
            return '{"risks": ["fire", "flood"]}'

    out = io.StringIO()
    import contextlib

    with contextlib.redirect_stderr(io.StringIO()):
        code = await run_extend(
            _request("Add {risks}", explode_field="risks"),
            FakeContext(RisksModel()),
            stdin=io.StringIO('{"id": 7, "site": "plant-a"}\n'),
            stdout=out,
        )
    assert code is ExitCode.OK
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert rows == [
        {"id": 7, "site": "plant-a", "risks": "fire"},
        {"id": 7, "site": "plant-a", "risks": "flood"},
    ]


def test_base_fields_drops_media_transport_keys() -> None:
    from dataclasses import replace

    from smartpipe.io.items import item_from_line

    line = json.dumps(
        {"image_b64": "aGk=", "mime": "image/png", "source": "fig.png", "text": "a chart"}
    )
    item = item_from_line(line, 0)
    assert item.media  # the record carried media
    base = base_fields(replace(item))
    assert "image_b64" not in base and "mime" not in base  # transport dropped
    assert base["source"] == "fig.png"  # provenance survives
