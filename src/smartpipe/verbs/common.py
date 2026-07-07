"""Shared verb helpers: outcome→exit-code, item-stream plumbing, embed batching."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, TypeVar, assert_never

from smartpipe.core.errors import ExitCode, ItemError, TooManyFailures, UsageFault
from smartpipe.engine.runner import (
    Done,
    FailurePolicy,
    ItemOutcome,
    Skipped,
    should_halt,
    should_halt_consecutive,
)
from smartpipe.io import diagnostics
from smartpipe.models.base import AudioData, ImageData, MediaEmbeddingModel, VideoData
from smartpipe.verbs.convert import AUDIO_NEEDS_TEXT, Converter

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
    from pathlib import Path

    from smartpipe.io.items import Item
    from smartpipe.models.base import EmbeddingModel

__all__ = [
    "AUDIO_NEEDS_TEXT",
    "EMBED_BATCH_SIZE",
    "IMAGE_NEEDS_MAP",
    "WindowGate",
    "batched",
    "embed_budget",
    "embed_in_batches",
    "ensure_text",
    "interrupted_exit_code",
    "outcome_exit_code",
    "prepend",
    "resolve_schema",
    "transcribe",
]

T = TypeVar("T")

EMBED_BATCH_SIZE = 64  # texts per embed call on finite corpora (plan/post-1.0/06)


def outcome_exit_code(*, done: int, skipped: int) -> ExitCode:
    """0 = all ok · 1 = some skipped · 3 = every item failed (spec §12)."""
    if skipped == 0:
        return ExitCode.OK
    if done == 0:
        return ExitCode.ALL_FAILED
    return ExitCode.PARTIAL


def interrupted_exit_code(*, done: int, skipped: int) -> ExitCode:
    """After a drained Ctrl-C (ux.md §12): the run's normal outcome code — an
    interrupt doesn't mask partiality — except 130 when nothing finished at all."""
    if done == 0 and skipped == 0:
        return ExitCode.INTERRUPTED
    return outcome_exit_code(done=done, skipped=skipped)


async def prepend(first: Item, rest: AsyncIterator[Item]) -> AsyncIterator[Item]:
    """Re-attach an item pulled for a first-item check (filter's brace fail-fast)."""
    yield first
    async for item in rest:
        yield item


IMAGE_NEEDS_MAP = "image items need map — this verb reads text"  # stage-7 wording, pinned


def transcribe(audio: AudioData) -> str:
    """The default transcriber (the ``[audio]`` extra) with the pinned two-fix skip."""
    from smartpipe.parsing.extract import MissingExtra, transcribe_audio

    try:
        return transcribe_audio(audio)
    except MissingExtra as exc:
        raise ItemError(AUDIO_NEEDS_TEXT) from exc


async def ensure_text(
    item: Item,
    *,
    transcriber: Callable[[AudioData], str] = transcribe,
    log: diagnostics.DegradationLog | None = None,
    converter: Converter | None = None,
) -> Item:
    """Non-map verbs read text (D20/D32/D33): media parts convert to text through
    the ladder — an LLM when the cost fence allows (free local: automatic; cloud:
    behind --allow-captions), whisper for audio beneath it — every conversion
    row-noted. Image-ONLY items with no conversion available keep the skip."""
    if not item.media:
        return item
    import asyncio

    from smartpipe.io.items import describe_source

    where = describe_source(item.source)
    text: str = item.text
    figures: list[ImageData] = []
    for part in item.media:
        match part:
            case AudioData() as audio:
                spoken: str
                if converter is not None:
                    spoken = await converter.audio_to_text(audio, where)
                else:
                    spoken = await asyncio.to_thread(transcriber, audio)
                    if log is not None:
                        log.note(where, "audio → text", _whisper_detail())
                text = _merge(text, spoken)
            case VideoData() as video:
                if converter is not None:
                    visual, speech = await converter.video_halves(video, where)
                    halves: list[str] = [half for half in (visual, speech) if half]
                    watched: str = "\n\n".join(halves)
                    text = _merge(text, watched)
                    continue
                from smartpipe.parsing.extract import video_to_parts

                parts = await asyncio.to_thread(video_to_parts, video)
                if parts.track is None:
                    raise ItemError(
                        "this video has no audio track — text verbs read text; "
                        "map can see its frames"
                    )
                # converter is provably None here — the halves branch above
                # consumed every converter path and `continue`d
                track_text: str = await asyncio.to_thread(transcriber, parts.track)
                if log is not None:
                    log.note(
                        where,
                        "video → text",
                        "audio track converted; frames dropped — map sees frames",
                    )
                text = _merge(text, track_text)
            case ImageData() as image:
                figures.append(image)
            case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                assert_never(unreachable)
    if figures:
        text = await _figures_to_text(figures, text, where, log=log, converter=converter)
    return replace(item, text=text, media=())


async def _figures_to_text(
    figures: list[ImageData],
    text: str,
    where: str,
    *,
    log: diagnostics.DegradationLog | None,
    converter: Converter | None,
) -> str:
    """Captions when the fence allows; a mixed item degrades to text-only (noted)
    when it doesn't; an image-ONLY item skips with the two-fix line (D33)."""
    if converter is not None:
        try:
            captions = [await converter.image_to_text(figure, where) for figure in figures]
        except ItemError:
            if not text:
                raise  # image-only: the converter's message names both fixes
            captions = None
        if captions is not None:
            described = "\n\n".join(
                f"[figure {position}] {caption}"
                for position, caption in enumerate(captions, start=1)
            )
            return f"{text}\n\n{described}".strip() if text else described
    if not text:
        raise ItemError(IMAGE_NEEDS_MAP)
    if log is not None:
        plural = "s" if len(figures) != 1 else ""
        log.note(
            where,
            "figures dropped",
            f"{len(figures)} image{plural} — captions convert them "
            "(free with a local vision model, --allow-captions for cloud)",
        )
    return text


def _merge(text: str, addition: str) -> str:
    """Append a converted part to an item's text (empty-safe)."""
    return f"{text}\n\n{addition}".strip() if text else addition


def _whisper_detail() -> str:
    import os

    from smartpipe.parsing.extract import whisper_size

    return f"whisper {whisper_size(os.environ)}"


EMBED_BUDGET_TOKENS = 4_800  # 8k published minus the usual safety margin
_GEMINI_EMBED_BUDGET_TOKENS = 1_200  # gemini-embedding caps input at 2k tokens


def embed_budget(provider: str) -> int:
    """Embedding windows aren't published via API — a conservative static table."""
    return _GEMINI_EMBED_BUDGET_TOKENS if provider == "gemini" else EMBED_BUDGET_TOKENS


@dataclass
class WindowGate:
    """Per-run, probe-aware oversize gate for chat calls (D26).

    Fast path: an item within the static table budget never triggers anything.
    The first item that exceeds it asks the provider for the real window once
    (four wires publish it); the answer can only widen the budget.
    """

    provider: str
    model_name: str
    overhead: int
    window: Callable[[], Awaitable[int | None]]
    _budget: int | None = None
    _probed: bool = False

    async def budget_for_oversized(self, text: str) -> int | None:
        """None when the text fits (no probe, no cost); else the best-known budget."""
        from smartpipe.engine.chunking import budget_for, estimate_tokens

        if self._budget is None:
            self._budget = budget_for(self.provider, prompt_overhead=self.overhead)
        if estimate_tokens(text) <= self._budget:
            return None
        if not self._probed:
            self._probed = True
            probed = await self.window()
            if probed is not None:
                widened = budget_for(self.provider, prompt_overhead=self.overhead, window=probed)
                self._budget = max(self._budget, widened)
        return None if estimate_tokens(text) <= self._budget else self._budget

    def refusal(self, text: str, budget: int) -> str:
        from smartpipe.engine.chunking import estimate_tokens

        return (
            f"~{estimate_tokens(text):,} tokens is past {self.model_name}'s "
            f"~{budget:,}-token budget — split it first: "
            'smartpipe split --in FILE | smartpipe map "..." | smartpipe reduce "..."'
        )


def resolve_schema(
    path: Path | None,
    dsl: str | None,
    *,
    loader: Callable[[Path], dict[str, object]],
) -> dict[str, object] | None:
    """Rungs 3/5 of the schema ladder (D22): mutually exclusive, resolved before
    any model call. The loader is the verb's own ``load_schema`` so its test seam
    keeps working."""
    if path is not None and dsl is not None:
        raise UsageFault(
            "--schema-from and --schema both shape the output — use one\n"
            "  --schema-from builds the schema from a short description; --schema loads a file."
        )
    if dsl is not None:
        from smartpipe.engine.schema_dsl import dsl_to_schema

        return dsl_to_schema(dsl)
    return loader(path) if path is not None else None


def batched(items: Sequence[T], size: int) -> Iterator[tuple[T, ...]]:
    """``itertools.batched`` for the 3.11 floor — tuple chunks, order preserved."""
    if size < 1:
        raise ValueError(f"batch size must be >= 1, got {size}")
    return (tuple(items[start : start + size]) for start in range(0, len(items), size))


_native_noted = False  # one disclosure per process, not per item  # noqa: N816-ish (module state)


def _native_route(item: Item, model: object) -> tuple[MediaEmbeddingModel, ImageData] | None:
    """The native-path test (D39/04): a media-capable embedder + an
    image-ONLY item (no meaningful text). Text-bearing items keep embedding
    their text; audio/video keep the pivot ladder. Returns the narrowed
    model alongside the image so the caller's ``model`` binding stays put."""
    from smartpipe.models.base import supports_media_embedding

    if not supports_media_embedding(model):
        return None
    if item.text.strip():
        return None
    images = [part for part in item.media if isinstance(part, ImageData)]
    if len(images) != 1 or len(item.media) != 1:
        return None
    return model, images[0]


async def embed_in_batches(
    model: EmbeddingModel,
    items: Sequence[Item],
    *,
    failure_policy: FailurePolicy,
    batch_size: int = EMBED_BATCH_SIZE,
    stop: asyncio.Event | None = None,
    transcriber: Callable[[AudioData], str] = transcribe,
    log: diagnostics.DegradationLog | None = None,
    converter: Converter | None = None,
) -> AsyncIterator[ItemOutcome[tuple[Item, tuple[float, ...]]]]:
    """Embed a finite corpus in ≤``batch_size`` chunks, sequentially (DEFER-3).

    Bypasses ``run_ordered`` on purpose — batching ≠ per-item workers: order
    comes from sequential chunks, and isolation from the fallback (a failed
    chunk re-runs item-by-item, so one poison item skips alone instead of
    taking 63 neighbors with it). Accounting mirrors the runner's: majority
    failure past ``min_sample`` halts with ``TooManyFailures``.
    """
    processed = 0
    skipped = 0
    consecutive = 0
    succeeded = False

    def account_skip(reason: str) -> None:
        nonlocal skipped, consecutive
        skipped += 1
        consecutive += 1
        if should_halt(failure_policy, total=processed, skipped=skipped):
            raise TooManyFailures(skipped, processed, reason)
        if should_halt_consecutive(failure_policy, succeeded=succeeded, consecutive=consecutive):
            raise TooManyFailures(skipped, processed, reason)

    def account_done() -> None:
        nonlocal consecutive, succeeded
        consecutive = 0
        succeeded = True

    from smartpipe.engine.chunking import estimate_tokens, mean_pool, split_text

    budget = embed_budget(model.ref.provider)

    async def embed_batch(
        batch: list[Item],
    ) -> AsyncIterator[ItemOutcome[tuple[Item, tuple[float, ...]]]]:
        if not batch:
            return
        try:
            vectors = await model.embed([entry.text for entry in batch])
            if len(vectors) != len(batch):
                raise ItemError(f"endpoint returned {len(vectors)} vectors for {len(batch)} texts")
        except ItemError:
            # the poison fallback (DEFER-3): re-run item-by-item so one bad item
            # skips alone — accounting runs BETWEEN calls so the D18 halt can
            # stop the spend mid-batch, not after it
            for entry in batch:
                if stop is not None and stop.is_set():
                    return
                try:
                    vector = (await model.embed([entry.text]))[0]
                except ItemError as exc:
                    skip = Skipped(entry.source.index, str(exc), entry.source)
                    account(skip)
                    yield skip
                else:
                    done = Done(entry.source.index, (entry, vector))
                    account(done)
                    yield done
            return
        for entry, vector in zip(batch, vectors, strict=True):
            done = Done(entry.source.index, (entry, vector))
            account(done)
            yield done

    async def pooled(entry: Item) -> ItemOutcome[tuple[Item, tuple[float, ...]]]:
        # D26: one text past the embedding window — embed its chunks, mean-pool
        try:
            vectors = await model.embed(list(split_text(entry.text, budget)))
        except ItemError as exc:
            return Skipped(entry.source.index, str(exc), entry.source)
        return Done(entry.source.index, (entry, mean_pool(vectors)))

    def account(outcome: ItemOutcome[tuple[Item, tuple[float, ...]]]) -> None:
        nonlocal processed
        processed += 1
        if isinstance(outcome, Done):
            account_done()
        else:
            account_skip(outcome.reason)

    async def _drain(
        outcomes: AsyncIterator[ItemOutcome[tuple[Item, tuple[float, ...]]]],
    ) -> list[ItemOutcome[tuple[Item, tuple[float, ...]]]]:
        return [outcome async for outcome in outcomes]

    pending: list[Item] = []
    for item in items:
        if stop is not None and stop.is_set():
            return
        if item.media:
            native = _native_route(item, model)
            if native is not None:
                media_model, image = native
                # D39/04: the embedder takes images natively — no captions
                for outcome in await _drain(embed_batch(pending)):
                    yield outcome
                pending = []
                global _native_noted
                if not _native_noted:
                    _native_noted = True
                    diagnostics.note(
                        f"media embedded natively ({model.ref.provider}/{model.ref.name})"
                        " — no captions"
                    )
                try:
                    vectors = await media_model.embed_parts([image])
                except ItemError as exc:
                    skip = Skipped(item.source.index, str(exc), item.source)
                    account(skip)
                    yield skip
                    continue
                done = Done(item.source.index, (item, vectors[0]))
                account(done)
                yield done
                continue
            video = next((part for part in item.media if isinstance(part, VideoData)), None)
            if video is not None and converter is not None and converter.chat is not None:
                from smartpipe.verbs.convert import embed_video_halves

                # flush first so stdout order stays input order, then the halves
                for outcome in await _drain(embed_batch(pending)):
                    yield outcome
                pending = []
                try:
                    converted, vector = await embed_video_halves(model, item, video, converter)
                except ItemError as exc:
                    skip = Skipped(item.source.index, str(exc), item.source)
                    account(skip)
                    yield skip
                    continue
                done = Done(item.source.index, (converted, vector))
                account(done)
                yield done
                continue
            try:
                item = await ensure_text(
                    item, transcriber=transcriber, log=log, converter=converter
                )
            except ItemError as exc:
                skip = Skipped(item.source.index, str(exc), item.source)
                account(skip)
                yield skip
                continue
        if estimate_tokens(item.text) > budget:
            # flush first so stdout order stays input order, then pool this one
            async for outcome in embed_batch(pending):
                yield outcome
            pending = []
            pooled_outcome = await pooled(item)
            account(pooled_outcome)
            yield pooled_outcome
            continue
        pending.append(item)
        if len(pending) >= batch_size:
            async for outcome in embed_batch(pending):
                yield outcome
            pending = []
    async for outcome in embed_batch(pending):
        yield outcome
