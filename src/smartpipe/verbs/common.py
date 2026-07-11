"""Shared verb helpers: outcome→exit-code, item-stream plumbing, embed batching."""

from __future__ import annotations

from dataclasses import dataclass, replace
from dataclasses import field as dataclasses_field
from typing import TYPE_CHECKING, Protocol, TypeVar, assert_never

from smartpipe.core.errors import (
    CircuitOpenTransport,
    ExcludedError,
    ExitCode,
    ItemError,
    LateSetupFault,
    RetryableError,
    SourceCounts,
    TooManyFailures,
    UnsentError,
    UsageFault,
)
from smartpipe.engine.runner import (
    Done,
    FailurePolicy,
    ItemOutcome,
    Skipped,
    should_halt,
    should_halt_consecutive,
)
from smartpipe.io import diagnostics, source_accounting
from smartpipe.models.base import AudioData, ImageData, MediaEmbeddingModel, VideoData
from smartpipe.verbs.convert import AUDIO_NEEDS_TEXT, Converter

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
    from pathlib import Path

    from smartpipe.io.items import Item
    from smartpipe.models.base import ChatModel, EmbeddingModel, MediaData

__all__ = [
    "AUDIO_NEEDS_TEXT",
    "EMBED_BATCH_SIZE",
    "IMAGE_NEEDS_MAP",
    "ExecutionPolicySource",
    "GeometryFence",
    "ModelSlot",
    "Oversize",
    "WindowGate",
    "batched",
    "embed_budget",
    "embed_in_batches",
    "ensure_text",
    "interrupted_exit_code",
    "make_failover",
    "media_embedder",
    "native_route",
    "note_ambiguous_temporal",
    "note_native_once",
    "outcome_exit_code",
    "prepend",
    "reset_run_disclosures",
    "resolve_schema",
    "row_embedder",
    "transcribe",
    "warn_unenforced_schema",
]

T = TypeVar("T")

EMBED_BATCH_SIZE = 64  # texts per embed call on finite corpora (plan/post-1.0/06)


class ExecutionPolicySource(Protocol):
    """The composition-root execution policy shared by model-using verbs."""

    def concurrency(self, flag: int | None = None) -> int: ...

    def failure_policy(self, provider: str) -> FailurePolicy: ...


@dataclass(slots=True)
class ModelSlot:
    """The run's current chat model, swappable WHOLESALE by the failover (item
    11) — never per-item interleaving. The tally counts answered items per
    model so the end receipt keeps the seam visible."""

    current: ChatModel
    counts: dict[str, int] = dataclasses_field(default_factory=dict[str, int])
    switched: bool = False

    def tally(self, label: str) -> None:
        self.counts[label] = self.counts.get(label, 0) + 1

    def receipt(self) -> str:
        split = " · ".join(
            f"{label} ×{count}"  # noqa: RUF001 — the pinned count mark (D27 rollup style)
            for label, count in self.counts.items()
        )
        return f"answers: {split}"


def make_failover(
    slot: ModelSlot,
    resolve: Callable[[], Awaitable[ChatModel]],
    *,
    limit: int,
) -> Callable[[], Awaitable[bool]]:
    """The verb-side failover hook: build the configured fallback at switch
    time (keys/login checked here), swap the slot wholesale, announce loudly.
    An unusable fallback returns False — the runner then dies on the ordinary
    provider-down screen, with the reason already noted."""

    async def switch() -> bool:
        from smartpipe.core.errors import SempipeError

        provider = slot.current.ref.provider
        try:
            fallback = await resolve()
        except SempipeError as fault:
            first = str(fault).splitlines()[0].removeprefix("error: ")
            diagnostics.note(f"fallback model unusable — {first}")
            return False
        slot.current = fallback
        slot.switched = True
        diagnostics.warn(
            f"{provider} looks down ({limit} consecutive transport failures) — "
            f"switching to {fallback.ref} for the rest of the run"
        )
        return True

    return switch


def outcome_exit_code(
    *,
    done: int,
    skipped: int,
    failed: int = 0,
    input_count: int | None = None,
    partial: bool = False,
    source_counts: SourceCounts | None = None,
) -> ExitCode:
    """0 = all ok · 1 = some skipped · 3 = every item failed (spec §12).

    ``done``/``skipped`` remain the verb's stage outcome for its exit status.
    A grouped source (for example one PDF yielding three OCR pages) supplies
    ``source_counts`` so the manifest records the source once without making
    page-level partial output look like an all-source failure.
    """
    from smartpipe.io import manifest

    recorded = (
        SourceCounts(succeeded=done, skipped=skipped, failed=failed)
        if source_counts is None
        else source_counts
    )
    manifest.record_counts(
        done=recorded.succeeded,
        skipped=recorded.skipped,
        failed=recorded.failed,
        input_count=recorded.total if source_counts is not None else input_count,
    )
    if done == 0:
        return ExitCode.ALL_FAILED if skipped else (ExitCode.PARTIAL if partial else ExitCode.OK)
    return ExitCode.PARTIAL if skipped or partial else ExitCode.OK


def interrupted_exit_code(
    *,
    done: int,
    skipped: int,
    failed: int = 0,
    input_count: int | None = None,
    partial: bool = False,
    source_counts: SourceCounts | None = None,
) -> ExitCode:
    """After a drained Ctrl-C (ux.md §12): the run's normal outcome code — an
    interrupt doesn't mask partiality — except 130 when nothing finished at all."""
    if done == 0 and skipped == 0:
        from smartpipe.io import manifest

        recorded = SourceCounts(0, 0, 0) if source_counts is None else source_counts
        manifest.record_counts(
            done=recorded.succeeded,
            skipped=recorded.skipped,
            failed=recorded.failed,
            input_count=recorded.total if source_counts is not None else input_count,
        )
        return ExitCode.INTERRUPTED
    return outcome_exit_code(
        done=done,
        skipped=skipped,
        failed=failed,
        input_count=input_count,
        partial=partial,
        source_counts=source_counts,
    )


async def prepend(first: Item, rest: AsyncIterator[Item]) -> AsyncIterator[Item]:
    """Re-attach an item pulled for a first-item check (filter's brace fail-fast)."""
    yield first
    async for item in rest:
        yield item


IMAGE_NEEDS_MAP = "image items need map — this verb reads text"  # stage-7 wording, pinned

_AMBIGUITY_CAP = 5  # ambiguous-date notes per invocation: first rows verbatim, then quiet
_ambiguous_dates_seen = 0


def note_ambiguous_temporal(message: str) -> None:
    """The coercion's ambiguous-date disclosure (item 56): month-first guesses
    surface on stderr, capped so a systematically ambiguous corpus can't flood."""
    global _ambiguous_dates_seen
    _ambiguous_dates_seen += 1
    if _ambiguous_dates_seen <= _AMBIGUITY_CAP:
        diagnostics.note(message)
    elif _ambiguous_dates_seen == _AMBIGUITY_CAP + 1:
        diagnostics.note("more ambiguous dates follow (suppressed)")


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
    from smartpipe.parsing.extract import configured_whisper_size

    return f"whisper {configured_whisper_size()}"


EMBED_BUDGET_TOKENS = 4_800  # 8k published minus the usual safety margin
_GEMINI_EMBED_BUDGET_TOKENS = 1_200  # gemini-embedding caps input at 2k tokens


def embed_budget(provider: str) -> int:
    """Embedding windows aren't published via API — a conservative static table."""
    return _GEMINI_EMBED_BUDGET_TOKENS if provider == "gemini" else EMBED_BUDGET_TOKENS


@dataclass(frozen=True, slots=True)
class Oversize:
    """One item past the window: its combined text+media estimate, the
    best-known per-call budget the auto-chunk strategies must fit, and the
    media share of the estimate (media can't be text-chunked — it rides the
    first chunk, and its cost shrinks the text budget)."""

    estimate: int
    budget: int
    media_tokens: int = 0
    model_name: str | None = None


@dataclass(slots=True)
class _WindowState:
    budget: int
    probe: asyncio.Future[int | None] | None = None


@dataclass
class WindowGate:
    """Per-run, probe-aware oversize gate for chat calls (D26).

    Fast path: an item within the static table budget never triggers anything.
    The first item that exceeds it asks the provider for the real window once
    (four wires publish it); the answer can only widen the budget. The estimate
    counts text AND media (D26 v2) — images/audio/video spend context too.
    """

    provider: str
    model_name: str
    overhead: int
    window: Callable[[], Awaitable[int | None]]
    _states: dict[tuple[str, str], _WindowState] = dataclasses_field(
        default_factory=dict[tuple[str, str], _WindowState],
        init=False,
    )

    async def budget_for_oversized(
        self,
        text: str,
        media: Sequence[MediaData] = (),
        *,
        provider: str | None = None,
        model_name: str | None = None,
        window: Callable[[], Awaitable[int | None]] | None = None,
    ) -> Oversize | None:
        """None when the item fits (no probe, no cost); else the estimate and
        the best-known budget."""
        import asyncio

        from smartpipe.engine.chunking import budget_for, estimate_tokens, media_tokens

        effective_provider = self.provider if provider is None else provider
        effective_model = self.model_name if model_name is None else model_name
        effective_window = self.window if window is None else window
        key = (effective_provider, effective_model)
        state = self._states.get(key)
        if state is None:
            state = _WindowState(
                budget=budget_for(effective_provider, prompt_overhead=self.overhead)
            )
            self._states[key] = state
        media_estimate = 0
        if media:
            from smartpipe.io import metering

            media_estimate = media_tokens(
                media,
                effective_provider,
                seconds_of=metering.clip_seconds,
            )
        estimate = estimate_tokens(text) + media_estimate
        if estimate <= state.budget:
            return None
        if state.probe is None:
            state.probe = asyncio.ensure_future(effective_window())
        probed = await asyncio.shield(state.probe)
        if probed is not None:
            widened = budget_for(
                effective_provider,
                prompt_overhead=self.overhead,
                window=probed,
            )
            state.budget = max(state.budget, widened)
        if estimate <= state.budget:
            return None
        return Oversize(estimate, state.budget, media_estimate, effective_model)

    def refusal(self, over: Oversize) -> str:
        from smartpipe.verbs.oversize import refusal

        return refusal(over.estimate, over.model_name or self.model_name, over.budget)


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
    from smartpipe.io import manifest

    if dsl is not None:
        from smartpipe.engine.schema_dsl import dsl_to_schema

        compiled = dsl_to_schema(dsl)
        manifest.record_schema(compiled)  # the --manifest funnel (item 65a)
        return compiled
    loaded = loader(path) if path is not None else None
    manifest.record_schema(loaded)
    return loaded


def batched(items: Sequence[T], size: int) -> Iterator[tuple[T, ...]]:
    """``itertools.batched`` for the 3.11 floor — tuple chunks, order preserved."""
    if size < 1:
        raise ValueError(f"batch size must be >= 1, got {size}")
    return (tuple(items[start : start + size]) for start in range(0, len(items), size))


_native_noted = False  # one disclosure per invocation, not per item
_unenforced_schema_warned = False  # A3: one schema-enforcement warning per invocation
_LOOSE_SCHEMA_PROVIDERS = frozenset({"ollama"})  # attach the schema as advisory, not enforced


def reset_run_disclosures() -> None:
    """Reset stderr disclosure caps at one invocation boundary."""
    global _ambiguous_dates_seen, _native_noted, _unenforced_schema_warned
    _ambiguous_dates_seen = 0
    _native_noted = False
    _unenforced_schema_warned = False


def note_native_once(model: object) -> None:
    """One disclosure per run when pixels replace captions - shared by the
    finite and streaming embed paths."""
    global _native_noted
    if not _native_noted:
        _native_noted = True
        ref = getattr(model, "ref", None)
        diagnostics.note(
            f"media embedded natively ({getattr(ref, 'provider', '?')}/{getattr(ref, 'name', '?')})"
            " — no captions"
        )


def warn_unenforced_schema(model: object) -> None:
    """A3: one loud line per run when a schema-attached request comes back
    violating its schema even after the one repair rung. For a wire whose schema
    is advisory (ollama's ``format``) this is the expected miss of many cloud
    models; for a strict-enforcing wire the same event is surprising enough to
    read as a provider-side regression. Graph inherits this automatically — its
    chunks flow through ``map_one``."""
    global _unenforced_schema_warned
    if _unenforced_schema_warned:
        return
    _unenforced_schema_warned = True
    ref = getattr(model, "ref", None)
    provider = getattr(ref, "provider", "?")
    name = f"{provider}/{getattr(ref, 'name', '?')}"
    if provider in _LOOSE_SCHEMA_PROVIDERS:
        diagnostics.warn(
            f"{name} was asked to enforce the reply schema but its reply violates it "
            "— this model likely ignores constrained decoding (cloud models often do); "
            "a stricter --model, or graph's schema canary, catches this early."
        )
    else:
        diagnostics.warn(
            f"{name} returned a reply that violates the schema its wire enforces "
            "— possibly a provider-side API regression."
        )


def media_embedder(model: EmbeddingModel, media_model: EmbeddingModel | None) -> EmbeddingModel:
    """The model that media items route to: the ``media-embed-model`` role
    when configured, else the run's embedding model (D39/04 unchanged)."""
    return media_model if media_model is not None else model


def row_embedder(item: Item, model: EmbeddingModel, media_model: EmbeddingModel | None) -> str:
    """The resolved ref that embedded THIS row — the ``__embedder`` stamp.
    Media-native rows carry the media role's ref; everything else (text, and
    every caption/transcript pivot) carries the text embedder's."""
    effective = media_embedder(model, media_model)
    if native_route(item, effective) is not None:
        return str(effective.ref)
    return str(model.ref)


@dataclass(slots=True)
class GeometryFence:
    """One run, one vector space (deliverable 2's law): when text items embed
    with one model and media items with another, the vectors are mutually
    meaningless — refuse loudly the moment both kinds have been seen. Equal
    refs (the joint-model setup) can never trip it."""

    text_ref: str
    media_ref: str
    saw_text: bool = False
    saw_media: bool = False

    def admit(self, *, media: bool) -> None:
        if media:
            self.saw_media = True
        else:
            self.saw_text = True
        if self.saw_text and self.saw_media and self.text_ref != self.media_ref:
            raise UsageFault(
                "one run, one vector space — text items embed with "
                f"{self.text_ref}, media items with {self.media_ref}\n"
                "  Vectors from two models can't be compared, so smartpipe refuses to mix them.\n"
                f"  Fix: use the joint model for everything - set embed-model to "
                f"{self.media_ref}\n"
                "  (smartpipe use walks you through it), or feed media-only input when\n"
                "  media-embed-model stands apart."
            )


def native_route(item: Item, model: object) -> tuple[MediaEmbeddingModel, ImageData] | None:
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
    call_concurrency: int = 1,
    stop: asyncio.Event | None = None,
    transcriber: Callable[[AudioData], str] = transcribe,
    log: diagnostics.DegradationLog | None = None,
    converter: Converter | None = None,
    media_model: EmbeddingModel | None = None,
) -> AsyncIterator[ItemOutcome[tuple[Item, tuple[float, ...]]]]:
    """Embed a finite corpus in ≤``batch_size`` chunks (DEFER-3).

    Up to ``call_concurrency`` batch API calls run at once while outcomes stay
    input-ordered. A content-specific failed chunk re-runs item-by-item so one
    poison item skips alone instead of taking its neighbors with it. Fatal
    failures cancel sibling calls; accounting is applied only in input order.
    """
    if call_concurrency < 1:
        raise ValueError(f"call concurrency must be >= 1, got {call_concurrency}")
    if stop is not None and stop.is_set():
        return
    import asyncio

    processed = 0
    skipped = 0
    consecutive = 0
    succeeded = False
    sources = source_accounting.SourceCounter()
    active_calls: list[tuple[list[Item], asyncio.Task[tuple[tuple[float, ...], ...]]]] = []
    stopped_before_send = "run stopping — not sent"

    def is_stopping() -> bool:
        return stop is not None and stop.is_set()

    def unsent(entry: Item, *, reason: str = stopped_before_send) -> Skipped:
        return Skipped(entry.source.index, reason, entry.source, failed=False)

    def unavailable(entry: Item, fault: RetryableError) -> Skipped:
        return Skipped(
            entry.source.index,
            str(fault),
            entry.source,
            transport=True,
            transport_series=fault.series_id,
            transport_call=fault.call_id,
            circuit_trip=(fault.trip_id if isinstance(fault, CircuitOpenTransport) else None),
        )

    def provider_down(failed_entries: Sequence[Item]) -> LateSetupFault:
        """Settle an accepted finite corpus before surfacing provider exit 2."""
        failed_sources = [entry.source for entry in failed_entries]
        for batch, call in active_calls:
            if not call.done() or call.cancelled():
                continue
            fault = call.exception()
            if isinstance(fault, RetryableError):
                failed_sources.extend(entry.source for entry in batch)
        for remainder in items[processed:]:
            sources.skip(
                remainder.source,
                failed=any(remainder.source == source for source in failed_sources),
            )
        return LateSetupFault(
            failure_policy.transport_screen,
            source_counts=sources.counts,
        )

    def account_skip(reason: str) -> None:
        nonlocal skipped, consecutive
        skipped += 1
        consecutive += 1
        if should_halt(failure_policy, total=processed, skipped=skipped):
            for remainder in items[processed:]:
                sources.skip(remainder.source, failed=False)
            raise TooManyFailures(
                skipped,
                processed,
                reason,
                source_counts=sources.counts,
            )
        if should_halt_consecutive(failure_policy, succeeded=succeeded, consecutive=consecutive):
            for remainder in items[processed:]:
                sources.skip(remainder.source, failed=False)
            raise TooManyFailures(
                skipped,
                processed,
                reason,
                source_counts=sources.counts,
            )

    def account_done() -> None:
        nonlocal consecutive, succeeded
        consecutive = 0
        succeeded = True

    from smartpipe.engine.chunking import estimate_tokens, mean_pool, split_text

    budget = embed_budget(model.ref.provider)

    async def finish_batch(
        batch: list[Item],
        call: asyncio.Task[tuple[tuple[float, ...], ...]],
    ) -> AsyncIterator[ItemOutcome[tuple[Item, tuple[float, ...]]]]:
        try:
            vectors = call.result()
            if len(vectors) != len(batch):
                raise ItemError(f"endpoint returned {len(vectors)} vectors for {len(batch)} texts")
        except CircuitOpenTransport as fault:
            raise provider_down(batch) from fault
        except RetryableError as batch_error:
            # One failed actual call had every batch member waiting behind it.
            # Fan that one typed availability outcome to the waiters; retrying
            # each text solo would multiply an exhausted retry ladder by K.
            for entry in batch:
                skip = unavailable(entry, batch_error)
                account(skip)
                yield skip
            return
        except ItemError as batch_error:
            if isinstance(batch_error, UnsentError):
                for entry in batch:
                    skip = unsent(entry, reason=str(batch_error))
                    account(skip)
                    yield skip
                return
            # the poison fallback (DEFER-3): re-run item-by-item so one bad item
            # skips alone — accounting runs BETWEEN calls so the D18 halt can
            # stop the spend mid-batch, not after it
            for position, entry in enumerate(batch):
                if is_stopping():
                    for remainder in batch[position:]:
                        skip = unsent(remainder)
                        account(skip)
                        yield skip
                    return
                try:
                    vector = (await model.embed([entry.text]))[0]
                except CircuitOpenTransport as fault:
                    raise provider_down((entry,)) from fault
                except RetryableError as fault:
                    skip = unavailable(entry, fault)
                    account(skip)
                    yield skip
                    for remainder in batch[position + 1 :]:
                        rest = unsent(
                            remainder,
                            reason="provider unavailable — not sent after isolation stopped",
                        )
                        account(rest)
                        yield rest
                    return
                except ItemError as exc:
                    skip = Skipped(
                        entry.source.index,
                        str(exc),
                        entry.source,
                        failed=not isinstance(exc, (ExcludedError, UnsentError)),
                    )
                    account(skip)
                    yield skip
                    if isinstance(exc, UnsentError) or is_stopping():
                        for remainder in batch[position + 1 :]:
                            rest = unsent(remainder)
                            account(rest)
                            yield rest
                        return
                else:
                    done = Done(entry.source.index, (entry, vector))
                    account(done)
                    yield done
                    if is_stopping():
                        for remainder in batch[position + 1 :]:
                            skip = unsent(remainder)
                            account(skip)
                            yield skip
                        return
            return
        for entry, vector in zip(batch, vectors, strict=True):
            done = Done(entry.source.index, (entry, vector))
            account(done)
            yield done

    async def embed_text_run(
        entries: Sequence[Item],
    ) -> AsyncIterator[ItemOutcome[tuple[Item, tuple[float, ...]]]]:
        """Run batch calls concurrently and consume their outcomes in order."""
        from collections import deque

        batches = iter(list(batch) for batch in batched(entries, batch_size))
        calls: deque[tuple[list[Item], asyncio.Task[tuple[tuple[float, ...], ...]]]] = deque()

        def start_one() -> bool:
            if is_stopping():
                return False
            try:
                batch = next(batches)
            except StopIteration:
                return False
            call = asyncio.create_task(model.embed([entry.text for entry in batch]))
            calls.append((batch, call))
            return True

        for _slot in range(call_concurrency):
            if not start_one():
                break
        try:
            while calls:
                batch, call = calls.popleft()
                start_after_finish = False
                try:
                    await call
                except CircuitOpenTransport:
                    pass  # finish_batch maps it to the provider-down screen
                except RetryableError:
                    if not is_stopping():
                        start_one()
                except ItemError:
                    # Poison isolation itself makes solo API calls, so it owns
                    # this freed slot until the batch is fully isolated.
                    start_after_finish = True
                else:
                    vectors = call.result()
                    if len(vectors) == len(batch) and not is_stopping():
                        start_one()
                    else:
                        start_after_finish = True
                active_calls[:] = list(calls)
                try:
                    async for outcome in finish_batch(batch, call):
                        yield outcome
                finally:
                    active_calls.clear()
                if start_after_finish and not is_stopping():
                    start_one()
            for batch in batches:
                for entry in batch:
                    skip = unsent(entry)
                    account(skip)
                    yield skip
        finally:
            for _batch, call in calls:
                call.cancel()
            await asyncio.gather(
                *(call for _batch, call in calls),
                return_exceptions=True,
            )

    async def pooled(entry: Item) -> ItemOutcome[tuple[Item, tuple[float, ...]]]:
        # D26: one text past the embedding window — embed its chunks, mean-pool
        try:
            vectors = await model.embed(list(split_text(entry.text, budget)))
        except CircuitOpenTransport as fault:
            raise provider_down((entry,)) from fault
        except RetryableError as fault:
            return unavailable(entry, fault)
        except ItemError as exc:
            return Skipped(
                entry.source.index,
                str(exc),
                entry.source,
                failed=not isinstance(exc, (ExcludedError, UnsentError)),
            )
        return Done(entry.source.index, (entry, mean_pool(vectors)))

    def account(outcome: ItemOutcome[tuple[Item, tuple[float, ...]]]) -> None:
        nonlocal processed
        processed += 1
        if isinstance(outcome, Done):
            sources.done(outcome.value[0].source)
            account_done()
        else:
            sources.skip(outcome.source, failed=outcome.failed)
            if outcome.failed and not outcome.transport:
                account_skip(outcome.reason)

    pending: list[Item] = []
    for item in items:
        if is_stopping():
            skip = unsent(item)
            account(skip)
            yield skip
            continue
        if item.media:
            effective = media_embedder(model, media_model)
            native = native_route(item, effective)
            if native is not None:
                narrowed, image = native
                # D39/04: the embedder takes images natively — no captions
                async for outcome in embed_text_run(pending):
                    yield outcome
                pending = []
                if is_stopping():
                    skip = unsent(item)
                    account(skip)
                    yield skip
                    continue
                note_native_once(effective)
                try:
                    vectors = await narrowed.embed_parts([image])
                except CircuitOpenTransport as fault:
                    raise provider_down((item,)) from fault
                except RetryableError as fault:
                    skip = unavailable(item, fault)
                    account(skip)
                    yield skip
                    continue
                except ItemError as exc:
                    skip = Skipped(
                        item.source.index,
                        str(exc),
                        item.source,
                        failed=not isinstance(exc, (ExcludedError, UnsentError)),
                    )
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
                async for outcome in embed_text_run(pending):
                    yield outcome
                pending = []
                if is_stopping():
                    skip = unsent(item)
                    account(skip)
                    yield skip
                    continue
                try:
                    converted, vector = await embed_video_halves(model, item, video, converter)
                except CircuitOpenTransport as fault:
                    raise provider_down((item,)) from fault
                except RetryableError as fault:
                    skip = unavailable(item, fault)
                    account(skip)
                    yield skip
                    continue
                except ItemError as exc:
                    skip = Skipped(
                        item.source.index,
                        str(exc),
                        item.source,
                        failed=not isinstance(exc, (ExcludedError, UnsentError)),
                    )
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
            except CircuitOpenTransport as fault:
                async for outcome in embed_text_run(pending):
                    yield outcome
                pending = []
                raise provider_down((item,)) from fault
            except RetryableError as fault:
                async for outcome in embed_text_run(pending):
                    yield outcome
                pending = []
                skip = unavailable(item, fault)
                account(skip)
                yield skip
                continue
            except ItemError as exc:
                async for outcome in embed_text_run(pending):
                    yield outcome
                pending = []
                skip = Skipped(
                    item.source.index,
                    str(exc),
                    item.source,
                    failed=not isinstance(exc, (ExcludedError, UnsentError)),
                )
                account(skip)
                yield skip
                continue
            if is_stopping():
                async for outcome in embed_text_run(pending):
                    yield outcome
                pending = []
                skip = unsent(item)
                account(skip)
                yield skip
                continue
        if estimate_tokens(item.text) > budget:
            # flush first so stdout order stays input order, then pool this one
            async for outcome in embed_text_run(pending):
                yield outcome
            pending = []
            if is_stopping():
                skip = unsent(item)
                account(skip)
                yield skip
                continue
            pooled_outcome = await pooled(item)
            account(pooled_outcome)
            yield pooled_outcome
            continue
        pending.append(item)
    async for outcome in embed_text_run(pending):
        yield outcome
