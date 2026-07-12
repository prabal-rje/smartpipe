"""The graph verb's --fast half (wave G1): free by construction, pinned here."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, ItemError, SetupFault, UsageFault
from smartpipe.engine.graphkg import EntitySpan
from smartpipe.engine.runner import FailurePolicy
from smartpipe.io.inputs import InputSpec
from smartpipe.verbs.graph import (
    DEFAULT_ENTITIES,
    FoldCut,
    FoldOutcome,
    GraphRequest,
    fold_cut_flips_partial,
    fold_vectors,
    parse_entities,
    run_graph,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from smartpipe.models.base import AudioData, ChatModel, EmbeddingModel, ModelRef
    from smartpipe.models.resilience import WiredChat
    from smartpipe.models.stt import Transcriber


class FakeFinder:
    """Marks any configured name it sees in the text, with char offsets."""

    def __init__(self, known: dict[str, str], *, poison: str | None = None) -> None:
        self.known = known  # name -> label
        self.poison = poison
        self.calls: list[str] = []
        self.events: list[str] = []  # "load"/"find" in call order — the pre-warm pin

    def load(self, *, quiet: bool = False) -> None:
        del quiet
        self.events.append("load")

    def find(self, text: str) -> tuple[EntitySpan, ...]:
        self.calls.append(text)
        self.events.append("find")
        if self.poison is not None and self.poison in text:
            raise ItemError("the local NER model returned an unexpected shape")
        found = [
            EntitySpan(
                name=name, label=label, start=text.index(name), end=text.index(name) + len(name)
            )
            for name, label in self.known.items()
            if name in text
        ]
        return tuple(sorted(found, key=lambda span: span.start))


class FakeEmbedder:
    """Unit vectors by table — names sharing a vector fold together; names
    outside the table get mutually orthogonal one-hots (they never fold)."""

    def __init__(self, table: dict[str, tuple[float, ...]], *, broken: bool = False) -> None:
        self.table = table
        self.broken = broken
        self.batches: list[list[str]] = []
        self._assigned: dict[str, int] = {}

    @property
    def ref(self) -> ModelRef:
        from smartpipe.models.base import parse_model_ref

        return parse_model_ref("local/nomic-embed-text-v1.5")

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if self.broken:
            raise ItemError("local embedding failed (onnx says no)")
        self.batches.append(list(texts))
        return tuple(self._vector(text) for text in texts)

    def _vector(self, text: str) -> tuple[float, ...]:
        if text in self.table:
            return self.table[text]
        slot = self._assigned.setdefault(text, len(self._assigned))
        one_hot = [0.0] * 64
        one_hot[slot] = 1.0
        return tuple(one_hot)


class FakeTranscriber:
    """The Transcriber protocol in miniature — ``ref`` is a PROPERTY there,
    so the fake declares one too (pyright keeps the conformance honest)."""

    def __init__(self, ref_text: str, reply: str = "Ann met Bob", *, fail: bool = False) -> None:
        from smartpipe.models.base import parse_model_ref

        self._ref = parse_model_ref(ref_text)
        self.reply = reply
        self.fail = fail
        self.heard: list[bytes] = []

    @property
    def ref(self) -> ModelRef:
        return self._ref

    async def transcribe(self, audio: AudioData) -> str:
        self.heard.append(audio.data)
        if self.fail:
            raise ItemError("the wire hiccuped")  # plain = recoverable — the ladder continues
        return self.reply


@dataclass
class FakeContext:
    finder: FakeFinder
    embedder: FakeEmbedder
    finder_labels: tuple[str, ...] = ()
    chat_calls: int = field(default=0)
    stt: Transcriber | None = None
    stt_flags: list[str | None] = field(default_factory=list["str | None"])

    def entity_finder(self, labels: Sequence[str]) -> FakeFinder:
        self.finder_labels = tuple(labels)
        return self.finder

    async def fold_embedder(self, flag: str | None = None) -> FakeEmbedder:
        del flag  # the fake's embedder is fixed; flag-honoring is proven at the container
        return self.embedder

    def remote_transcriber(self, *, flag: str | None = None) -> Transcriber | None:
        self.stt_flags.append(flag)
        return self.stt

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        self.chat_calls += 1
        raise AssertionError("graph --fast constructed a chat model — the free pin is broken")

    async def resilient_chat_model(
        self, flag: str | None = None, fallback_flag: str | None = None
    ) -> WiredChat:
        self.chat_calls += 1
        raise AssertionError("graph --fast wired a resilient chat model: the free pin is broken")

    async def embedding_model(self, flag: str | None = None) -> object:
        self.chat_calls += 1
        raise AssertionError("graph --fast resolved the configured embedder — must stay local")

    def document_parser(self, flag: str | None = None) -> None:
        return None  # the free modes never parse documents through a model

    def batching(self) -> None:
        return None  # the free modes never coalesce — no chat calls to pack (#21)

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def failure_policy(self, provider: str) -> FailurePolicy:
        del provider
        return FailurePolicy()


def _context(
    known: dict[str, str] | None = None,
    *,
    vectors: dict[str, tuple[float, ...]] | None = None,
    poison: str | None = None,
    broken_embedder: bool = False,
) -> FakeContext:
    return FakeContext(
        finder=FakeFinder(known or {}, poison=poison),
        embedder=FakeEmbedder(vectors or {}, broken=broken_embedder),
    )


async def _run(
    request: GraphRequest, context: FakeContext, stdin_text: str = ""
) -> tuple[ExitCode, str]:
    out = io.StringIO()
    code = await run_graph(
        request, context, stdin=io.StringIO(stdin_text), stdout=out, clock=lambda: 0.0
    )
    return code, out.getvalue()


PEOPLE = {"Ann": "person", "Bob": "person", "Acme": "company"}


# --- flag validation ----------------------------------------------------------


async def test_graph_without_fast_refuses_loudly() -> None:
    with pytest.raises(UsageFault, match="--fast"):
        await _run(GraphRequest(fast=False), _context())


async def test_min_weight_and_top_validate() -> None:
    with pytest.raises(UsageFault, match="--min-weight"):
        await _run(GraphRequest(fast=True, min_weight=0), _context())
    with pytest.raises(UsageFault, match="--top"):
        await _run(GraphRequest(fast=True, top=0), _context())


async def test_save_extension_refuses_before_any_work() -> None:
    context = _context(PEOPLE)
    with pytest.raises(UsageFault, match="--save"):
        await _run(GraphRequest(fast=True, save="graph.xlsx"), context, "Ann met Bob\n")


async def test_concurrency_is_configured_before_mode_work() -> None:
    class InvalidConcurrency(FakeContext):
        def concurrency(self, flag: int | None = None) -> int:
            raise UsageFault("invalid concurrency")

    base = _context(PEOPLE)
    context = InvalidConcurrency(base.finder, base.embedder)

    with pytest.raises(UsageFault, match="invalid concurrency"):
        await _run(GraphRequest(fast=True), context, "Ann met Bob\n")

    assert context.finder.calls == []
    assert context.finder.calls == []  # the refusal landed before the grind


def test_parse_entities_defaults_and_dedupes() -> None:
    assert parse_entities(None) == DEFAULT_ENTITIES
    assert parse_entities("person, vessel , person") == ("person", "vessel")
    with pytest.raises(UsageFault, match="--entities"):
        parse_entities(" , ")


async def test_entities_dial_reaches_the_finder() -> None:
    context = _context(PEOPLE)
    await _run(GraphRequest(fast=True, entities="person, vessel"), context, "Ann met Bob\n")
    assert context.finder_labels == ("person", "vessel")


async def test_scan_pre_warms_the_model_before_the_first_find() -> None:
    # the load's download/session-init shows a caller-owned status line instead
    # of a silent block inside the per-item loop (reads as "hung" otherwise)
    context = _context(PEOPLE)
    await _run(GraphRequest(fast=True), context, "Ann met Bob\n")
    assert context.finder.events[0] == "load"
    assert context.finder.events.index("load") < context.finder.events.index("find")


async def test_scan_without_labels_never_pays_for_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smartpipe.verbs import graph as graph_module

    def no_labels(raw: str | None) -> tuple[str, ...]:
        del raw
        return ()

    monkeypatch.setattr(graph_module, "parse_entities", no_labels)
    context = _context(PEOPLE)
    await _run(GraphRequest(fast=True), context, "Ann met Bob\n")
    assert "load" not in context.finder.events  # no labels → no ~190 MB pull


# --- the free co-occurrence path ------------------------------------------------


async def test_chunk_windows_emit_weighted_jsonl_edges() -> None:
    code, out = await _run(
        GraphRequest(fast=True), _context(PEOPLE), "Ann met Bob\nBob saw Ann\nAnn alone\n"
    )
    assert code is ExitCode.OK
    edges = [json.loads(line) for line in out.splitlines()]
    assert edges == [
        {
            "source": "Ann",
            "relation": "co-occurs",
            "target": "Bob",
            "weight": 2,
            "sources": [
                {"path": "-", "as": "lines", "line": 1},
                {"path": "-", "as": "lines", "line": 2},
            ],
        }
    ]


async def test_receipt_wording_is_pinned(capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = await _run(
        GraphRequest(fast=True), _context(PEOPLE), "Ann met Bob at Acme\nBob left Acme\n"
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert "note: graph: 3 entities (0 folded) · 3 edges (0 pruned) · 0 tok" in err


async def test_min_weight_prunes_and_the_receipt_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, out = await _run(
        GraphRequest(fast=True, min_weight=2),
        _context(PEOPLE),
        "Ann met Bob\nBob saw Ann\nAnn met Acme\nBob near Acme\n",
    )
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"]) for e in edges] == [("Ann", "Bob")]
    assert "3 entities (0 folded) · 1 edges (2 pruned) · 0 tok" in capsys.readouterr().err


async def test_sentence_window_separates_sentences() -> None:
    _, out = await _run(
        GraphRequest(fast=True, window="sentence"),
        _context(PEOPLE),
        "Ann met Bob. Acme closed.\n",
    )
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"]) for e in edges] == [("Ann", "Bob")]


async def test_document_window_spans_the_whole_file(tmp_path: Path) -> None:
    corpus = tmp_path / "notes.txt"
    corpus.write_text("Ann here\nBob there\n", encoding="utf-8")
    request = GraphRequest(
        fast=True,
        window="document",
        input=InputSpec(patterns=(str(corpus),), from_files=False, as_mode="lines"),
    )
    _, out = await _run(request, _context(PEOPLE))
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"], e["weight"]) for e in edges] == [("Ann", "Bob", 1)]
    assert edges[0]["sources"] == [{"path": str(corpus), "as": "file"}]


async def test_jsonl_records_project_their_content_fields() -> None:
    row = json.dumps({"text": "Ann met Bob", "__source": {"path": "notes.txt", "as": "lines"}})
    _, out = await _run(GraphRequest(fast=True), _context(PEOPLE), f"{row}\n")
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"]) for e in edges] == [("Ann", "Bob")]


async def test_empty_input_is_ok_and_silent() -> None:
    code, out = await _run(GraphRequest(fast=True), _context(PEOPLE), "")
    assert code is ExitCode.OK
    assert out == ""


# --- folding ---------------------------------------------------------------------


async def test_entity_folding_notes_and_folds_onto_the_most_frequent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    known = {"Acme Corp": "company", "Acme Corporation": "company", "Bob": "person"}
    vectors = {
        "Acme Corp": (1.0, 0.0, 0.0),
        "Acme Corporation": (1.0, 0.0, 0.0),
        "Bob": (0.0, 1.0, 0.0),
    }
    _, out = await _run(
        GraphRequest(fast=True),
        _context(known, vectors=vectors),
        "Acme Corp hired Bob\nAcme Corp grew\nAcme Corporation filed\nBob at Acme Corporation\n",
    )
    err = capsys.readouterr().err
    assert "note: 2 entity names folded into 1 node" in err
    assert "3 entities (2 folded)" in err
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"], e["weight"]) for e in edges] == [("Acme Corp", "Bob", 2)]


async def test_broken_embedder_degrades_to_no_folding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    known = {"Acme Corp": "company", "Acme Corporation": "company"}
    code, out = await _run(
        GraphRequest(fast=True),
        _context(known, broken_embedder=True),
        "Acme Corp and Acme Corporation\n",
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert "entity folding skipped" in err
    assert "2 entities (0 folded)" in err
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"]) for e in edges] == [("Acme Corp", "Acme Corporation")]


async def test_single_entity_never_wakes_the_embedder() -> None:
    context = _context({"Ann": "person"})
    await _run(GraphRequest(fast=True), context, "Ann alone\n")
    assert context.embedder.batches == []


# --- the free ladder and its census ------------------------------------------------


async def test_image_only_items_get_one_census_note(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import base64

    pixel = base64.b64encode(b"px").decode()
    record = json.dumps({"__media": {"kind": "image", "mime": "image/png", "data_b64": pixel}})
    code, out = await _run(
        GraphRequest(fast=True),
        _context(PEOPLE),
        f"{record}\n{record}\nAnn met Bob\n",
    )
    assert code is ExitCode.PARTIAL
    err = capsys.readouterr().err
    assert (
        "note: 2 files skipped — no free text (images/scans); the full mode or ocr-model reads them"
    ) in err
    assert len([json.loads(line) for line in out.splitlines()]) == 1


async def test_all_image_only_items_exit_nonzero() -> None:
    import base64

    pixel = base64.b64encode(b"px").decode()
    record = json.dumps({"__media": {"kind": "image", "mime": "image/png", "data_b64": pixel}})
    code, out = await _run(GraphRequest(fast=True), _context(PEOPLE), f"{record}\n{record}\n")
    assert code is ExitCode.ALL_FAILED
    assert out == ""


async def test_audio_items_ride_the_local_whisper_rung() -> None:
    import base64

    clip = base64.b64encode(b"riff").decode()
    record = json.dumps({"__media": {"kind": "audio", "mime": "audio/mpeg", "data_b64": clip}})
    heard: list[bytes] = []

    def fake_whisper(audio: object) -> str:
        heard.append(getattr(audio, "data", b""))
        return "Ann met Bob"

    out = io.StringIO()
    code = await run_graph(
        GraphRequest(fast=True),
        _context(PEOPLE),
        stdin=io.StringIO(f"{record}\n"),
        stdout=out,
        transcriber=fake_whisper,
        clock=lambda: 0.0,
    )
    assert code is ExitCode.OK
    assert heard == [b"riff"]  # the clip reached the LOCAL transcriber, not a model
    edges = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [(e["source"], e["target"]) for e in edges] == [("Ann", "Bob")]


# --- the stt-model role in the scanning modes (#20) ---------------------------------


def _audio_record(data: bytes = b"riff") -> str:
    import base64

    clip = base64.b64encode(data).decode()
    return json.dumps({"__media": {"kind": "audio", "mime": "audio/mpeg", "data_b64": clip}})


async def test_stt_flag_without_a_scan_mode_refuses() -> None:
    """The pairing guard (verbatim): full and adopt refuse the flag at USAGE —
    only the scanning modes read audio themselves."""
    with pytest.raises(UsageFault, match="--stt-model rides the scanning modes"):
        await _run(
            GraphRequest(focus="who pays whom", stt_model_flag="openai/whisper-1"), _context()
        )
    with pytest.raises(UsageFault, match="pair it with --fast or --name-top"):
        await _run(GraphRequest(stt_model_flag="openai/whisper-1"), _context())


async def test_bare_fast_resolves_the_ladder_once_and_stays_quiet(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bare ``--fast``: ONE flagless resolution (no chat ref — an ambient OpenAI
    key never flips the free mode remote) and no paid-transcription note."""
    context = _context(PEOPLE)
    code, _ = await _run(GraphRequest(fast=True), context, "Ann met Bob\n")
    assert code is ExitCode.OK
    assert context.stt_flags == [None]
    assert "paid transcription" not in capsys.readouterr().err


async def test_stt_flag_threads_to_the_resolution() -> None:
    context = _context(PEOPLE)
    code, _ = await _run(
        GraphRequest(fast=True, stt_model_flag="openai/whisper-1"), context, "Ann met Bob\n"
    )
    assert code is ExitCode.OK
    assert context.stt_flags == ["openai/whisper-1"]


async def test_configured_stt_transcribes_the_scan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The resolved wire hears every clip; the injectable whisper callable never
    runs (with a converter present, ensure_text bypasses it); each row carries
    the converter's note and the run carries EXACTLY ONE paid disclosure."""
    context = _context(PEOPLE)
    stt = FakeTranscriber("openai/whisper-1")
    context.stt = stt
    heard_by_whisper: list[bytes] = []

    def fake_whisper(audio: object) -> str:  # pragma: no cover - the assertion is []
        heard_by_whisper.append(getattr(audio, "data", b""))
        return "never"

    out = io.StringIO()
    code = await run_graph(
        GraphRequest(fast=True, stt_model_flag="openai/whisper-1"),
        context,
        stdin=io.StringIO(f"{_audio_record()}\n{_audio_record(b'more')}\n"),
        stdout=out,
        transcriber=fake_whisper,
        clock=lambda: 0.0,
    )
    assert code is ExitCode.OK
    assert stt.heard == [b"riff", b"more"]
    assert heard_by_whisper == []
    err = capsys.readouterr().err
    assert err.count("transcribing audio via openai/whisper-1 (paid transcription)") == 1
    assert "transcribed by openai/whisper-1" in err
    edges = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [(e["source"], e["target"]) for e in edges] == [("Ann", "Bob")]


async def test_local_stt_resolution_never_discloses_paid_transcription(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """stt-model = "local" rides the same seam but is free — the run-level
    paid disclosure must NOT fire for an on-device wire."""
    context = _context(PEOPLE)
    context.stt = FakeTranscriber("local/whisper-tiny")
    out = io.StringIO()
    code = await run_graph(
        GraphRequest(fast=True),
        context,
        stdin=io.StringIO(f"{_audio_record()}\n"),
        stdout=out,
        clock=lambda: 0.0,
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert "paid transcription" not in err
    assert "transcribed by local/whisper-tiny" in err  # the per-row note still tells


async def test_stt_with_a_text_only_corpus_stays_quiet(
    capsys: pytest.CaptureFixture[str],
) -> None:
    context = _context(PEOPLE)
    context.stt = FakeTranscriber("openai/whisper-1")
    code, _ = await _run(
        GraphRequest(fast=True, stt_model_flag="openai/whisper-1"), context, "Ann met Bob\n"
    )
    assert code is ExitCode.OK
    assert "paid transcription" not in capsys.readouterr().err


async def test_stt_failure_falls_to_the_whisper_rung(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recoverable stt hiccup continues the audio ladder — asserted via the
    LADDER'S own note (never the injectable callable: with a converter present
    the whisper rung is ``_whisper_or_skip``, which ignores it)."""
    from smartpipe.parsing import extract

    def fake_transcribe(audio: AudioData) -> str:
        del audio
        return "Ann met Bob"

    monkeypatch.setattr(extract, "transcribe_audio", fake_transcribe)
    context = _context(PEOPLE)
    stt = FakeTranscriber("openai/whisper-1", fail=True)
    context.stt = stt
    out = io.StringIO()
    code = await run_graph(
        GraphRequest(fast=True),
        context,
        stdin=io.StringIO(f"{_audio_record()}\n"),
        stdout=out,
        clock=lambda: 0.0,
    )
    assert code is ExitCode.OK
    assert stt.heard == [b"riff"]  # the wired rung was TRIED before the ladder fell through
    assert "(whisper tiny)" in capsys.readouterr().err  # the ladder's note, not the fake's
    edges = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [(e["source"], e["target"]) for e in edges] == [("Ann", "Bob")]


async def test_stt_preflight_faults_before_stdin_is_read() -> None:
    """The scan-mode resolve IS the preflight: a broken stt config (missing key,
    unsupported wire, --local-only) faults at SETUP before a byte is read."""

    class _UnreadStdin(io.StringIO):
        def read(self, size: int | None = -1) -> str:  # pragma: no cover - the raise is the test
            raise AssertionError("stdin was read before the stt preflight")

        def readline(self, size: int = -1) -> str:  # pragma: no cover
            raise AssertionError("stdin was read before the stt preflight")

    @dataclass
    class _FaultingSttContext(FakeContext):
        def remote_transcriber(self, *, flag: str | None = None) -> Transcriber | None:
            del flag
            raise SetupFault("error: remote transcription needs OPENAI_API_KEY")

    context = _FaultingSttContext(finder=FakeFinder(PEOPLE), embedder=FakeEmbedder({}))
    with pytest.raises(SetupFault, match="OPENAI_API_KEY"):
        await run_graph(
            GraphRequest(fast=True, stt_model_flag="openai/whisper-1"),
            context,
            stdin=_UnreadStdin("Ann met Bob\n"),
            stdout=io.StringIO(),
            clock=lambda: 0.0,
        )


async def test_fast_receipt_reports_real_spend_once_stt_meters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The --fast receipt reads the live meter (receipt_tail): a paid
    transcription can no longer hide behind a hardcoded ``· 0 tok``."""
    from smartpipe.io import metering

    class _MeteredTranscriber(FakeTranscriber):
        async def transcribe(self, audio: AudioData) -> str:
            metering.add_conversion()  # what the real remote wire does per clip
            return await super().transcribe(audio)

    context = _context(PEOPLE)
    context.stt = _MeteredTranscriber("openai/whisper-1")
    out = io.StringIO()
    code = await run_graph(
        GraphRequest(fast=True, stt_model_flag="openai/whisper-1"),
        context,
        stdin=io.StringIO(f"{_audio_record()}\n"),
        stdout=out,
        clock=lambda: 0.0,
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert "· 0 tok" not in err
    assert "1 paid conversions" in err  # metering.receipt()'s own wording rides the tail


async def test_finder_failures_skip_that_item_loudly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = await _run(
        GraphRequest(fast=True),
        _context(PEOPLE, poison="radioactive"),
        "Ann met Bob\nradioactive line\n",
    )
    assert code is ExitCode.PARTIAL
    assert "skipped: line 2" in capsys.readouterr().err
    assert len(out.splitlines()) == 1


# --- the zero-model-call pin --------------------------------------------------------


async def test_fast_never_touches_chat_or_the_configured_embedder() -> None:
    context = _context(PEOPLE)
    code, _ = await _run(GraphRequest(fast=True), context, "Ann met Bob at Acme\nBob saw Ann\n")
    assert code is ExitCode.OK
    assert context.chat_calls == 0  # would have raised AssertionError if asked


# --- B1: the CPU-bound fold honors a stop -------------------------------------------

_THREE_PAIRS = "Ann met Bob\nCarol met Dave\nEve met Frank\n"
_SIX_NAMES: dict[str, str] = dict.fromkeys(
    ("Ann", "Bob", "Carol", "Dave", "Eve", "Frank"), "person"
)


async def test_should_stop_cuts_the_fold_and_salvages_without_crashing() -> None:
    # B1: the synchronous stop predicate reaches the fold (through to_thread). A
    # stop that fires immediately cuts every fold window, so the salvaged graph is
    # empty — but the run still completes and writes it, never a crash.
    out = io.StringIO()
    code = await run_graph(
        GraphRequest(fast=True),
        _context(_SIX_NAMES),
        stdin=io.StringIO(_THREE_PAIRS),
        stdout=out,
        should_stop=lambda: True,
        clock=lambda: 0.0,
    )
    assert code is ExitCode.OK  # no drained async stop → normal exit on the partial
    assert out.getvalue() == ""  # the fold was cut before a single edge folded
    # control: the same corpus without the stop folds all three co-occurrence edges
    control_code, control_out = await _run(
        GraphRequest(fast=True), _context(_SIX_NAMES), _THREE_PAIRS
    )
    assert control_code is ExitCode.OK
    assert len(control_out.splitlines()) == 3


def test_fold_phase_note_names_the_fold_only_on_large_corpora(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # B1: the fold is a distinct phase that can dominate; past a threshold it is
    # named so a user isn't left wondering why the run keeps going after entities.
    from smartpipe.verbs import graph as graph_module

    threshold: int = graph_module._FOLD_NOTE_WINDOWS  # pyright: ignore[reportPrivateUsage] — pin
    graph_module._note_fold_phase(threshold - 1)  # pyright: ignore[reportPrivateUsage] — under test
    assert "folding" not in capsys.readouterr().err  # small graphs stay quiet
    graph_module._note_fold_phase(threshold)  # pyright: ignore[reportPrivateUsage] — under test
    err = capsys.readouterr().err
    assert f"entities done — folding {threshold:,} windows" in err
    assert "this can dominate on large corpora" in err


async def test_drained_stop_mid_scan_salvages_and_exits_interrupted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # B1: a drained Ctrl-C during the scan — the entities gathered before the stop
    # are folded and written, and the exit reflects the partial (ux.md §12).
    import asyncio

    stop = asyncio.Event()
    context = _context(_SIX_NAMES)
    seen = 0
    plain_find = context.finder.find

    def find(text: str) -> tuple[EntitySpan, ...]:
        nonlocal seen
        spans = plain_find(text)
        seen += 1
        if seen == 2:  # after two items are read, the drain trips (no waiters: safe)
            stop.set()
        return spans

    context.finder.find = find  # type: ignore[method-assign]
    out = io.StringIO()
    code = await run_graph(
        GraphRequest(fast=True),
        context,
        stdin=io.StringIO(_THREE_PAIRS),
        stdout=out,
        stop=stop,
        clock=lambda: 0.0,
    )
    assert code is ExitCode.PARTIAL  # two of three files done — an honest partial
    assert "interrupted" in capsys.readouterr().err
    edges = [json.loads(line) for line in out.getvalue().splitlines()]
    assert {(e["source"], e["target"]) for e in edges} == {("Ann", "Bob"), ("Carol", "Dave")}


# --- projected-time honesty ----------------------------------------------------------


async def test_slow_pace_projection_drops_progress_clause_when_stderr_is_piped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B3: capsys makes stderr a non-TTY, so the status bar is OFF. The projection
    must stay truthful — the '(progress below …)' clause is dropped rather than
    promising a bar that will never appear (graph.py:_note_projected_grind keys the
    clause on the entity bar's own ``enabled`` flag)."""
    ticks = iter([0.0] + [100.0] * 200)
    out = io.StringIO()
    lines = "".join(f"Ann {n}\n" for n in range(40))
    code = await run_graph(
        GraphRequest(fast=True),
        _context(PEOPLE),
        stdin=io.StringIO(lines),
        stdout=out,
        clock=lambda: next(ticks),
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert "note: ~40 files at this machine's pace — roughly 3 min" in err
    assert "progress below" not in err  # no bar → no promise of one


async def test_slow_pace_projection_promises_progress_when_the_bar_is_on(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """B3: when stderr IS a TTY the status bar animates, so the projection may
    honestly point at it — the '(progress below; Ctrl-C is safe)' clause returns.
    NO_COLOR keeps the captured note free of the dim-wrap ANSI codes."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("smartpipe.io.tty.stderr_is_tty", lambda: True)
    ticks = iter([0.0] + [100.0] * 200)
    out = io.StringIO()
    lines = "".join(f"Ann {n}\n" for n in range(40))
    code = await run_graph(
        GraphRequest(fast=True),
        _context(PEOPLE),
        stdin=io.StringIO(lines),
        stdout=out,
        clock=lambda: next(ticks),
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert (
        "note: ~40 files at this machine's pace — roughly 3 min (progress below; Ctrl-C is safe)"
    ) in err


async def test_quick_pace_stays_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    ticks = iter([0.0] + [1.0] * 200)
    out = io.StringIO()
    lines = "".join(f"Ann {n}\n" for n in range(40))
    await run_graph(
        GraphRequest(fast=True),
        _context(PEOPLE),
        stdin=io.StringIO(lines),
        stdout=out,
        clock=lambda: next(ticks),
    )
    assert "at this machine's pace" not in capsys.readouterr().err


# --- --save ---------------------------------------------------------------------------


def _save_request(save: str, top: int | None = None) -> GraphRequest:
    return GraphRequest(fast=True, save=save, top=top)


CORPUS = "Ann met Bob at Acme\nBob saw Ann\nAcme hired Ann\n"


async def test_save_graphml_dot_html_write_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("g.graphml", "g.dot", "g.html", "g.mmd"):
        path = tmp_path / name
        await _run(_save_request(str(path)), _context(PEOPLE), CORPUS)
        assert path.exists()
        assert f"graph saved: {path}" in capsys.readouterr().err


async def test_save_csv_writes_the_pair_side_by_side(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await _run(_save_request(str(tmp_path / "g.csv")), _context(PEOPLE), CORPUS)
    nodes = (tmp_path / "g.nodes.csv").read_text(encoding="utf-8")
    edges = (tmp_path / "g.edges.csv").read_text(encoding="utf-8")
    assert nodes.startswith("id,label,count\n")
    assert edges.startswith("source,target,relation,weight\n")
    assert "graph saved:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("save_name", "manifest_name"),
    (
        ("g.graphml", "g.graphml"),
        ("g.csv", "g.nodes.csv"),
        ("g.csv", "g.edges.csv"),
    ),
)
async def test_every_static_save_output_refuses_a_manifest_alias_before_work(
    tmp_path: Path,
    save_name: str,
    manifest_name: str,
) -> None:
    from smartpipe.io import manifest

    target = tmp_path / manifest_name
    original = "irreplaceable manifest\n"
    target.write_text(original, encoding="utf-8")
    manifest.reset()
    manifest.begin(target, verb="graph", argv=("graph",))
    context = _context(PEOPLE)

    with pytest.raises(UsageFault, match="aliases --save output"):
        await _run(_save_request(str(tmp_path / save_name)), context, CORPUS)

    assert context.finder.calls == []
    assert target.read_text(encoding="utf-8") == original


async def test_vault_save_refuses_a_manifest_inside_its_output_tree_before_work(
    tmp_path: Path,
) -> None:
    from smartpipe.io import manifest

    vault = tmp_path / "vault"
    vault.mkdir()
    target = vault / "index.md"
    original = "irreplaceable manifest\n"
    target.write_text(original, encoding="utf-8")
    manifest.reset()
    manifest.begin(target, verb="graph", argv=("graph",))
    context = _context(PEOPLE)

    with pytest.raises(UsageFault, match="inside --save vault"):
        await _run(_save_request(f"{vault}/"), context, CORPUS)

    assert context.finder.calls == []
    assert target.read_text(encoding="utf-8") == original


async def test_save_vault_writes_notes_and_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = tmp_path / "vault"
    await _run(_save_request(f"{vault}/"), _context(PEOPLE), CORPUS)
    assert (vault / "index.md").exists()
    assert (vault / "Ann.md").exists()
    assert "(4 notes)" in capsys.readouterr().err


async def test_top_caps_display_formats_and_mermaid_notes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mmd = tmp_path / "g.mmd"
    await _run(_save_request(str(mmd), top=2), _context(PEOPLE), CORPUS)
    text = mmd.read_text(encoding="utf-8")
    assert "Bob" not in text  # the smallest hub fell off
    assert "mermaid capped to the 2 biggest hubs of 3 nodes" in capsys.readouterr().err

    html = tmp_path / "g.html"
    await _run(_save_request(str(html), top=1), _context(PEOPLE), CORPUS)
    assert '"id": "Bob"' not in html.read_text(encoding="utf-8")


# --- fold embedder disclosure: a non-local fold is a paid spend, so say so ----


class _RefEmbedder:
    """A stand-in embedder that reports whatever ref it is told to — the
    disclosure keys on ``ref.provider``, so this drives both branches."""

    def __init__(self, ref_text: str) -> None:
        from smartpipe.models.base import parse_model_ref

        self._ref = parse_model_ref(ref_text)

    @property
    def ref(self) -> ModelRef:
        return self._ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(index),) * 4 for index, _ in enumerate(texts))


@dataclass
class _FoldOnlyContext:
    """The minimal fold seam: records the embed flag it is handed and returns
    a fixed embedder — the paid-modes' fold path in miniature."""

    embedder: _RefEmbedder
    seen_flags: list[str | None] = field(default_factory=list["str | None"])

    def entity_finder(self, labels: Sequence[str]) -> FakeFinder:  # pragma: no cover - unused
        raise AssertionError("fold_vectors must not resolve the NER model")

    async def fold_embedder(self, flag: str | None = None) -> _RefEmbedder:
        self.seen_flags.append(flag)
        return self.embedder

    def remote_transcriber(self, *, flag: str | None = None) -> Transcriber | None:
        del flag  # the fold never transcribes; conformance keeps pyright honest
        return None


async def test_fold_vectors_discloses_a_paid_non_local_embedder(
    capsys: pytest.CaptureFixture[str],
) -> None:
    context = _FoldOnlyContext(_RefEmbedder("openai/text-embedding-3-small"))
    outcome = await fold_vectors(context, ["Alice", "Bob"])
    assert set(outcome.vectors) == {"Alice", "Bob"}
    assert outcome.cut is FoldCut.NONE
    err = capsys.readouterr().err
    assert "folding 2 entity names via openai/text-embedding-3-small (paid embeddings)" in err


async def test_fold_vectors_stays_quiet_for_a_local_embedder(
    capsys: pytest.CaptureFixture[str],
) -> None:
    context = _FoldOnlyContext(_RefEmbedder("local/nomic-embed-text-v1.5"))
    await fold_vectors(context, ["Alice", "Bob"])
    assert "paid embeddings" not in capsys.readouterr().err


async def test_fold_vectors_stays_quiet_for_a_loopback_ollama_embedder(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ollama is free (loopback or self-hosted), like the codebase treats it
    everywhere else (convert.py, fence.py). On Python 3.14+ (no fastembed
    wheels) the free DEFAULT fold resolves to ``ollama/nomic-embed-text`` — it
    must NOT be mislabeled 'paid embeddings' on a bare ``--fast`` run."""
    context = _FoldOnlyContext(_RefEmbedder("ollama/nomic-embed-text"))
    await fold_vectors(context, ["Alice", "Bob"])
    assert "paid embeddings" not in capsys.readouterr().err


async def test_fold_vectors_threads_the_embed_flag_to_the_context() -> None:
    context = _FoldOnlyContext(_RefEmbedder("local/nomic-embed-text-v1.5"))
    await fold_vectors(context, ["Alice", "Bob"], embed_flag="openai/text-embedding-3-large")
    assert context.seen_flags == ["openai/text-embedding-3-large"]


# --- #30: paid work is never lost — the fold salvages on ANY cut ----------------------

_MANY_NAMES = tuple(f"N{n}" for n in range(65))  # 65 names → a full 64-batch plus one


@dataclass
class _CutFoldContext:
    """The minimal fold seam for the salvage tests — the embedder field is typed
    to the protocol so budgeted wrappers ride in under pyright strict."""

    embedder: EmbeddingModel

    def entity_finder(self, labels: Sequence[str]) -> FakeFinder:  # pragma: no cover - unused
        raise AssertionError("fold_vectors must not resolve the NER model")

    async def fold_embedder(self, flag: str | None = None) -> EmbeddingModel:
        del flag
        return self.embedder

    def remote_transcriber(self, *, flag: str | None = None) -> Transcriber | None:
        del flag  # the fold never transcribes; conformance keeps pyright honest
        return None


class _DiesOnSecondBatch:
    """Embeds the first batch cleanly, then raises the scripted exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self._inner = FakeEmbedder({})
        self.batches = 0

    @property
    def ref(self) -> ModelRef:
        return self._inner.ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.batches += 1
        if self.batches > 1:
            raise self._exc
        return await self._inner.embed(texts)


async def test_interrupt_mid_fold_keeps_the_embedded_batch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A drained Ctrl-C between batches keeps every vector already embedded —
    the poll is per batch, on the synchronous predicate only."""
    embedder = FakeEmbedder({})
    flips = iter((False, True))
    outcome = await fold_vectors(
        _CutFoldContext(embedder), _MANY_NAMES, should_stop=lambda: next(flips)
    )
    assert outcome.cut is FoldCut.INTERRUPT
    assert set(outcome.vectors) == set(_MANY_NAMES[:64])  # batch one salvaged, never reset
    assert embedder.batches == [list(_MANY_NAMES[:64])]  # batch two was never sent
    assert (
        "entity folding interrupted — 64 of 65 names embedded; the rest keep their spelling"
    ) in capsys.readouterr().err


async def test_mid_fold_item_error_salvages_and_says_stopped_early(
    capsys: pytest.CaptureFixture[str],
) -> None:
    embedder = _DiesOnSecondBatch(ItemError("onnx died mid-run"))
    outcome = await fold_vectors(_CutFoldContext(embedder), _MANY_NAMES)
    assert outcome.cut is FoldCut.FAULT
    assert len(outcome.vectors) == 64  # the embedded batch is kept, never reset
    assert (
        "entity folding stopped early (onnx died mid-run) — 64 of 65 names embedded; "
        "the rest keep their spelling"
    ) in capsys.readouterr().err


async def test_mid_fold_wire_death_degrades_instead_of_killing_the_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ruling 5: a runtime SetupFault (the wire died mid-run) degrades to a
    partial fold — build faults stay fatal via the #27 preflight, not here."""
    embedder = _DiesOnSecondBatch(SetupFault("the embed wire died mid-run"))
    outcome = await fold_vectors(_CutFoldContext(embedder), _MANY_NAMES)
    assert outcome.cut is FoldCut.FAULT
    assert len(outcome.vectors) == 64
    assert (
        "entity folding stopped early (the embed wire died mid-run) — 64 of 65 names embedded"
    ) in capsys.readouterr().err


async def test_belt_cut_mid_fold_is_belt_not_fault(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """UnsentError is an ItemError subclass: the BELT arm must catch it FIRST,
    or the belt cut is indistinguishable from a wire fault."""
    import asyncio

    from smartpipe.models.budget import CallBudget, budgeted_embed

    budget = CallBudget(limit=1, stop=asyncio.Event())
    outcome = await fold_vectors(
        _CutFoldContext(budgeted_embed(FakeEmbedder({}), budget)), _MANY_NAMES
    )
    assert outcome.cut is FoldCut.BELT
    assert len(outcome.vectors) == 64  # the paid batch is kept — never re-spent
    assert (
        "entity folding stopped early (call budget reached (--max-calls 1)) — "
        "64 of 65 names embedded; the rest keep their spelling"
    ) in capsys.readouterr().err


async def test_nothing_embedded_keeps_the_pinned_skip_wording(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The empty-case wording stays byte-identical to the pre-#30 disclosure."""
    outcome = await fold_vectors(_CutFoldContext(FakeEmbedder({}, broken=True)), ["Ann", "Bob"])
    assert outcome.cut is FoldCut.FAULT
    assert outcome.vectors == {}
    assert (
        "entity folding skipped (local embedding failed (onnx says no)) — "
        "every surface form keeps its node"
    ) in capsys.readouterr().err


async def test_fewer_than_two_names_is_a_none_cut() -> None:
    outcome = await fold_vectors(_CutFoldContext(FakeEmbedder({})), ["Ann"])
    assert outcome == FoldOutcome(vectors={}, cut=FoldCut.NONE)


def test_fold_cut_flips_partial_only_for_the_belt() -> None:
    """#29 ruling: only a BELT cut flips the exit — an interrupt already exits
    by the drain rules, and a fault exits by the run's normal counts."""
    assert fold_cut_flips_partial(FoldCut.BELT) is True
    assert fold_cut_flips_partial(FoldCut.NONE) is False
    assert fold_cut_flips_partial(FoldCut.INTERRUPT) is False
    assert fold_cut_flips_partial(FoldCut.FAULT) is False


# --- C6 #34: the density hint — a near-complete graph is window math, not signal ------

_TWENTY_FOUR = tuple(f"P{n:02d}" for n in range(1, 25))  # no name a substring of another


def test_dense_graph_hint_thresholds_are_structural(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A1 (#34): the hint keys on ≥20 nodes AND kept/C(n,2) ≥ 0.8 — structural
    thresholds, never corpus-shaped. 19 fully dense nodes stay quiet (the node
    floor), 20 nodes at 151 of 190 stay quiet (under the density line), 152 of
    190 fires (the 0.8 boundary EXACTLY — a Codex-review pin, green from
    birth), and 190 of 190 fires the exact pinned wording."""
    from smartpipe.verbs.graph import note_dense_graph

    note_dense_graph(19, 171)  # C(19,2) = 171: complete, but under the node floor
    assert capsys.readouterr().err == ""
    note_dense_graph(20, 151)  # 151/190 ≈ 0.79: dense, but under the density line
    assert capsys.readouterr().err == ""
    note_dense_graph(20, 152)  # 152/190 = 0.8 exactly: ≥ means the boundary fires
    assert "note: near-complete graph (152 of 190 possible edges)" in capsys.readouterr().err
    note_dense_graph(20, 190)
    assert (
        "note: near-complete graph (190 of 190 possible edges) — everything co-occurs "
        "with everything; --window sentence tightens it, then --min-weight 2 keeps "
        "recurring pairs"
    ) in capsys.readouterr().err


async def test_one_window_corpus_fires_the_hint_before_the_receipt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A2 (#34): 24 names in ONE line is one chunk window, so every pair
    co-occurs — a complete graph, C(24,2) = 276 of 276. The hint fires, and
    BEFORE the receipt, so the explanation lands beside the numbers it
    explains (the owner's one-MP3 hairball, in miniature)."""
    known = dict.fromkeys(_TWENTY_FOUR, "person")
    code, out = await _run(GraphRequest(fast=True), _context(known), " ".join(_TWENTY_FOUR) + "\n")
    assert code is ExitCode.OK
    assert len(out.splitlines()) == 276  # the complete graph reached stdout intact
    err = capsys.readouterr().err
    hint = err.index("note: near-complete graph (276 of 276 possible edges)")
    receipt = err.index("note: graph: 24 entities")
    assert hint < receipt


async def test_sparse_spread_never_fires_the_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A3 (#34): the same 24 names spread pairwise across lines chain into 23
    of 276 possible edges — plenty of nodes, honest sparsity, no hint."""
    from itertools import pairwise

    known = dict.fromkeys(_TWENTY_FOUR, "person")
    corpus = "".join(f"{a} met {b}\n" for a, b in pairwise(_TWENTY_FOUR))
    code, _ = await _run(GraphRequest(fast=True), _context(known), corpus)
    assert code is ExitCode.OK
    assert "near-complete graph" not in capsys.readouterr().err


# --- C2 #37: every fold phase owns a visible element --------------------------------


async def test_fold_bar_zero_state_lands_before_the_first_embed_batch(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#37 integration pin (green via D1): the embed fold's [fold] bar paints its
    0% zero state BEFORE the first embed batch returns — that batch is the one
    an admission cooldown (≤60s) plus the retry ladder can hold for minutes,
    and it used to be the bar's FIRST byte."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("smartpipe.io.tty.stderr_is_tty", lambda: True)
    stderr_at_first_batch: list[str] = []

    class SnappingEmbedder(FakeEmbedder):
        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            if not stderr_at_first_batch:
                stderr_at_first_batch.append(capsys.readouterr().err)
            return await super().embed(texts)

    context = FakeContext(finder=FakeFinder(dict(PEOPLE)), embedder=SnappingEmbedder({}))
    out = io.StringIO()
    code = await run_graph(
        GraphRequest(fast=True), context, stdin=io.StringIO("Ann met Bob\n"), stdout=out
    )
    assert code is ExitCode.OK
    (snapshot,) = stderr_at_first_batch
    assert any(
        "[fold]" in frame and "0% · 0/2" in frame for frame in snapshot.split("\r")
    )  # the zero state was already on screen when the first batch went out


async def test_fast_surface_fold_owns_a_visible_element(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#37 D4: the label-cluster fold (fold_surfaces, off the loop, no item count
    of its own) owns a [fold] count line — painted at start, advanced per label
    group — so the quadratic phase never runs faceless."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("smartpipe.io.tty.stderr_is_tty", lambda: True)
    out = io.StringIO()
    code = await run_graph(
        GraphRequest(fast=True), _context(PEOPLE), stdin=io.StringIO("Ann met Bob\n"), stdout=out
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert any(
        "[fold]" in frame and "Processing [0]" in frame for frame in err.split("\r")
    )  # the surface fold's zero state wore the fold label
