"""``doctor --probe`` (D31/D42): the modality matrix, with real tiny calls.

``doctor`` alone never spends a cent (D18); this flag is the explicit opt-in
that answers what the docs can only claim: which modalities *actually* reach
your configured models. Marks: check = native; dash+star = works via a
footnote names it); cross = no path. Four tiny paid calls, announced first.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

from smartpipe.core.errors import SempipeError
from smartpipe.io import diagnostics
from smartpipe.models.base import (
    AudioData,
    CompletionRequest,
    ImageData,
    supports_media_embedding,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smartpipe.models.base import ChatModel, EmbeddingModel

__all__ = ["render_matrix", "run_probe"]


@dataclass(frozen=True, slots=True)
class Cell:
    verdict: str  # "ok" | "no" | "via" (fallback) | "na"
    detail: str
    footnote: str | None = None  # what the * means, when verdict == "via"


def _asset(name: str) -> bytes:
    return (resources.files("smartpipe.assets") / name).read_bytes()


async def run_probe(env: Mapping[str, str]) -> str:
    """Build the matrix against the configured chat + embed models."""
    import os

    from smartpipe.container import build_container

    del env  # the container reads the process environment itself
    async with build_container(os.environ) as container:
        chat = await container.chat_model()
        embed = await container.embedding_model()
        diagnostics.note(
            "probing modalities with 4 tiny calls "
            f"(chat: {chat.ref.name} · embed: {embed.ref.name})"
        )
        stt = _stt_path(os.environ, container.config.stt_model, chat.ref.provider)
        chat_image = await _chat_image(chat)
        chat_audio = await _chat_audio(chat, stt)
        rows = {
            "text": (await _chat_text(chat), await _embed_text(embed)),
            "image": (chat_image, _embed_image(embed, chat_image)),
            "audio": (chat_audio, _embed_audio(chat_audio, stt)),
            "video": (_chat_video(chat), _embed_video()),
            "document": (
                Cell("ok", "parsed locally (no call)"),
                Cell("ok", "as extracted text"),
            ),
        }
        _remember_probe(os.environ, str(chat.ref), image=chat_image, audio=chat_audio)
    return render_matrix(rows)


def _remember_probe(env: Mapping[str, str], ref: str, *, image: Cell, audio: Cell) -> None:
    """Persist what the probe PAID to learn: the config picker's capability
    chips read this back ("sees, hears — probed 3d ago"). Only NATIVE ability
    counts — fallback paths make no chip claims. Best-effort, never fatal."""
    import time

    from smartpipe.config.state_cache import probe_path, record_probe

    record_probe(
        probe_path(env),
        ref,
        sees=image.verdict == "ok",
        hears=audio.verdict == "ok",
        now=time.time(),
    )


def _stt_path(
    env: Mapping[str, str], configured: str | None, chat_provider: str | None
) -> str | None:
    """The transcription path the ladder would take, if any (D39/05) — the
    shared resolver, display-only: a garbage ref renders verbatim, never dies."""
    from importlib.util import find_spec

    from smartpipe.models.resolve import resolve_stt

    resolution = resolve_stt(env, configured, chat_provider)
    if resolution.kind == "remote":
        return f"{resolution.ref} (auto)" if resolution.source == "auto" else resolution.ref
    if resolution.kind == "local":
        return "local whisper"
    return "local whisper" if find_spec("faster_whisper") is not None else None


async def _chat_text(chat: ChatModel) -> Cell:
    try:
        reply = await chat.complete(
            CompletionRequest(system=None, user="Reply with exactly: OK", max_tokens=8)
        )
        return Cell("ok", f"replied {reply.strip()[:16]!r}")
    except SempipeError as exc:
        return Cell("no", _first_line(exc))


async def _chat_image(chat: ChatModel) -> Cell:
    try:
        reply = await chat.complete(
            CompletionRequest(
                system=None,
                user="One word: what color dominates this image?",
                media=(ImageData(_asset("probe.png"), "image/png"),),
                max_tokens=8,
            )
        )
        return Cell("ok", f"saw it — {reply.strip()[:16]!r}")
    except SempipeError:
        return Cell("no", "this model can't see images")


async def _chat_audio(chat: ChatModel, stt: str | None) -> Cell:
    try:
        reply = await chat.complete(
            CompletionRequest(
                system=None,
                user="One word: is this sound a tone or speech?",
                media=(AudioData(_asset("probe.wav"), "audio/wav"),),
                max_tokens=8,
            )
        )
        return Cell("ok", f"heard it — {reply.strip()[:16]!r}")
    except SempipeError:
        if stt is not None:
            return Cell("via", "transcribed, then chat", footnote=f"audio → {stt}")
        return Cell("no", "no transcription path — reinstall smartpipe")


async def _embed_text(embed: EmbeddingModel) -> Cell:
    try:
        vectors = await embed.embed([_asset("probe.txt").decode("utf-8").strip()])
        return Cell("ok", f"{len(vectors[0])}-dim vector")
    except SempipeError as exc:
        return Cell("no", _first_line(exc))


def _embed_image(embed: EmbeddingModel, chat_image: Cell) -> Cell:
    if supports_media_embedding(embed):
        return Cell("ok", "embedded as pixels")
    if chat_image.verdict == "ok":
        return Cell("via", "caption, then embed", footnote="image → caption pivot (D33)")
    return Cell("no", "needs a vision chat model to caption")


def _embed_audio(chat_audio: Cell, stt: str | None) -> Cell:
    if chat_audio.verdict == "ok" or stt is not None:
        return Cell("via", "transcript, then embed", footnote="audio → transcript pivot")
    return Cell("no", "no transcription path")


def _chat_video(chat: ChatModel) -> Cell:
    if chat.ref.provider == "gemini":
        return Cell("ok", "watched natively")
    try:
        from smartpipe.parsing.extract import ffmpeg_exe

        ffmpeg_exe()
        return Cell("via", "frames + audio track", footnote="video → 1 fps frames + track")
    except SempipeError:
        return Cell("no", "ffmpeg unavailable — reinstall smartpipe")


def _embed_video() -> Cell:
    return Cell("via", "halves, then embed", footnote="video → visual+speech halves (D36)")


def _first_line(exc: SempipeError) -> str:
    return str(exc).splitlines()[0].removeprefix("error: ")


_MARKS = {"ok": "✓ ", "no": "✗ ", "via": "–*", "na": "– "}  # noqa: RUF001 — pinned marks


def render_matrix(rows: Mapping[str, tuple[Cell, Cell]]) -> str:
    """Aligned by VISIBLE width, cells truncated to the grid — overflow never
    smashes columns (D42; live-caught by the owner's screenshot)."""
    from smartpipe.cli.screens import bad, good, heading, tint

    label_width = max(len(name) for name in rows) + 2
    cap = 30

    def cell_text(cell: Cell) -> str:
        detail = cell.detail if len(cell.detail) <= cap else cell.detail[: cap - 1] + "…"
        return f"{_MARKS[cell.verdict]} {detail}"

    def paint(cell: Cell, text: str) -> str:
        mark_len = 2
        mark, rest = text[:mark_len], text[mark_len:]
        match cell.verdict:
            case "ok":
                return good(mark) + rest
            case "no":
                return bad(mark) + rest
            case _:
                return tint(mark, "2") + rest

    chat_width = max(len(cell_text(chat)) for chat, _embed in rows.values()) + 3
    header = f"  {' ' * label_width}{heading(_pad_plain('chat', chat_width))}{heading('embed')}"
    lines = [header]
    footnotes: list[str] = []
    for modality, (chat_cell, embed_cell) in rows.items():
        left = paint(chat_cell, cell_text(chat_cell))
        right = paint(embed_cell, cell_text(embed_cell))
        label = tint(modality.ljust(label_width), "2")
        lines.append(f"  {label}{_pad_ansi(left, chat_width)}{right}")
        for cell in (chat_cell, embed_cell):
            if cell.footnote and cell.footnote not in footnotes:
                footnotes.append(cell.footnote)
    if footnotes:
        lines.append(tint("  * fallback paths: " + " · ".join(footnotes), "2"))
    return "\n".join(lines)


def _pad_plain(text: str, width: int) -> str:
    return f"{text:{width}s}"


def _pad_ansi(text: str, width: int) -> str:
    """Pad by VISIBLE length — ANSI escapes are zero-width."""
    import re

    visible = len(re.sub(r"\x1b\[[0-9;]*m", "", text))
    return text + " " * max(0, width - visible)
