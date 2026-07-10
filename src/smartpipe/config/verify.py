"""The config flow's exit probe: verify what the chosen models can ACTUALLY do.

~5 tiny paid requests, consented first (default yes). THE CONTROL RULE
(owner-pinned): the chat model's text-only ping runs FIRST; if that control
fails, probing STOPS entirely and the report is a SETUP fault (key/endpoint) -
nothing is concluded about modalities. Only capability-class refusals AFTER a
passing control mark a modality absent (the breaker's transport-vs-content
taxonomy, reused). Results land in the same dated chips cache that
``doctor --probe`` writes, and a closing matrix screen shows model x
text/image/audio with ✓ / ✗ reason / - not attempted.

All probing logic here is pure over injected models; the live wiring in
``cli/config_cmd`` builds real adapters and is pragma-excluded like doctor's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ItemError, SempipeError, TransportError
from smartpipe.models.base import (
    AudioData,
    CompletionRequest,
    ImageData,
    supports_media_embedding,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from smartpipe.config.picker import ProbeChip
    from smartpipe.models.base import ChatModel, EmbeddingModel

__all__ = [
    "Attempt",
    "VerifyReport",
    "probe_models",
    "render_verify_matrix",
    "run_exit_probe",
]

_NOT_ATTEMPTED = "not attempted"
_NO_AUDIO_EMBED = "never attempted (no ecosystem wire)"


@dataclass(frozen=True, slots=True)
class Attempt:
    """One cell's verdict: '✓' needs no words, the others carry their reason."""

    mark: str  # "ok" | "no" | "skip"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class VerifyReport:
    chat_ref: str | None
    chat_text: Attempt
    chat_image: Attempt
    chat_audio: Attempt
    embed_ref: str | None
    embed_text: Attempt
    embed_image: Attempt
    control_fault: str | None = None  # the control died - conclude NOTHING


_SKIP_CONTROL = Attempt("skip", "control failed - nothing concluded")


async def probe_models(
    chat: ChatModel | None,
    embed: EmbeddingModel | None,
    asset: Callable[[str], bytes],
) -> VerifyReport:
    """Chat: text control → image → audio. Embed: text → image only when the
    model claims media capability. Audio embedding is never attempted."""
    chat_ref = str(chat.ref) if chat is not None else None
    embed_ref = str(embed.ref) if embed is not None else None
    if chat is not None:
        try:
            await chat.complete(
                CompletionRequest(system=None, user="Reply with exactly: OK", max_tokens=8)
            )
        except SempipeError as exc:
            # THE CONTROL RULE: a dead control means key/endpoint trouble -
            # stop probing entirely, conclude nothing about modalities.
            return VerifyReport(
                chat_ref=chat_ref,
                chat_text=Attempt("no", _first_line(exc)),
                chat_image=_SKIP_CONTROL,
                chat_audio=_SKIP_CONTROL,
                embed_ref=embed_ref,
                embed_text=_SKIP_CONTROL,
                embed_image=_SKIP_CONTROL,
                control_fault=_first_line(exc),
            )
        chat_text = Attempt("ok")
        chat_image = await _try_media(
            chat,
            "One word: what color dominates this image?",
            ImageData(asset("probe.png"), "image/png"),
        )
        chat_audio = await _try_media(
            chat,
            "One word: is this sound a tone or speech?",
            AudioData(asset("probe.wav"), "audio/wav"),
        )
    else:
        chat_text = chat_image = chat_audio = Attempt("skip", "no chat model configured")
    embed_text, embed_image = await _probe_embed(embed, asset)
    return VerifyReport(
        chat_ref=chat_ref,
        chat_text=chat_text,
        chat_image=chat_image,
        chat_audio=chat_audio,
        embed_ref=embed_ref,
        embed_text=embed_text,
        embed_image=embed_image,
    )


async def _try_media(chat: ChatModel, prompt: str, media: ImageData | AudioData) -> Attempt:
    """After a PASSING control: a content refusal is a capability verdict; a
    wire failure concludes nothing (transport vs content - the breaker's line)."""
    try:
        await chat.complete(
            CompletionRequest(system=None, user=prompt, media=(media,), max_tokens=8)
        )
        return Attempt("ok")
    except TransportError:
        return Attempt("skip", "wire trouble - not concluded")
    except ItemError as exc:
        return Attempt("no", _first_line(exc))
    except SempipeError:
        return Attempt("skip", "wire trouble - not concluded")


async def _probe_embed(
    embed: EmbeddingModel | None, asset: Callable[[str], bytes]
) -> tuple[Attempt, Attempt]:
    if embed is None:
        skip = Attempt("skip", "no embed model configured")
        return skip, skip
    try:
        await embed.embed(["ok"])
    except SempipeError as exc:
        # the embed model's own control: a dead text embed concludes nothing
        return Attempt("no", _first_line(exc)), Attempt("skip", "control failed")
    if not supports_media_embedding(embed):
        return Attempt("ok"), Attempt("skip", "no media claim - " + _NOT_ATTEMPTED)
    try:
        await embed.embed_parts([ImageData(asset("probe.png"), "image/png")])
        return Attempt("ok"), Attempt("ok")
    except TransportError:
        return Attempt("ok"), Attempt("skip", "wire trouble - not concluded")
    except ItemError as exc:
        return Attempt("ok"), Attempt("no", _first_line(exc))
    except SempipeError:
        return Attempt("ok"), Attempt("skip", "wire trouble - not concluded")


def _first_line(exc: SempipeError) -> str:
    return str(exc).splitlines()[0].removeprefix("error: ")


# --- the closing matrix -------------------------------------------------------------------


def render_verify_matrix(report: VerifyReport) -> str:
    """model x text/image/audio - ✓ / ✗ reason / - not attempted."""
    from smartpipe.cli.screens import bad, good, heading, tint

    rows: list[tuple[str, Attempt, Attempt, Attempt]] = []
    if report.chat_ref is not None:
        rows.append((report.chat_ref, report.chat_text, report.chat_image, report.chat_audio))
    if report.embed_ref is not None:
        rows.append(
            (
                report.embed_ref,
                report.embed_text,
                report.embed_image,
                Attempt("skip", _NO_AUDIO_EMBED),
            )
        )
    if not rows:
        return tint("  nothing to verify - no models configured", "2")
    label_width = max(len(ref) for ref, *_cells in rows) + 3
    cap = 26

    def cell_text(attempt: Attempt) -> str:
        match attempt.mark:
            case "ok":
                return "✓"
            case "no":
                return f"✗ {attempt.reason}"[: cap + 2]
            case _:
                return f"- {attempt.reason}"[: cap + 2]

    def paint(attempt: Attempt, text: str) -> str:
        match attempt.mark:
            case "ok":
                return good(text)
            case "no":
                return bad(text[:1]) + text[1:]
            case _:
                return tint(text, "2")

    width = max(len(cell_text(cell)) for _ref, *cells in rows for cell in cells) + 3
    header = (
        "  "
        + " " * label_width
        + "".join(heading(_pad(name, width)) for name in ("text", "image", "audio"))
    )
    lines = [header]
    for ref, *cells in rows:
        painted = "".join(_pad_ansi(paint(cell, cell_text(cell)), width) for cell in cells)
        lines.append("  " + tint(ref.ljust(label_width), "2") + painted)
    return "\n".join(line.rstrip() for line in lines)


def _pad(text: str, width: int) -> str:
    return f"{text:{width}s}"


def _pad_ansi(text: str, width: int) -> str:
    import re

    visible = len(re.sub(r"\x1b\[[0-9;]*m", "", text))
    return text + " " * max(0, width - visible)


# --- consent + cache (the injected-I/O flow config_cmd drives) ---------------------------


def _freshest_age_days(
    chips: Mapping[str, ProbeChip], refs: tuple[str | None, ...], now: float
) -> int | None:
    stamps = [chips[ref].ts for ref in refs if ref is not None and ref in chips]
    if not stamps:
        return None
    return int(max(0.0, now - max(stamps)) // 86_400)


async def run_exit_probe(
    *,
    chat_ref: str | None,
    embed_ref: str | None,
    chips: Mapping[str, ProbeChip],
    now: float,
    confirm: Callable[[str, bool], bool],
    say: Callable[[str], None],
    probe: Callable[[], Awaitable[VerifyReport]],
    record: Callable[[str, bool, bool], None],
) -> None:
    """Consent (default YES; a fresh probe flips it to a re-verify no), probe,
    record chips, and print the matrix. A control fault records NOTHING."""
    from smartpipe.cli.screens import bad, tint

    if chat_ref is None and embed_ref is None:
        return
    age = _freshest_age_days(chips, (chat_ref, embed_ref), now)
    if age is not None:
        stamp = "today" if age == 0 else f"{age}d ago"
        if not confirm(f"probed {stamp} - re-verify?", False):
            return
    elif not confirm(
        "verify what these models can actually do? ~5 tiny requests, a fraction of a cent",
        True,
    ):
        return
    report = await probe()
    if report.control_fault is not None:
        say("")
        say("  " + bad("✗") + f" the text control failed: {report.control_fault}")
        say(tint("  that's a setup fault (key/endpoint) - nothing was concluded", "2"))
        say(tint("  fix the credential, then re-run: smartpipe config", "2"))
        return
    if report.chat_ref is not None and _concluded(report.chat_image, report.chat_audio):
        record(report.chat_ref, report.chat_image.mark == "ok", report.chat_audio.mark == "ok")
    if report.embed_ref is not None and report.embed_image.mark in ("ok", "no"):
        record(report.embed_ref, report.embed_image.mark == "ok", False)
    say("")
    say(render_verify_matrix(report))


def _concluded(*attempts: Attempt) -> bool:
    """Chips only claim what a probe SAW - a skipped cell poisons the record."""
    return all(attempt.mark in ("ok", "no") for attempt in attempts)
