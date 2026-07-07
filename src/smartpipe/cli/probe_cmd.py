"""``doctor --probe`` (D31): the modality-by-consumer matrix, with real tiny calls.

``doctor`` alone never spends a cent (D18); this flag is the explicit opt-in
that answers what the docs can only claim: which modalities *actually* reach
your configured models. Four tiny paid calls, announced first. The assets ship
in the wheel (an 8x8 PNG, a 0.25 s beep, one sentence — a few KB).
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

from smartpipe.core.errors import SempipeError
from smartpipe.io import diagnostics
from smartpipe.models.base import AudioData, CompletionRequest, ImageData

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smartpipe.models.base import ChatModel, EmbeddingModel

__all__ = ["render_matrix", "run_probe"]


@dataclass(frozen=True, slots=True)
class Cell:
    verdict: str  # "ok" | "no" | "na"
    detail: str


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
        rows = {
            "text": (await _chat_text(chat), await _embed_text(embed)),
            "image": (await _chat_image(chat), Cell("na", "text-only endpoint")),
            "audio": (await _chat_audio(chat), Cell("na", "transcribe-then-embed path")),
            "video": (_video_local(), Cell("na", "frames land with map")),
            "document": (
                Cell("ok", "parsed locally (no call)"),
                Cell("ok", "as text"),
            ),
        }
    return render_matrix(rows)


async def _chat_text(chat: ChatModel) -> Cell:
    try:
        reply = await chat.complete(
            CompletionRequest(system=None, user="Reply with exactly: OK", max_tokens=8)
        )
        return Cell("ok", f"replied {reply.strip()[:20]!r}")
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
        return Cell("ok", f"saw it — {reply.strip()[:24]!r}")
    except SempipeError as exc:
        return Cell("no", _first_line(exc))


async def _chat_audio(chat: ChatModel) -> Cell:
    try:
        reply = await chat.complete(
            CompletionRequest(
                system=None,
                user="One word: is this sound a tone or speech?",
                media=(AudioData(_asset("probe.wav"), "audio/wav"),),
                max_tokens=8,
            )
        )
        return Cell("ok", f"heard it — {reply.strip()[:24]!r}")
    except SempipeError as exc:
        return Cell("no", _first_line(exc))


async def _embed_text(embed: EmbeddingModel) -> Cell:
    try:
        vectors = await embed.embed([_asset("probe.txt").decode("utf-8").strip()])
        return Cell("ok", f"{len(vectors[0])}-dim vector")
    except SempipeError as exc:
        return Cell("no", _first_line(exc))


def _video_local() -> Cell:
    try:
        from smartpipe.parsing.extract import ffmpeg_exe

        ffmpeg_exe()
        return Cell("ok", "ffmpeg found — frames+audio")
    except SempipeError:
        return Cell("no", "ffmpeg missing — pip install 'smartpipe[video]'")


def _first_line(exc: SempipeError) -> str:
    return str(exc).splitlines()[0].removeprefix("error: ")


_MARKS = {"ok": "✓", "no": "✗", "na": "–"}  # noqa: RUF001 — the pinned matrix marks


def render_matrix(rows: Mapping[str, tuple[Cell, Cell]]) -> str:
    lines = [f"  {'':10s}  {'chat':34s}{'embed'}"]
    for modality, (chat_cell, embed_cell) in rows.items():
        left = f"{_MARKS[chat_cell.verdict]} {chat_cell.detail}"
        right = f"{_MARKS[embed_cell.verdict]} {embed_cell.detail}"
        lines.append(f"  {modality:10s}  {left:34s}{right}")
    return "\n".join(lines)
