"""The exit probe: control rule, transport-vs-content verdicts, chips, matrix."""

from __future__ import annotations

from typing import TYPE_CHECKING

from smartpipe.config.picker import ProbeChip
from smartpipe.config.verify import (
    Attempt,
    VerifyReport,
    probe_models,
    render_verify_matrix,
    run_exit_probe,
)
from smartpipe.core.errors import ItemError, SetupFault, TransportError
from smartpipe.models.base import (
    CompletionRequest,
    ImageData,
    ModelRef,
    parse_model_ref,
)
from tests.helpers.golden import assert_golden

if TYPE_CHECKING:
    from collections.abc import Sequence

_NOW = 1_751_900_000.0


def _asset(name: str) -> bytes:
    return f"<{name}>".encode()


class _Chat:
    """A scripted chat model: per-modality outcomes, every request logged."""

    def __init__(self, ref: str, outcomes: dict[str, Exception | str]) -> None:
        self.ref: ModelRef = parse_model_ref(ref)
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def complete(self, request: CompletionRequest) -> str:
        kind = "text"
        for part in request.media:
            kind = "image" if isinstance(part, ImageData) else "audio"
        self.calls.append(kind)
        outcome = self.outcomes[kind]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Embed:
    """A text-only embedder; ``_MediaEmbed`` adds the media claim."""

    def __init__(self, ref: str, outcome: Exception | None = None) -> None:
        self.ref: ModelRef = parse_model_ref(ref)
        self.outcome = outcome
        self.calls: list[str] = []

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls.append("text")
        if self.outcome is not None:
            raise self.outcome
        return tuple((0.0, 1.0) for _ in texts)


class _MediaEmbed(_Embed):
    def __init__(self, ref: str, image_outcome: Exception | None = None) -> None:
        super().__init__(ref)
        self.image_outcome = image_outcome

    async def embed_parts(self, parts: Sequence[str | ImageData]) -> tuple[tuple[float, ...], ...]:
        self.calls.append("image")
        if self.image_outcome is not None:
            raise self.image_outcome
        return tuple((0.0, 1.0) for _ in parts)


_OK_CHAT = {"text": "OK", "image": "blue", "audio": "tone"}


async def test_all_capable_is_five_tiny_requests_all_ok() -> None:
    chat = _Chat("openai/gpt-5.4-mini", dict(_OK_CHAT))
    embed = _MediaEmbed("jina/jina-clip-v2")
    report = await probe_models(chat, embed, _asset)
    assert report.chat_text == Attempt("ok")
    assert report.chat_image == Attempt("ok")
    assert report.chat_audio == Attempt("ok")
    assert report.embed_text == Attempt("ok")
    assert report.embed_image == Attempt("ok")
    assert report.control_fault is None
    assert len(chat.calls) + len(embed.calls) == 5  # the consent line's promise


async def test_control_failure_stops_everything_and_concludes_nothing() -> None:
    chat = _Chat("openai/gpt-5.4-mini", {"text": SetupFault("error: the API key was rejected")})
    embed = _MediaEmbed("jina/jina-clip-v2")
    report = await probe_models(chat, embed, _asset)
    assert report.control_fault == "the API key was rejected"
    assert chat.calls == ["text"]  # NOTHING else was attempted
    assert embed.calls == []
    assert report.chat_image.mark == "skip"
    assert report.chat_audio.mark == "skip"
    assert report.embed_text.mark == "skip"


async def test_capability_refusal_after_passing_control_marks_absent() -> None:
    chat = _Chat(
        "ollama/qwen3:8b",
        {"text": "OK", "image": ItemError("this model can't see images"), "audio": "tone"},
    )
    report = await probe_models(chat, None, _asset)
    assert report.chat_image == Attempt("no", "this model can't see images")
    assert report.chat_audio == Attempt("ok")  # a refusal never stops later probes


async def test_transport_trouble_mid_probe_concludes_nothing_for_that_cell() -> None:
    chat = _Chat(
        "openai/gpt-5.4-mini",
        {"text": "OK", "image": TransportError("openai error 502"), "audio": "tone"},
    )
    report = await probe_models(chat, None, _asset)
    assert report.chat_image.mark == "skip"
    assert "not concluded" in report.chat_image.reason


async def test_embed_text_failure_skips_the_image_attempt() -> None:
    chat = _Chat("openai/gpt-5.4-mini", dict(_OK_CHAT))
    embed = _MediaEmbed("jina/jina-clip-v2")
    embed.outcome = SetupFault("error: Jina needs an API key")
    report = await probe_models(chat, embed, _asset)
    assert report.embed_text.mark == "no"
    assert report.embed_image.mark == "skip"
    assert embed.calls == ["text"]


async def test_text_only_embedder_never_gets_an_image() -> None:
    embed = _Embed("openai/text-embedding-3-small")
    report = await probe_models(None, embed, _asset)
    assert report.embed_text == Attempt("ok")
    assert report.embed_image.mark == "skip"
    assert "no media claim" in report.embed_image.reason
    assert embed.calls == ["text"]  # text only - it claimed nothing more


async def test_media_embedder_image_refusal_is_a_verdict() -> None:
    embed = _MediaEmbed("jina/jina-clip-v2", image_outcome=ItemError("images unsupported"))
    report = await probe_models(None, embed, _asset)
    assert report.embed_image == Attempt("no", "images unsupported")


# --- consent + chips (run_exit_probe) ---------------------------------------------------


class _Session:
    def __init__(self, confirms: dict[str, bool] | None = None) -> None:
        self.confirms = confirms or {}
        self.asked: list[tuple[str, bool]] = []
        self.said: list[str] = []
        self.recorded: list[tuple[str, bool, bool]] = []
        self.probes = 0

    def confirm(self, question: str, default: bool) -> bool:
        self.asked.append((question, default))
        return self.confirms.get(question, default)

    def record(self, ref: str, sees: bool, hears: bool) -> None:
        self.recorded.append((ref, sees, hears))


def _report_all_ok() -> VerifyReport:
    return VerifyReport(
        chat_ref="openai/gpt-5.4-mini",
        chat_text=Attempt("ok"),
        chat_image=Attempt("ok"),
        chat_audio=Attempt("no", "no audio wire"),
        embed_ref="jina/jina-clip-v2",
        embed_text=Attempt("ok"),
        embed_image=Attempt("ok"),
    )


async def _run(
    session: _Session,
    report: VerifyReport,
    *,
    chips: dict[str, ProbeChip] | None = None,
    chat_ref: str | None = "openai/gpt-5.4-mini",
    embed_ref: str | None = "jina/jina-clip-v2",
) -> None:
    async def probe() -> VerifyReport:
        session.probes += 1
        return report

    await run_exit_probe(
        chat_ref=chat_ref,
        embed_ref=embed_ref,
        chips=chips or {},
        now=_NOW,
        confirm=session.confirm,
        say=session.said.append,
        probe=probe,
        record=session.record,
    )


async def test_consent_defaults_to_yes_and_names_the_cost() -> None:
    session = _Session()
    await _run(session, _report_all_ok())
    question, default = session.asked[0]
    assert question == (
        "verify what these models can actually do? ~5 tiny requests, a fraction of a cent"
    )
    assert default is True
    assert session.probes == 1


async def test_declined_consent_probes_nothing() -> None:
    question = "verify what these models can actually do? ~5 tiny requests, a fraction of a cent"
    session = _Session(confirms={question: False})
    await _run(session, _report_all_ok())
    assert session.probes == 0
    assert session.recorded == []


async def test_fresh_chips_flip_the_question_to_a_default_no_reverify() -> None:
    chips = {"openai/gpt-5.4-mini": ProbeChip(sees=True, hears=False, ts=_NOW - 3 * 86_400)}
    session = _Session()
    await _run(session, _report_all_ok(), chips=chips)
    question, default = session.asked[0]
    assert question == "probed 3d ago - re-verify?"
    assert default is False
    assert session.probes == 0  # the default answer declines


async def test_control_fault_reports_setup_and_records_nothing() -> None:
    report = VerifyReport(
        chat_ref="openai/gpt-5.4-mini",
        chat_text=Attempt("no", "the API key was rejected (401)"),
        chat_image=Attempt("skip", "control failed"),
        chat_audio=Attempt("skip", "control failed"),
        embed_ref=None,
        embed_text=Attempt("skip", "control failed"),
        embed_image=Attempt("skip", "control failed"),
        control_fault="the API key was rejected (401)",
    )
    session = _Session()
    await _run(session, report, embed_ref=None)
    assert session.recorded == []
    transcript = "\n".join(session.said)
    assert "setup fault" in transcript
    assert "nothing was concluded" in transcript


async def test_concluded_probes_record_chips_for_both_models() -> None:
    session = _Session()
    await _run(session, _report_all_ok())
    assert ("openai/gpt-5.4-mini", True, False) in session.recorded
    assert ("jina/jina-clip-v2", True, False) in session.recorded


async def test_skipped_cells_poison_the_chip_record() -> None:
    report = VerifyReport(
        chat_ref="openai/gpt-5.4-mini",
        chat_text=Attempt("ok"),
        chat_image=Attempt("ok"),
        chat_audio=Attempt("skip", "wire trouble - not concluded"),
        embed_ref=None,
        embed_text=Attempt("skip", "no embed model configured"),
        embed_image=Attempt("skip", "no embed model configured"),
    )
    session = _Session()
    await _run(session, report, embed_ref=None)
    assert session.recorded == []  # chips only claim what a probe SAW


async def test_nothing_configured_asks_nothing() -> None:
    session = _Session()
    await _run(session, _report_all_ok(), chat_ref=None, embed_ref=None)
    assert session.asked == []


# --- the matrix screen ---------------------------------------------------------------


def test_verify_matrix_screen_matches_golden() -> None:
    rendered = render_verify_matrix(_report_all_ok())
    assert_golden("config_verify_matrix", rendered + "\n")


def test_verify_matrix_marks_and_reasons() -> None:
    rendered = render_verify_matrix(_report_all_ok())
    plain = rendered
    assert "✓" in plain
    assert "✗ no audio wire" in plain
    assert "never attempted (no ecosys" in plain  # audio embed: no wire, truncated to the cell
    assert "openai/gpt-5.4-mini" in plain and "jina/jina-clip-v2" in plain
