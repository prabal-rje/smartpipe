"""The composition root (design template: intent-finder's ``src/containers.py``).

One place wires every dependency for an invocation: the env snapshot, the loaded
config, a shared ``httpx.AsyncClient``, the retry policy, and the color mode. Verbs
receive a built ``AppContainer`` and ask it for ``Protocol``-typed collaborators
(``ChatModel``, ``EmbeddingModel``, ``ResultWriter``) — they never construct
adapters or read the environment themselves.

Unlike the template, the wiring is hand-rolled rather than using ``dependency-
injector``: the core dependency budget is frozen (plan/decisions.md D10), and a
CLI's composition root is small enough that a frozen dataclass of factory methods
is clearer than a container framework.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

from smartpipe.cli import screens
from smartpipe.config.paths import config_path
from smartpipe.config.store import Config, load_config
from smartpipe.core.errors import SetupFault, UsageFault
from smartpipe.io import diagnostics, manifest, tty
from smartpipe.io.tty import ColorMode
from smartpipe.io.writers import (
    OutputFormat,
    RenderMode,
    WriterConfig,
    make_writer,
    resolve_format,
)
from smartpipe.models.anthropic_adapter import build_anthropic_chat_model
from smartpipe.models.base import ModelRef, parse_model_ref
from smartpipe.models.budget import CallBudget, budgeted_chat, budgeted_embed
from smartpipe.models.http_support import make_client
from smartpipe.models.ollama import (
    OllamaChatModel,
    OllamaEmbeddingModel,
    ollama_model_names,
    resolve_host,
)
from smartpipe.models.openai_compat import (
    GEMINI_WIRE,
    MISTRAL_WIRE,
    OPENROUTER_WIRE,
    OpenAIChatModel,
    OpenAIEmbeddingModel,
    WireConfig,
    require_api_key,
    resolve_base_url,
)
from smartpipe.models.resolve import resolve_chat_ref, resolve_embed_ref, resolve_stt
from smartpipe.models.retry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence

    import httpx

    from smartpipe.engine.coalesce import BatchSettings
    from smartpipe.engine.graphkg import EntityFinder
    from smartpipe.engine.runner import FailurePolicy
    from smartpipe.io.writers import ResultWriter, TextSink
    from smartpipe.models.admission import OutboundCallPolicy
    from smartpipe.models.base import ChatModel, EmbeddingModel
    from smartpipe.models.ocr import DocumentParser
    from smartpipe.models.resilience import ResilientChatModel, WiredChat
    from smartpipe.models.stt import Transcriber

__all__ = ["AppContainer", "build_container"]

_DEFAULT_CONCURRENCY = 4


def _default_call_policy() -> OutboundCallPolicy:
    from smartpipe.models.admission import OutboundCallPolicy

    return OutboundCallPolicy(concurrency=_DEFAULT_CONCURRENCY)


@dataclass(frozen=True, slots=True)
class AppContainer:
    env: Mapping[str, str]
    config: Config
    http_client: httpx.AsyncClient
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    color_mode: ColorMode = ColorMode.AUTO
    budget: CallBudget | None = None  # --max-calls (D18); None = uncapped
    stop: asyncio.Event | None = None  # the per-item verbs' drain event (ux.md §12)
    caches: list[object] = field(default_factory=list[object])  # CachingChatModel wrappers (D38/15)
    coalescers: list[object] = field(default_factory=list[object])  # batching wrappers (item 62)
    window_cache: dict[str, int | None] = field(default_factory=dict[str, "int | None"])
    call_policy: OutboundCallPolicy = field(default_factory=_default_call_policy)

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        resolved = await resolve_chat_ref(flag, self.env, self.config, self.probe_ollama)
        if resolved.notice is not None:
            diagnostics.note(resolved.notice)
        manifest.record_model("chat", str(resolved.ref))
        return self._wrap_chat(self._build_chat(resolved.ref))

    async def resilient_chat_model(
        self, flag: str | None = None, fallback_flag: str | None = None
    ) -> WiredChat:
        """The composed resilient chat model a per-item verb runs on (item 11):
        the primary wire wrapped in the run's breaker + concurrency gate with the
        configured fallback armed LAZILY underneath it. The failover swaps by
        itself on a provider-down trip — the verb calls one plain ``ChatModel``
        (``wired.model``) and never branches on the wire's health; ``wired.route``
        keeps the answered-per-model receipt honest. The fallback ref resolves
        HERE, before a cent is spent (an embedding-model fallback is refused at
        this line, exactly as ``fallback_ref``'s eager fence)."""
        from smartpipe.models.resilience import WiredChat

        resolved = await resolve_chat_ref(flag, self.env, self.config, self.probe_ollama)
        if resolved.notice is not None:
            diagnostics.note(resolved.notice)
        manifest.record_model("chat", str(resolved.ref))
        fallback = self.fallback_ref(fallback_flag)  # embedder/fence refused here, pre-spend
        resilient = self._resilient_chat_core(self._build_chat(resolved.ref), fallback_ref=fallback)
        return WiredChat(
            model=self._wrap_outer(resilient),
            route=resilient.route,
            primary_ref=resolved.ref,
            fallback_ref=fallback,
        )

    def batching(self) -> BatchSettings | None:
        """The run's coalescing posture (item 62): SMARTPIPE_BATCH > config
        ``batching`` > ON (the product pitch is seamless cost reduction).
        None = off; verbs size their intake from the returned settings."""
        if not _batching_enabled(self.env, self.config):
            return None
        from smartpipe.engine.coalesce import BatchSettings

        return BatchSettings(
            size=_batch_size(self.env), window_seconds=_batch_window_seconds(self.env)
        )

    def _wrap_chat(self, model: ChatModel) -> ChatModel:
        """The no-fallback resilient chat build (solo ``chat_model``, the vision
        OCR wire): the breaker + concurrency-gate core with no failover, plus the
        shared outer coalescer/cache."""
        return self._wrap_outer(self._resilient_chat_core(model))

    def _resilient_chat_core(
        self, model: ChatModel, *, fallback_ref: ModelRef | None = None
    ) -> ResilientChatModel:
        """Budget the wire, then wrap it in the run's breaker + concurrency gate
        as a standalone ``ResilientChatModel`` — the doctrine: robustness is
        COMPOSED combinators at the root, not something the caller reasons about.
        The breaker + gate that ``OutboundCallPolicy`` used to own are now a
        standalone ``Breaker`` and a ``rate_limited`` gate stacked here; embed/
        OCR/STT keep ``admitted_*`` (this decomposition is chat-only, because
        failover is a chat concern). When ``fallback_ref`` is set, the breaker's
        trip builds that model LAZILY (through ``_build_fallback_adapter``) and
        swaps to it wholesale — announced on stderr, the receipt reading ``.route``.
        """
        from smartpipe.models.resilience import Breaker, Cooldown, ResilientChatModel

        wired = model if self.budget is None else budgeted_chat(model, self.budget)
        fallback_factory: Callable[[], Awaitable[ChatModel]] | None = None
        if fallback_ref is not None:
            target = fallback_ref

            async def build_fallback() -> ChatModel:
                return self._build_fallback_adapter(target)

            fallback_factory = build_fallback
        return ResilientChatModel(
            wired,
            breaker=Breaker(limit=self.call_policy.breaker_limit),
            concurrency=self.call_policy.concurrency,
            cooldown=Cooldown(),
            fallback_factory=fallback_factory,
            fallback_ref=fallback_ref,
            announce=diagnostics.warn,
            note=diagnostics.note,
        )

    def _build_fallback_adapter(self, ref: ModelRef) -> ChatModel:
        """The failover model as a RAW budgeted adapter — no coalescer/cache of
        its own. The shared coalescer sits OUTSIDE the breaker and re-packs the
        replayed window onto whatever the breaker routes to, so the fallback must
        not double-coalesce; the budget belt is shared. Keys/login are checked
        HERE (on the first trip), so an unusable fallback surfaces as
        ``circuit_broken``'s honest 'unusable' note and the run dies loudly."""
        manifest.record_model("chat_fallback", str(ref))
        adapter = self._build_chat(ref)
        return adapter if self.budget is None else budgeted_chat(adapter, self.budget)

    def _wrap_outer(self, model: ChatModel) -> ChatModel:
        """Wrap a resilient chat core in the run's OUTER layers — the coalescer
        (batching) then the cache — shared by every chat build. The layering is
        load-bearing: cache → coalescer → breaker+gate → budget → adapter, so a
        hit never enqueues, one packed flight is one charged call (item 62 §5/§9),
        and a replayed window re-packs onto the swapped fallback target."""
        wired = model
        settings = self.batching()
        if settings is not None:
            from smartpipe.models.coalesce import CoalescingChatModel

            wired = CoalescingChatModel(
                wired,
                settings=settings,
                stop=self.stop,
            )
            self.coalescers.append(wired)
        if not _cache_enabled(self.env, self.config):
            return wired
        # cache OUTERMOST: a hit short-circuits before the budget counts it —
        # the belt caps SPEND, not answers (D38/15)
        from smartpipe.models.cache import CachingChatModel

        wrapper = CachingChatModel(wired, _cache_dir(self.env))
        self.caches.append(wrapper)
        return wrapper

    def _cache_parser(self, parser: DocumentParser) -> DocumentParser:
        """Cache the dedicated OCR wire OUTERMOST (A7), mirroring ``_wrap_outer``'s
        chat layering: cache → admission → budget → parser, so a hit short-circuits
        before admission gates it or the page belt meters it — a rerun re-reads a
        banked conversion for free (a pilot paid 943 conversions that reruns re-paid).
        Gated on the same posture and dir as the chat cache, and appended to
        ``self.caches`` so the shared daily TTL/LRU sweep covers it too (D38/15)."""
        if not _cache_enabled(self.env, self.config):
            return parser
        from smartpipe.models.cache import CachingDocumentParser

        wrapper = CachingDocumentParser(parser, _cache_dir(self.env))
        self.caches.append(wrapper)
        return wrapper

    def _cache_embed(self, model: EmbeddingModel) -> EmbeddingModel:
        """Bank per-TEXT vectors OUTERMOST (#22), mirroring ``_cache_parser``:
        cache → admission → budget → adapter, so a hit never takes the outbound
        semaphore or charges the belt. ``cached_embed`` splits on the media
        capability so a joint embedder keeps its ``embed_parts`` marker (the
        verbs probe it on the WRAPPED model). Called once per build — a caller
        that builds twice (run_graph's #27 preflight) appends two wrappers, and
        the receipt stays honest because the discarded one counts zero traffic."""
        if not _cache_enabled(self.env, self.config):
            return model
        from smartpipe.models.cache import cached_embed

        wrapper = cached_embed(model, _cache_dir(self.env))
        self.caches.append(wrapper)
        return wrapper

    def _cache_stt(self, transcriber: Transcriber) -> Transcriber:
        """Bank remote transcriptions OUTERMOST (#22), same posture/dir/sweep
        as every other cache surface."""
        if not _cache_enabled(self.env, self.config):
            return transcriber
        from smartpipe.models.cache import CachingTranscriber

        wrapper = CachingTranscriber(transcriber, _cache_dir(self.env))
        self.caches.append(wrapper)
        return wrapper

    def probe_fallback_model(self) -> ChatModel | None:
        """Build the configured fallback as a direct probe wire, without waiting
        for a breaker trip. This is probe-only composition-root wiring."""
        ref = self.fallback_ref()
        if ref is None:
            return None
        return self._wrap_chat(self._build_chat(ref))

    def fallback_ref(self, flag: str | None = None) -> ModelRef | None:
        """The chat failover target (item 11): --fallback-model >
        SMARTPIPE_FALLBACK_MODEL > config, or None when unset. Chat wires only —
        an embedding-model ref is refused HERE, at resolution time, before any
        spend: mixed-embedder vectors are geometrically meaningless."""
        raw = (
            (flag or "").strip()
            or self.env.get("SMARTPIPE_FALLBACK_MODEL", "").strip()
            or (self.config.fallback_model or "").strip()
        )
        if not raw:
            return None
        ref = parse_model_ref(raw)
        self._fence(ref, "chat")  # a cloud fallback fails HERE, not at switch time
        if _looks_like_embedder(ref):
            raise UsageFault(
                f"fallback-model works for chat models only — '{raw}' embeds\n"
                "  Mixed-embedder vectors are geometrically meaningless: two models'\n"
                "  vectors live in different spaces, so similarity across them is noise.\n"
                "  Pick a chat fallback, or drop the setting."
            )
        return ref

    async def context_window(self, ref: ModelRef) -> int | None:
        """The model's context window: env override > one cached live probe > None
        (D26 layer 1). Called lazily — only when chunking math needs it."""
        override = self.env.get("SMARTPIPE_CONTEXT_TOKENS", "").strip()
        if override:
            if not override.isdigit():
                raise SetupFault(
                    f"error: SMARTPIPE_CONTEXT_TOKENS must be a token count, got {override!r}\n"
                    "  Example: SMARTPIPE_CONTEXT_TOKENS=32000"
                )
            return int(override)
        key = f"{ref.provider}/{ref.name}"
        if key not in self.window_cache:
            from smartpipe.models.windows import probe_context_window

            self.window_cache[key] = await probe_context_window(
                ref, client=self.http_client, env=self.env
            )
        return self.window_cache[key]

    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel:
        ref = resolve_embed_ref(flag, self.env, self.config)
        manifest.record_model("embed", str(ref))
        return self._wrap_embed(self._build_embed(ref))

    async def media_embedding_model(self, flag: str | None = None) -> EmbeddingModel | None:
        """The ``media-embed-model`` role (item 40): a JOINT text+image space
        that media items route to while text items keep ``embed-model``. Unset
        = None (today's resolution). A text-only ref is refused here, before
        any spend — pixels through a text embedder are a category error."""
        raw = (
            (flag or "").strip()
            or self.env.get("SMARTPIPE_MEDIA_EMBED_MODEL", "").strip()
            or (self.config.media_embed_model or "").strip()
        )
        if not raw:
            return None
        from smartpipe.models.base import supports_media_embedding

        media_ref = parse_model_ref(raw)
        self._fence(media_ref, "media_embed")  # the media-role wording, before embed's
        manifest.record_model("media_embed", str(media_ref))
        model = self._build_embed(media_ref)
        probe: object = model  # narrow a VIEW, not the binding — the return stays typed
        if not supports_media_embedding(probe):
            raise SetupFault(
                f"error: media-embed-model needs a joint text+image embedder — "
                f"'{raw}' reads text only\n"
                "  Media items embed as pixels only in a joint space "
                "(e.g. jina/jina-clip-v2).\n"
                '  Set one in config.toml: media-embed-model = "jina/jina-clip-v2" — '
                "or unset the role."
            )
        return self._wrap_embed(model)

    def document_parser(self, flag: str | None = None) -> DocumentParser | None:
        """The ``ocr-model`` role (item 40): when set, ingested PDFs and images
        parse through it (owner ruling — configuring the role IS the consent;
        every use is disclosed per row). A mistral ref rides the dedicated
        ``/v1/ocr`` wire; any other ref reads pages through the normal
        chat-vision wire with an extract-the-text framing."""
        raw = (
            (flag or "").strip()
            or self.env.get("SMARTPIPE_OCR_MODEL", "").strip()
            or (self.config.ocr_model or "").strip()
        )
        if not raw:
            return None
        ref = parse_model_ref(raw)
        self._fence(ref, "ocr")  # a vision rung re-checks via _build_chat; harmless
        manifest.record_model("ocr", str(ref))
        if ref.provider == "mistral":
            from smartpipe.models.admission import admitted_parser
            from smartpipe.models.budget import budgeted_parser
            from smartpipe.models.ocr import OCR_RETRY_POLICY, MistralOcrParser

            parser = MistralOcrParser(
                ref=ref,
                client=self.http_client,
                api_key=require_api_key(self.env, ref.name, MISTRAL_WIRE),
                base_url=resolve_base_url(self.env, MISTRAL_WIRE),
                # A5.3: the paid wire gets the dedicated longer ladder (attempts=5,
                # cap ~30s), NOT the default chat retry — a page is cheaper to wait
                # on than to re-buy. Composition root decides the degree of protection.
                retry=OCR_RETRY_POLICY,
            )
            # item 48: the dedicated OCR wire wears the belt too — one charge
            # per page (the vision rung below is budgeted via its chat)
            wired = parser if self.budget is None else budgeted_parser(parser, self.budget)
            # A7: cache OUTERMOST so a rerun banks the paid page conversions —
            # the hit short-circuits before admission gates or the belt meters it
            return self._cache_parser(admitted_parser(wired, self.call_policy))
        from smartpipe.models.ocr import VisionOcrParser

        return VisionOcrParser(chat=self._wrap_chat(self._build_chat(ref)))

    def remote_transcriber(
        self, chat_ref: ModelRef | None = None, *, flag: str | None = None
    ) -> Transcriber | None:
        """The stt-model role (D39/05), resolved through the shared matrix
        (``resolve_stt``): an explicit flag (#20: graph's ``--stt-model``) or
        env/config wins — ``local`` pins on-device whisper as rung 0;
        otherwise an openai KEY means whisper-1 (the API supports it;
        ChatGPT-login does not, so OAuth-only stays local); gemini hears
        natively (no preemption); ollama has no STT (ladder)."""
        resolution = resolve_stt(
            self.env,
            self.config.stt_model,
            chat_ref.provider if chat_ref is not None else None,
            flag=flag,
        )
        if resolution.kind == "ladder":
            return None
        if resolution.kind == "local":
            from smartpipe.models.stt import LocalTranscriber
            from smartpipe.parsing.extract import configured_whisper_size

            local_ref = ModelRef(provider="local", name=f"whisper-{configured_whisper_size()}")
            self._fence(local_ref, "stt")  # passes — the fence admits local wires
            manifest.record_model("stt", str(local_ref))
            return LocalTranscriber(ref=local_ref)  # free wire: no budget/admission belt
        assert resolution.ref is not None
        ref = parse_model_ref(resolution.ref)
        self._fence(ref, "stt")
        if ref.provider != "openai":
            raise SetupFault(
                f"error: no STT wire for {ref.provider!r} yet\n"
                "  Remote transcription supports openai models — in config.toml: "
                'stt-model = "openai/whisper-1"  (or "local" for on-device whisper)'
            )
        key = self.env.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise SetupFault(
                "error: remote transcription needs OPENAI_API_KEY\n"
                '  export OPENAI_API_KEY=sk-…   (or set stt-model = "local", '
                "or unset it for the ladder)"
            )
        from smartpipe.models.admission import admitted_transcriber
        from smartpipe.models.budget import budgeted_transcriber
        from smartpipe.models.stt import RemoteTranscriber

        manifest.record_model("stt", str(ref))
        adapter = RemoteTranscriber(
            ref=ref,
            client=self.http_client,
            api_key=key,
            retry=self.retry,
        )
        wired = adapter if self.budget is None else budgeted_transcriber(adapter, self.budget)
        # cache OUTERMOST (#22): a banked transcription never re-pays the wire,
        # admission, or the belt — reruns of the same clip are free
        return self._cache_stt(admitted_transcriber(wired, self.call_policy))

    def entity_finder(self, labels: Sequence[str]) -> EntityFinder:
        """``graph --fast``'s NER (wave G1): ALWAYS the local GLiNER wire —
        the free mode never routes entities through a paid model.

        Deliberately NOT cached (#22): span extraction is fast on-device
        inference with no wire to save, and per-text span payloads would flood
        the shared LRU (evicting banked PAID chat/OCR/embed entries) for zero
        cost reduction."""
        from smartpipe.models.local_ner import GlinerEntityFinder, ner_precision

        precision = ner_precision(self.env)
        manifest.record_model("ner", f"local/gliner-small-v2.1@{precision}")
        return GlinerEntityFinder(labels=tuple(labels), precision=precision)

    async def fold_embedder(self, flag: str | None = None) -> EmbeddingModel:
        """The graph's canonicalization fold embedder (owner ruling: specified
        wins, local fallback). It honors ``--embed-model`` > SMARTPIPE_EMBED_MODEL
        > config ``embed-model`` — the SAME resolution ``embedding_model`` uses —
        and falls back to the on-device local model (free) only when nothing is
        set. A configured cloud embed-model therefore folds through paid vectors
        in EVERY mode (``--fast`` included); ``fold_vectors`` discloses that."""
        ref = resolve_embed_ref(flag, self.env, self.config)
        manifest.record_model("fold_embed", str(ref))
        model = self._build_embed(ref)
        # The fold is graph infrastructure (like NER), not the user's metered
        # per-item work: a FREE wire stays OFF the billable belt - local on-device,
        # OR loopback/self-hosted ollama (the 3.14 default when fastembed's wheels
        # are absent), the same free split 373823b uses for disclosure - so a bare
        # ``--fast`` run can't charge --max-calls or flip a $0 fold to PARTIAL on
        # ANY python. A paid cloud fold is metered + admitted + disclosed via
        # ``_wrap_embed``.
        if ref.provider in ("local", "ollama"):
            # cached but NEVER budgeted/admitted: the free fold stays off-belt
            # (the cafa99b invariant) AND banks its vectors (#22) — a miss
            # embeds on the raw wire, a hit costs nothing, neither can charge
            # --max-calls. run_graph builds this twice (#27 preflight + fold);
            # each build appends its own wrapper, which keeps the receipt
            # honest because the discarded preflight wrapper counts zero.
            return self._cache_embed(model)
        return self._wrap_embed(model)

    def concurrency(self, flag: int | None = None) -> int:
        """Max parallel model calls: flag > SMARTPIPE_CONCURRENCY > config > default 4."""
        value = _resolve_concurrency(self.env, self.config, flag)
        from smartpipe.engine.runner import resolve_breaker_limit

        self.call_policy.configure(
            concurrency=value,
            breaker_limit=resolve_breaker_limit(self.env.get("SMARTPIPE_BREAKER", "")),
        )
        return value

    def failure_policy(self, provider: str) -> FailurePolicy:
        """The runner view of the already-resolved outbound breaker."""
        from smartpipe.engine.runner import FailurePolicy

        limit = self.call_policy.breaker_limit
        return FailurePolicy(
            transport_limit=limit,
            transport_screen=screens.provider_down(provider, limit),
        )

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
        mode = resolve_format(
            output_flag,
            self.env,
            stdout_tty=tty.stdout_is_tty(),
            structured=structured,
            fields=fields,
        )
        color = tty.stdout_supports_color(self.color_mode)
        width = tty.terminal_width()
        media_lines = None
        if mode is RenderMode.HUMAN:  # previews exist only where the block preview does
            from smartpipe.io.preview import maybe_preview

            media_lines = maybe_preview(
                enabled=self.config.media_previews is not False,  # unset = on
                color=color,
                width=width,
            )
        config = WriterConfig(
            mode=mode,
            color=color,
            width=width,
            fields=fields,
            bare=bare,
            full=full,
            media_lines=media_lines,
        )
        return make_writer(config, stdout)

    def _build_openai_chat(self, ref: ModelRef) -> ChatModel:
        """D19 precedence: an explicit API key (billable, deliberate) always wins;
        else a stored ChatGPT login rides the codex wire; else the dual-fix screen."""
        if self.env.get("OPENAI_API_KEY", "").strip():
            return OpenAIChatModel(
                ref=ref,
                client=self.http_client,
                base_url=resolve_base_url(self.env),
                api_key=require_api_key(self.env, ref.name),
                retry=self.retry,
            )
        from smartpipe.config.credentials import credentials_path, load_oauth

        store = credentials_path(self.env)
        credential = load_oauth(store, "openai")
        if credential is not None:
            from smartpipe.models.openai_codex import CodexChatModel

            return CodexChatModel(
                ref=ref,
                client=self.http_client,
                store_path=store,
                credential=credential,
                retry=self.retry,
            )
        raise SetupFault(screens.openai_needs_key_or_login(ref.name))

    def _build_openai_embed(self, ref: ModelRef) -> EmbeddingModel:
        if not self.env.get("OPENAI_API_KEY", "").strip():
            from smartpipe.config.credentials import credentials_path, load_oauth

            if load_oauth(credentials_path(self.env), "openai") is not None:
                raise SetupFault(screens.EMBEDDINGS_NEED_KEY)  # the login wire has no embeddings
        return OpenAIEmbeddingModel(
            ref=ref,
            client=self.http_client,
            base_url=resolve_base_url(self.env),
            api_key=require_api_key(self.env, ref.name),
            retry=self.retry,
        )

    def _wrap_embed(self, model: EmbeddingModel) -> EmbeddingModel:
        """Budget then admit one remote embedding request, then cache OUTERMOST.

        The built-in on-device embedder performs no API call, so it stays off
        the API-call semaphore. Ollama remains admitted: it is an HTTP API even
        when its endpoint is loopback. Both returns ride ``_cache_embed`` (#22)
        so a banked text never reaches admission or the belt.
        """
        wired = model if self.budget is None else budgeted_embed(model, self.budget)
        if model.ref.provider == "local":
            return self._cache_embed(wired)
        from smartpipe.models.admission import admitted_embed

        return self._cache_embed(admitted_embed(wired, self.call_policy))

    def _fence(self, ref: ModelRef, role: str) -> None:
        """--local-only (item 65d): refuse any wire that would leave this
        machine - HERE, at build time, before user data or spend leaves it."""
        from smartpipe.core.fence import ensure_local_wire

        ensure_local_wire(ref, self.env, role=role, ollama_host=resolve_host(self.env))

    def _build_chat(self, ref: ModelRef) -> ChatModel:
        self._fence(ref, "chat")
        match ref.provider:
            case "ollama":
                return OllamaChatModel(
                    ref=ref,
                    client=self.http_client,
                    host=resolve_host(self.env),
                    retry=self.retry,
                )
            case "openai":
                return self._build_openai_chat(ref)
            case "anthropic":
                return build_anthropic_chat_model(
                    ref,
                    api_key=self.env.get("ANTHROPIC_API_KEY", "").strip(),
                    http_client=self.http_client,
                    retry=self.retry,
                )
            case "mistral":  # the parametrized OpenAI wire (workstream 10)
                return self._wire_chat(ref, MISTRAL_WIRE)
            case "jina" | "local":
                raise SetupFault(
                    f"error: '{ref.name}' is an embedding model, not a chat model\n"
                    "  Pick a chat model instead: smartpipe use …"
                )
            case "gemini":  # D34: chat rides the NATIVE wire — the one that watches video
                from smartpipe.models.gemini_native import GeminiNativeChatModel, native_base_url
                from smartpipe.models.openai_compat import require_api_key

                return GeminiNativeChatModel(
                    ref=ref,
                    client=self.http_client,
                    base_url=native_base_url(self.env),
                    api_key=require_api_key(self.env, ref.name, GEMINI_WIRE),
                    retry=self.retry,
                )
            case "openrouter":
                return self._wire_chat(ref, OPENROUTER_WIRE)
            case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                assert_never(unreachable)

    def _wire_chat(self, ref: ModelRef, wire: WireConfig) -> ChatModel:
        return OpenAIChatModel(
            ref=ref,
            client=self.http_client,
            base_url=resolve_base_url(self.env, wire),
            api_key=require_api_key(self.env, ref.name, wire),
            retry=self.retry,
            wire=wire,
        )

    def _wire_embed(self, ref: ModelRef, wire: WireConfig) -> EmbeddingModel:
        return OpenAIEmbeddingModel(
            ref=ref,
            client=self.http_client,
            base_url=resolve_base_url(self.env, wire),
            api_key=require_api_key(self.env, ref.name, wire),
            retry=self.retry,
            wire=wire,
        )

    def _build_embed(self, ref: ModelRef) -> EmbeddingModel:
        self._fence(ref, "embed")
        match ref.provider:
            case "local":  # D44: the on-device default — no server, no key
                from smartpipe.models.local_embed import LocalEmbeddingModel

                return LocalEmbeddingModel(ref=ref)
            case "ollama":
                return OllamaEmbeddingModel(
                    ref=ref,
                    client=self.http_client,
                    host=resolve_host(self.env),
                    retry=self.retry,
                )
            case "openai":
                return self._build_openai_embed(ref)
            case "anthropic":
                raise SetupFault(
                    f"error: '{ref.name}' is a chat model, not an embedding model\n"
                    "  Claude models don't provide embeddings. Use a local one:\n"
                    "  unset embed-model (the built-in local embedder takes over)"
                )
            case "mistral":  # mistral-embed rides the same /v1/embeddings wire
                return self._wire_embed(ref, MISTRAL_WIRE)
            case "gemini":
                return self._wire_embed(ref, GEMINI_WIRE)
            case "openrouter":
                return self._wire_embed(ref, OPENROUTER_WIRE)
            case "jina":  # D39/04: the media-native space (text + images)
                key = self.env.get("JINA_API_KEY", "").strip()
                if not key:
                    raise SetupFault(
                        "error: Jina needs an API key\n  export JINA_API_KEY=…   (https://jina.ai)"
                    )
                from smartpipe.models.jina import JINA_BASE_URL, JinaClipEmbeddingModel

                base = self.env.get("SMARTPIPE_JINA_BASE_URL", "").strip().rstrip("/")
                return JinaClipEmbeddingModel(
                    ref=ref,
                    client=self.http_client,
                    api_key=key,
                    base_url=base or JINA_BASE_URL,
                    retry=self.retry,
                )
            case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                assert_never(unreachable)

    async def probe_ollama(self) -> tuple[str, ...] | None:
        """Installed ollama model names, or None if nothing is listening.
        Under --local-only a remote OLLAMA_HOST refuses BEFORE the probe -
        otherwise autodetection could select execution on another machine."""
        self._fence(ModelRef(provider="ollama", name="(autodetect)"), "chat")
        return await ollama_model_names(self.http_client, resolve_host(self.env))


def _looks_like_embedder(ref: ModelRef) -> bool:
    """Embed-only providers, or a name that says so (nomic-embed-text,
    text-embedding-3-small, gemini-embedding-001, mistral-embed, …)."""
    return ref.provider in ("local", "jina") or "embed" in ref.name.lower()


def _resolve_max_calls(environ: Mapping[str, str], flag: int | None) -> int | None:
    """--max-calls > SMARTPIPE_MAX_CALLS > uncapped; bad values are loud (D18)."""
    if flag is not None:
        if flag < 1:
            raise UsageFault(f"--max-calls must be >= 1, got {flag}")
        return flag
    env_value = environ.get("SMARTPIPE_MAX_CALLS", "").strip()
    if not env_value:
        return None
    if not (env_value.isdigit() and int(env_value) >= 1):
        raise UsageFault(f"SMARTPIPE_MAX_CALLS must be a whole number >= 1, got {env_value!r}")
    return int(env_value)


def _resolve_concurrency(env: Mapping[str, str], config: Config, flag: int | None) -> int:
    if flag is not None:
        if flag < 1:
            raise UsageFault(f"--concurrency must be >= 1, got {flag}")
        return flag
    env_value = env.get("SMARTPIPE_CONCURRENCY", "").strip()
    if env_value:
        if not (env_value.isdigit() and int(env_value) >= 1):
            raise UsageFault(
                f"SMARTPIPE_CONCURRENCY must be a whole number >= 1, got {env_value!r}"
            )
        return int(env_value)
    if config.concurrency is not None:
        return config.concurrency
    return _DEFAULT_CONCURRENCY


def _cache_enabled(env: Mapping[str, str], config: Config) -> bool:
    flag = env.get("SMARTPIPE_CACHE", "").strip().lower()
    if flag in ("1", "true", "on", "yes"):
        return True
    if flag in ("0", "false", "off", "no"):
        return False
    return config.cache is not False  # unset = on


def _batching_enabled(env: Mapping[str, str], config: Config) -> bool:
    """SMARTPIPE_BATCH > config ``batching`` > ON — the same posture ladder as
    the cache, but defaulting on: eligible items sharing a call IS the product."""
    flag = env.get("SMARTPIPE_BATCH", "").strip().lower()
    if flag in ("1", "true", "on", "yes"):
        return True
    if flag in ("0", "false", "off", "no"):
        return False
    return config.batching is not False  # unset = on


def _batch_size(env: Mapping[str, str]) -> int:
    """SMARTPIPE_BATCH_SIZE: items per packed call (K), code-capped at 12."""
    from smartpipe.engine.coalesce import MAX_BATCH_SIZE

    raw = env.get("SMARTPIPE_BATCH_SIZE", "").strip()
    if not raw:
        return MAX_BATCH_SIZE
    if not (raw.isdigit() and 2 <= int(raw) <= MAX_BATCH_SIZE):
        raise UsageFault(
            f"SMARTPIPE_BATCH_SIZE must be a whole number in 2..{MAX_BATCH_SIZE}, got {raw!r}"
        )
    return int(raw)


def _batch_window_seconds(env: Mapping[str, str]) -> float:
    """SMARTPIPE_BATCH_WINDOW_MS: how long a partial group waits before flying.
    Default 75 ms — streams must stay live."""
    from smartpipe.engine.coalesce import WINDOW_SECONDS

    raw = env.get("SMARTPIPE_BATCH_WINDOW_MS", "").strip()
    if not raw:
        return WINDOW_SECONDS
    if not (raw.isdigit() and int(raw) >= 1):
        raise UsageFault(f"SMARTPIPE_BATCH_WINDOW_MS must be a whole number >= 1, got {raw!r}")
    return int(raw) / 1000.0


def _cache_dir(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_CACHE_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".cache"
    return root / "smartpipe" / "results"


@asynccontextmanager
async def build_container(
    environ: Mapping[str, str],
    *,
    color_mode: ColorMode = ColorMode.AUTO,
    max_calls: int | None = None,
    stop: asyncio.Event | None = None,
) -> AsyncGenerator[AppContainer]:
    """Build the container for one invocation and own the HTTP client's lifecycle.

    ``stop`` is the drain event of the per-item verbs: a tripped call budget stops
    intake through it. Whole-set verbs pass no stop — exhaustion there is fatal.
    """
    limit = _resolve_max_calls(environ, max_calls)
    config = load_config(config_path(environ), warn=diagnostics.warn)
    from smartpipe.config.credentials import keys_path, overlay_stored_keys, stored_api_keys
    from smartpipe.engine.schema import reset_deterministic_repairs
    from smartpipe.io import metering, source_accounting
    from smartpipe.parsing.extract import (
        configure_transcript_cache,
        configure_whisper_size,
        reset_transcript_cache,
        reset_whisper_size,
        whisper_size,
    )
    from smartpipe.verbs.common import reset_run_disclosures

    resolved_env = overlay_stored_keys(environ, stored_api_keys(keys_path(environ)))
    from smartpipe.engine.runner import resolve_breaker_limit
    from smartpipe.models.admission import OutboundCallPolicy

    call_concurrency = _resolve_concurrency(resolved_env, config, None)
    breaker_limit = resolve_breaker_limit(resolved_env.get("SMARTPIPE_BREAKER", ""))
    from smartpipe.core.fence import local_only

    client = make_client(trust_env=not local_only(resolved_env))
    metering.reset()  # a fresh run's meter (D40)
    reset_deterministic_repairs()  # rung 0's tally is run-scoped, like the meter (item 58)
    reset_run_disclosures()  # native-embedding/date notes are once per invocation
    from smartpipe.models.ollama import reset_ollama_disclosures

    reset_ollama_disclosures()
    manifest.reset()  # the --manifest collector is run-scoped too (item 65a); begin() re-arms
    source_accounting.reset()  # dropped inputs/OCR owners are invocation-scoped
    # C5 review: the construction window below can raise (bank/cache-dir, budget,
    # policy) — a mid-build fault must reset every token already installed and
    # close the client, or invocation state and the HTTP client leak past this
    # invocation. None-sentinels track exactly what to unwind.
    whisper_token = None
    transcript_token = None
    try:
        whisper_token = configure_whisper_size(whisper_size(resolved_env))
        # the local whisper transcript bank (#22) rides the same ContextVar-at-seam
        # pattern as the size above: installed here, reset in the same finally;
        # posture off ⇒ None ⇒ transcribe_audio stays byte-identical
        bank = None
        if _cache_enabled(resolved_env, config):
            from smartpipe.models.cache import TranscriptBank

            bank = TranscriptBank(_cache_dir(resolved_env))
        transcript_token = configure_transcript_cache(bank)
        container = AppContainer(
            # env > stored key, per provider — `auth login`'s store fills only the gaps
            env=resolved_env,
            config=config,
            http_client=client,
            color_mode=color_mode,
            budget=None if limit is None else CallBudget(limit=limit, stop=stop),
            stop=stop,
            call_policy=OutboundCallPolicy(
                concurrency=call_concurrency,
                breaker_limit=breaker_limit,
            ),
        )
        if bank is not None:
            container.caches.append(bank)  # the receipt and the daily sweep both see it
    except BaseException:
        if transcript_token is not None:
            reset_transcript_cache(transcript_token)
        if whisper_token is not None:
            reset_whisper_size(whisper_token)
        await client.aclose()
        raise
    try:
        yield container
    finally:
        try:
            try:
                await _close_coalescers(container)
            finally:
                await client.aclose()
            totals = metering.receipt()
            if totals is not None:
                diagnostics.note(totals)  # D40: the number that goes in the report
            _repair_receipt()  # rung 0's once-per-run disclosure (item 58)
            _batch_receipt(container)  # item 62 §9: the once-per-run batching disclosure
            from smartpipe.io import usage

            usage.record_run(metering.snapshot(), container.env)  # D41: the ledger
            _cache_receipt(container)
        finally:
            source_accounting.discard()
            # pyright proves both tokens non-None here: the yield is reachable
            # only after the construction try completed both assignments
            reset_whisper_size(whisper_token)
            reset_transcript_cache(transcript_token)


def _repair_receipt() -> None:
    """One dim note per run when rung 0 saved replies — never per item."""
    from smartpipe.engine.schema import deterministic_repairs

    count = deterministic_repairs()
    if count:
        noun = "reply" if count == 1 else "replies"
        diagnostics.note(f"{count} {noun} repaired deterministically (fences/commas/quotes)")


def _batch_receipt(container: AppContainer) -> None:
    """ONE stderr note per run, only when batching actually happened — the
    accounting-honesty disclosure (item 62 §9). stdout never sees a byte."""
    if not container.coalescers:
        return
    from smartpipe.models.coalesce import CoalescingChatModel

    wrappers = [w for w in container.coalescers if isinstance(w, CoalescingChatModel)]
    calls = sum(w.packed_calls for w in wrappers)
    items = sum(w.packed_items for w in wrappers)
    recoveries = sum(w.solo_recoveries for w in wrappers)
    if calls:
        item_noun = "item" if items == 1 else "items"
        call_noun = "call" if calls == 1 else "calls"
        message = f"batching: {items:,} {item_noun} in {calls:,} packed {call_noun}"
        if recoveries:
            recovery_noun = "recovery" if recoveries == 1 else "recoveries"
            message = f"{message} · {recoveries:,} solo {recovery_noun}"
        diagnostics.note(message)


def _cache_receipt(container: AppContainer) -> None:
    from smartpipe.models.cache import (
        CachingChatModel,
        CachingDocumentParser,
        CachingEmbeddingModel,
        CachingMediaEmbeddingModel,
        CachingTranscriber,
        TranscriptBank,
    )

    # all six cache-bearing surfaces sum into the ONE receipt line (#22):
    # chat, OCR pages (A7), text embeds, media-marked embeds, remote STT,
    # and the local whisper transcript bank
    banked = tuple(
        w
        for w in container.caches
        if isinstance(
            w,
            CachingChatModel
            | CachingDocumentParser
            | CachingEmbeddingModel
            | CachingMediaEmbeddingModel
            | CachingTranscriber
            | TranscriptBank,
        )
    )
    hits = sum(w.hits for w in banked)
    misses = sum(w.misses for w in banked)
    if hits or misses:
        diagnostics.note(f"cache: {hits:,} hits · {misses:,} misses")
    if container.caches:
        _maybe_sweep(container)


def _maybe_sweep(container: AppContainer) -> None:
    """TTL + LRU sweep at exit, at most daily — the cache is never the user's
    problem (D39/02). Any filesystem trouble is swallowed: a broken cache dir
    must never fail a run."""
    import time

    from smartpipe.models.cache import sweep

    directory = _cache_dir(container.env)
    marker = directory / "last-sweep"
    try:
        now = time.time()
        if marker.exists() and now - marker.stat().st_mtime < 86_400:
            return
        directory.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
        removed, freed = sweep(
            directory,
            ttl_days=container.config.cache_days or 30,
            max_mb=container.config.cache_max_mb or 500,
            now=now,
        )
        if removed:
            diagnostics.note(f"cache: swept {removed:,} entries ({freed / 1_048_576:.1f} MB)")
    except OSError:
        return


async def _close_coalescers(container: AppContainer) -> None:
    """Join every batching timer/flight before their shared client closes."""
    from smartpipe.models.coalesce import CoalescingChatModel

    wrappers = tuple(
        wrapper for wrapper in container.coalescers if isinstance(wrapper, CoalescingChatModel)
    )
    outcomes = await asyncio.gather(
        *(wrapper.aclose() for wrapper in wrappers),
        return_exceptions=True,
    )
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
