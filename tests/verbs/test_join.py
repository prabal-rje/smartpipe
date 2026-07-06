"""``join`` (D21): embed → block → judge, with the fail-before-spend preflight.

Fakes for both models; the chat fake records every judge call so the preflight
order ("a bad right side costs zero chat calls") is machine-proven, not asserted
by vibes.
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import ExitCode, ItemError, UsageFault
from sempipe.io.items import item_from_line
from sempipe.io.writers import OutputFormat, RenderMode, ResultWriter, WriterConfig, make_writer
from sempipe.models.base import CompletionRequest, ModelRef
from sempipe.verbs.join import JoinRequest, run_join

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from typing import TextIO


class FakeEmbed:
    """Vector per known text; unknown text raises (an embed failure)."""

    def __init__(self, table: Mapping[str, tuple[float, ...]]) -> None:
        self.ref = ModelRef("ollama", "fake-embed")
        self.table = dict(table)
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls.append(list(texts))
        out: list[tuple[float, ...]] = []
        for text in texts:
            vector = next((vec for key, vec in self.table.items() if key in text), None)
            if vector is None:
                raise ItemError(f"no embedding for {text!r}")
            out.append(vector)
        return tuple(out)


class FakeJudge:
    """Verdict by (left fragment, right fragment) containment in the statement."""

    def __init__(self, matches: Sequence[tuple[str, str]], *, poison: str | None = None) -> None:
        self.ref = ModelRef("ollama", "fake-chat")
        self.matches = tuple(matches)
        self.poison = poison
        self.calls: list[str] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request.user)
        if self.poison is not None and self.poison in request.user:
            raise ItemError("judge exploded")
        verdict = any(
            left in request.user and right in request.user for left, right in self.matches
        )
        return json.dumps({"match": verdict})


class FakeContext:
    def __init__(self, embed: FakeEmbed, judge: FakeJudge) -> None:
        self.embed = embed
        self.judge = judge

    async def chat_model(self, flag: str | None = None) -> FakeJudge:
        return self.judge

    async def embedding_model(self, flag: str | None = None) -> FakeEmbed:
        return self.embed

    def concurrency(self, flag: int | None = None) -> int:
        return 1  # deterministic transcripts

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter:
        config = WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=fields)
        return make_writer(config, stdout)


TABLE: dict[str, tuple[float, ...]] = {
    "printer smoking": (1.0, 0.0),
    "coffee is cold": (0.0, 1.0),
    "LaserJet 9": (0.9, 0.1),
    "Espresso One": (0.1, 0.9),
}


def _right_file(tmp_path: Path, lines: Sequence[str]) -> Path:
    path = tmp_path / "right.jsonl"
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def _request(right: Path, **kw: object) -> JoinRequest:
    defaults: dict[str, object] = {
        "predicate": "ticket {left.text} concerns {right.name}",
        "right": right,
        "k": 1,
        "threshold": None,
        "model_flag": None,
        "embed_model_flag": None,
        "concurrency_flag": None,
        "output": OutputFormat.AUTO,
    }
    defaults.update(kw)
    return JoinRequest(**defaults)  # type: ignore[arg-type]


async def _run(
    request: JoinRequest, stdin: str, embed: FakeEmbed, judge: FakeJudge
) -> tuple[ExitCode, list[dict[str, object]], FakeJudge]:
    out = io.StringIO()
    code = await run_join(request, FakeContext(embed, judge), stdin=io.StringIO(stdin), stdout=out)
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    return code, records, judge


RIGHT_LINES = ('{"name": "LaserJet 9"}', '{"name": "Espresso One"}')


def _side_values(records: list[dict[str, object]], side: str, key: str) -> list[object]:
    from sempipe.core.jsontools import as_record

    values: list[object] = []
    for record in records:
        payload = as_record(record[side])
        assert payload is not None
        values.append(payload[key])
    return values


def _right_names(records: list[dict[str, object]]) -> list[object]:
    return _side_values(records, "right", "name")


def _left_texts(records: list[dict[str, object]]) -> list[object]:
    return _side_values(records, "left", "text")


async def test_matches_pair_and_nests_both_sides(tmp_path: Path) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[("printer smoking", "LaserJet 9")])
    code, records, _ = await _run(
        _request(_right_file(tmp_path, RIGHT_LINES)), "printer smoking\n", embed, judge
    )
    assert code is ExitCode.OK
    assert records == [
        {
            "left": {"text": "printer smoking"},
            "right": {"name": "LaserJet 9"},
            "_score": pytest.approx(0.9969, abs=1e-3),
        }
    ]


async def test_bad_right_file_costs_zero_judge_calls(tmp_path: Path) -> None:
    embed = FakeEmbed({})  # right side can't embed at all
    judge = FakeJudge(matches=[])
    from sempipe.core.errors import TooManyFailures

    with pytest.raises(TooManyFailures):
        await _run(
            _request(_right_file(tmp_path, RIGHT_LINES * 3)),
            "printer smoking\n",
            embed,
            judge,
        )
    assert judge.calls == []  # the fail-before-spend contract, machine-proven


async def test_right_side_embeds_before_any_left_work(tmp_path: Path) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[])
    await _run(_request(_right_file(tmp_path, RIGHT_LINES)), "printer smoking\n", embed, judge)
    assert embed.calls[0] == ['{"name": "LaserJet 9"}', '{"name": "Espresso One"}'][:64]


async def test_zero_matches_is_a_clean_zero(tmp_path: Path) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[])  # judged, nothing true
    code, records, _ = await _run(
        _request(_right_file(tmp_path, RIGHT_LINES)), "printer smoking\n", embed, judge
    )
    assert code is ExitCode.OK
    assert records == []


async def test_k_widens_the_candidate_set(tmp_path: Path) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(
        matches=[("printer smoking", "LaserJet 9"), ("printer smoking", "Espresso One")]
    )
    _code, records, judge = await _run(
        _request(_right_file(tmp_path, RIGHT_LINES), k=2), "printer smoking\n", embed, judge
    )
    assert len(judge.calls) == 2  # both candidates judged
    assert _right_names(records) == ["LaserJet 9", "Espresso One"]


async def test_threshold_filters_before_judging(tmp_path: Path) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[("printer smoking", "Espresso One")])
    _code, _records, judge = await _run(
        _request(_right_file(tmp_path, RIGHT_LINES), k=2, threshold=0.9),
        "printer smoking\n",
        embed,
        judge,
    )
    assert len(judge.calls) == 1  # Espresso One (score ~.55) never reached the judge


async def test_poison_pair_skips_alone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[("printer smoking", "Espresso One")], poison="LaserJet 9")
    code, records, _ = await _run(
        _request(_right_file(tmp_path, RIGHT_LINES), k=2), "printer smoking\n", embed, judge
    )
    assert code is ExitCode.PARTIAL  # one pair skipped
    assert _right_names(records) == ["Espresso One"]
    assert "skipped:" in capsys.readouterr().err


async def test_left_order_is_preserved(tmp_path: Path) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(
        matches=[("printer smoking", "LaserJet 9"), ("coffee is cold", "Espresso One")]
    )
    _code, records, _ = await _run(
        _request(_right_file(tmp_path, RIGHT_LINES)),
        "printer smoking\ncoffee is cold\n",
        embed,
        judge,
    )
    assert _left_texts(records) == ["printer smoking", "coffee is cold"]


async def test_empty_right_is_a_usage_error(tmp_path: Path) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[])
    with pytest.raises(UsageFault, match="empty — a join against nothing"):
        await _run(_request(_right_file(tmp_path, ())), "x\n", embed, judge)


async def test_right_dash_is_a_usage_error(tmp_path: Path) -> None:
    import pathlib

    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[])
    with pytest.raises(UsageFault, match="stdin is join's left side"):
        await _run(_request(pathlib.Path("-")), "x\n", embed, judge)


async def test_k_zero_is_a_usage_error(tmp_path: Path) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[])
    with pytest.raises(UsageFault, match="--k must be >= 1"):
        await _run(_request(_right_file(tmp_path, RIGHT_LINES), k=0), "x\n", embed, judge)


async def test_missing_right_file_is_a_usage_error(tmp_path: Path) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[])
    with pytest.raises(UsageFault, match="no such file"):
        await _run(_request(tmp_path / "nope.jsonl"), "x\n", embed, judge)


async def test_dotted_fields_project_the_nested_record(tmp_path: Path) -> None:
    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[("printer smoking", "LaserJet 9")])
    code, records, _ = await _run(
        _request(_right_file(tmp_path, RIGHT_LINES), fields=("right.name", "_score")),
        "printer smoking\n",
        embed,
        judge,
    )
    assert code is ExitCode.OK
    assert list(records[0]) == ["right.name", "_score"]
    assert records[0]["right.name"] == "LaserJet 9"


async def test_judge_repair_recovers_an_invalid_verdict(tmp_path: Path) -> None:
    class FlakyJudge:
        def __init__(self) -> None:
            self.ref = ModelRef("ollama", "flaky")
            self.calls = 0

        async def complete(self, request: CompletionRequest) -> str:
            self.calls += 1
            if self.calls == 1:
                return "hmm, probably yes?"  # invalid → one repair re-ask
            return '{"match": true}'

    embed = FakeEmbed(TABLE)
    judge = FlakyJudge()
    out = io.StringIO()
    code = await run_join(
        _request(_right_file(tmp_path, RIGHT_LINES)),
        FakeContext(embed, judge),  # type: ignore[arg-type]
        stdin=io.StringIO("printer smoking\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert judge.calls == 2  # the single repair, exactly like filter/map
    assert '"LaserJet 9"' in out.getvalue()


async def test_five_consecutive_pair_failures_halt_the_doomed_join(tmp_path: Path) -> None:
    from sempipe.core.errors import TooManyFailures

    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[], poison="Statement")  # poison hits EVERY judge call
    with pytest.raises(TooManyFailures):
        await _run(
            _request(_right_file(tmp_path, RIGHT_LINES * 3), k=2),
            "printer smoking\ncoffee is cold\nprinter smoking\n",
            embed,
            judge,
        )
    assert len(judge.calls) == 5  # D18: stopped paying at the fifth consecutive


def test_preview_lines(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from sempipe.io import tty
    from sempipe.verbs.join import preview_cost

    monkeypatch.setattr(tty, "stderr_is_tty", lambda: True)
    preview_cost(total=1204, k=5, index_size=400)
    preview_cost(total=10, k=5, index_size=400)  # under the threshold: silent
    preview_cost(total=None, k=5, index_size=400)  # streaming left: the rate line
    err = capsys.readouterr().err
    assert "1,204 left items · up to 5 candidates each = at most 6,020 model calls" in err
    assert "up to 5 model calls per input line" in err
    assert err.count("join:") == 2


async def test_ratio_halt_covers_the_pair_book() -> None:
    from sempipe.core.errors import TooManyFailures
    from sempipe.engine.runner import FailurePolicy
    from sempipe.verbs.join import PairBook

    book = PairBook(
        policy=FailurePolicy(halt_ratio=0.5, min_sample=2, consecutive_limit=99),
        right_name="r.jsonl",
    )
    left = item_from_line("x\n", 0)
    book.ok()
    book.skip(left, 0, "bad")
    with pytest.raises(TooManyFailures):
        book.skip(left, 1, "bad")  # 2 of 3 judged failed — past the ratio


async def test_left_image_item_skips_whole(tmp_path: Path) -> None:
    png = tmp_path / "photo.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    from sempipe.io.inputs import InputSpec

    embed = FakeEmbed(TABLE)
    judge = FakeJudge(matches=[])
    out = io.StringIO()
    request = _request(
        _right_file(tmp_path, RIGHT_LINES),
        input=InputSpec(patterns=(str(png),), from_files=False),
    )

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    code = await run_join(request, FakeContext(embed, judge), stdin=_Tty(), stdout=out)
    assert code is ExitCode.ALL_FAILED  # the one left item skipped (image needs map)
    assert out.getvalue() == ""
