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
from smartpipe.io import diagnostics, tty
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
from smartpipe.models.resolve import resolve_chat_ref, resolve_embed_ref
from smartpipe.models.retry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping, Sequence

    import httpx

    from smartpipe.engine.coalesce import BatchSettings
    from smartpipe.engine.graphkg import EntityFinder
    from smartpipe.io.writers import ResultWriter, TextSink
    from smartpipe.models.base import ChatModel, EmbeddingModel
    from smartpipe.models.ocr import DocumentParser
    from smartpipe.models.stt import RemoteTranscriber

__all__ = ["AppContainer", "build_container"]

_DEFAULT_CONCURRENCY = 4


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

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        resolved = await resolve_chat_ref(flag, self.env, self.config, self.probe_ollama)
        if resolved.notice is not None:
            diagnostics.note(resolved.notice)
        return self._wrap_chat(self._build_chat(resolved.ref))

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
        wired = model if self.budget is None else budgeted_chat(model, self.budget)
        settings = self.batching()
        if settings is not None:
            # coalescer INSIDE the cache, OUTSIDE the budget: hits never enqueue,
            # and one packed flight is one charged call (item 62 §5/§9)
            from smartpipe.models.coalesce import CoalescingChatModel

            wired = CoalescingChatModel(wired, settings=settings, stop=self.stop)
            self.coalescers.append(wired)
        if not _cache_enabled(self.env, self.config):
            return wired
        # cache OUTERMOST: a hit short-circuits before the budget counts it —
        # the belt caps SPEND, not answers (D38/15)
        from smartpipe.models.cache import CachingChatModel

        wrapper = CachingChatModel(wired, _cache_dir(self.env))
        self.caches.append(wrapper)
        return wrapper

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
        if _looks_like_embedder(ref):
            raise UsageFault(
                f"fallback-model works for chat models only — '{raw}' embeds\n"
                "  Mixed-embedder vectors are geometrically meaningless: two models'\n"
                "  vectors live in different spaces, so similarity across them is noise.\n"
                "  Pick a chat fallback, or drop the setting."
            )
        return ref

    async def fallback_chat_model(self, ref: ModelRef) -> ChatModel:
        """The failover model, built at SWITCH time through the normal wire —
        keys/login are checked here, so a fallback with missing credentials
        surfaces as the ordinary SetupFault (the caller notes it and dies on
        the provider-down screen)."""
        return self._wrap_chat(self._build_chat(ref))

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
        model = self._build_embed(resolve_embed_ref(flag, self.env, self.config))
        return model if self.budget is None else budgeted_embed(model, self.budget)

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

        model = self._build_embed(parse_model_ref(raw))
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
        return model if self.budget is None else budgeted_embed(model, self.budget)

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
        if ref.provider == "mistral":
            from smartpipe.models.budget import budgeted_parser
            from smartpipe.models.ocr import MistralOcrParser

            parser = MistralOcrParser(
                ref=ref,
                client=self.http_client,
                api_key=require_api_key(self.env, ref.name, MISTRAL_WIRE),
                base_url=resolve_base_url(self.env, MISTRAL_WIRE),
                retry=self.retry,
            )
            # item 48: the dedicated OCR wire wears the belt too — one charge
            # per parse call (the vision rung below is budgeted via its chat)
            return parser if self.budget is None else budgeted_parser(parser, self.budget)
        from smartpipe.models.ocr import VisionOcrParser

        return VisionOcrParser(chat=self._wrap_chat(self._build_chat(ref)))

    def remote_transcriber(self, chat_ref: ModelRef | None = None) -> RemoteTranscriber | None:
        """The stt-model role (D39/05): explicit env/config wins; otherwise the
        owner's auto-matrix — an openai KEY means whisper-1 (the API supports
        it; ChatGPT-login does not, so OAuth-only stays local); gemini hears
        natively (no preemption); ollama has no STT (local whisper)."""
        raw = self.env.get("SMARTPIPE_STT_MODEL", "").strip() or (self.config.stt_model or "")
        if not raw:
            if (
                chat_ref is not None
                and chat_ref.provider == "openai"
                and self.env.get("OPENAI_API_KEY", "").strip()
            ):
                raw = "openai/whisper-1"  # the key wire supports transcriptions
            else:
                return None
        ref = parse_model_ref(raw)
        if ref.provider != "openai":
            raise SetupFault(
                f"error: no STT wire for {ref.provider!r} yet\n"
                "  Remote transcription supports openai models — in config.toml: "
                'stt-model = "openai/whisper-1"'
            )
        key = self.env.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise SetupFault(
                "error: remote transcription needs OPENAI_API_KEY\n"
                "  export OPENAI_API_KEY=sk-…   (or unset stt-model to use the ladder)"
            )
        from smartpipe.models.stt import RemoteTranscriber

        return RemoteTranscriber(ref=ref, client=self.http_client, api_key=key, retry=self.retry)

    def entity_finder(self, labels: Sequence[str]) -> EntityFinder:
        """``graph --fast``'s NER (wave G1): ALWAYS the local GLiNER wire —
        the free mode never routes entities through a paid model."""
        from smartpipe.models.local_ner import GlinerEntityFinder

        return GlinerEntityFinder(labels=tuple(labels))

    def fold_embedder(self) -> EmbeddingModel:
        """``graph --fast``'s canonicalization embedder: ALWAYS local, whatever
        ``embed-model`` says — free by definition means no cloud vectors."""
        from smartpipe.models.local_embed import LocalEmbeddingModel

        return LocalEmbeddingModel(ref=parse_model_ref("local/nomic-embed-text-v1.5"))

    def concurrency(self, flag: int | None = None) -> int:
        """Max parallel model calls: flag > SMARTPIPE_CONCURRENCY > config > default 4."""
        if flag is not None:
            if flag < 1:
                raise UsageFault(f"--concurrency must be >= 1, got {flag}")
            return flag
        env_value = self.env.get("SMARTPIPE_CONCURRENCY", "").strip()
        if env_value:
            if not (env_value.isdigit() and int(env_value) >= 1):
                raise UsageFault(
                    f"SMARTPIPE_CONCURRENCY must be a whole number >= 1, got {env_value!r}"
                )
            return int(env_value)
        if self.config.concurrency is not None:
            return self.config.concurrency
        return _DEFAULT_CONCURRENCY

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
                ref=ref, client=self.http_client, store_path=store, credential=credential
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

    def _build_chat(self, ref: ModelRef) -> ChatModel:
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
                return build_anthropic_chat_model(ref)
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
        """Installed ollama model names, or None if nothing is listening."""
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


def _cache_enabled(env: Mapping[str, str], config: Config) -> bool:
    flag = env.get("SMARTPIPE_CACHE", "").strip().lower()
    if flag in ("1", "true", "on", "yes"):
        return True
    if flag in ("0", "false", "off", "no"):
        return False
    return bool(config.cache)


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
    """SMARTPIPE_BATCH_SIZE: items per packed call (K). Default 12; ≥ 2 —
    a K of one would only add window latency to every item."""
    from smartpipe.engine.coalesce import GROUP_CEILING

    raw = env.get("SMARTPIPE_BATCH_SIZE", "").strip()
    if not raw:
        return GROUP_CEILING
    if not (raw.isdigit() and int(raw) >= 2):
        raise UsageFault(f"SMARTPIPE_BATCH_SIZE must be a whole number >= 2, got {raw!r}")
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
    client = make_client()
    from smartpipe.config.credentials import keys_path, overlay_stored_keys, stored_api_keys
    from smartpipe.engine.schema import reset_deterministic_repairs
    from smartpipe.io import metering

    metering.reset()  # a fresh run's meter (D40)
    reset_deterministic_repairs()  # rung 0's tally is run-scoped, like the meter (item 58)
    container = AppContainer(
        # env > stored key, per provider — `auth login`'s store fills only the gaps
        env=overlay_stored_keys(environ, stored_api_keys(keys_path(environ))),
        config=config,
        http_client=client,
        color_mode=color_mode,
        budget=None if limit is None else CallBudget(limit=limit, stop=stop),
        stop=stop,
    )
    try:
        yield container
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
    items = sum(w.batched_items for w in wrappers)
    if calls:
        noun = "call" if calls == 1 else "calls"
        diagnostics.note(f"batched {items:,} items into {calls:,} {noun}")


def _cache_receipt(container: AppContainer) -> None:
    from smartpipe.models.cache import CachingChatModel

    hits = sum(w.hits for w in container.caches if isinstance(w, CachingChatModel))
    misses = sum(w.misses for w in container.caches if isinstance(w, CachingChatModel))
    if hits or misses:
        diagnostics.note(f"cache: {hits:,} hits · {misses:,} calls")
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
