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
from typing import TYPE_CHECKING, assert_never

from sempipe.cli import screens
from sempipe.config.paths import config_path
from sempipe.config.store import Config, load_config
from sempipe.core.errors import SetupFault, UsageFault
from sempipe.io import diagnostics, tty
from sempipe.io.tty import ColorMode
from sempipe.io.writers import OutputFormat, WriterConfig, make_writer, resolve_format
from sempipe.models.anthropic_adapter import build_anthropic_chat_model
from sempipe.models.base import ModelRef
from sempipe.models.budget import CallBudget, budgeted_chat, budgeted_embed
from sempipe.models.http_support import make_client
from sempipe.models.ollama import (
    OllamaChatModel,
    OllamaEmbeddingModel,
    ollama_model_names,
    resolve_host,
)
from sempipe.models.openai_compat import (
    MISTRAL_WIRE,
    OpenAIChatModel,
    OpenAIEmbeddingModel,
    require_api_key,
    resolve_base_url,
)
from sempipe.models.resolve import resolve_chat_ref, resolve_embed_ref
from sempipe.models.retry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from typing import TextIO

    import httpx

    from sempipe.io.writers import ResultWriter
    from sempipe.models.base import ChatModel, EmbeddingModel

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

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        resolved = await resolve_chat_ref(flag, self.env, self.config, self.probe_ollama)
        if resolved.notice is not None:
            diagnostics.note(resolved.notice)
        model = self._build_chat(resolved.ref)
        return model if self.budget is None else budgeted_chat(model, self.budget)

    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel:
        model = self._build_embed(resolve_embed_ref(flag, self.env, self.config))
        return model if self.budget is None else budgeted_embed(model, self.budget)

    def concurrency(self, flag: int | None = None) -> int:
        """Max parallel model calls: flag > SEMPIPE_CONCURRENCY > config > default 4."""
        if flag is not None:
            if flag < 1:
                raise UsageFault(f"--concurrency must be >= 1, got {flag}")
            return flag
        env_value = self.env.get("SEMPIPE_CONCURRENCY", "").strip()
        if env_value:
            if not (env_value.isdigit() and int(env_value) >= 1):
                raise UsageFault(
                    f"SEMPIPE_CONCURRENCY must be a whole number >= 1, got {env_value!r}"
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
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter:
        mode = resolve_format(
            output_flag,
            self.env,
            stdout_tty=tty.stdout_is_tty(),
            structured=structured,
            fields=fields,
        )
        config = WriterConfig(
            mode=mode,
            color=tty.stdout_supports_color(self.color_mode),
            width=tty.terminal_width(),
            fields=fields,
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
        from sempipe.config.credentials import credentials_path, load_oauth

        store = credentials_path(self.env)
        credential = load_oauth(store, "openai")
        if credential is not None:
            from sempipe.models.openai_codex import CodexChatModel

            return CodexChatModel(
                ref=ref, client=self.http_client, store_path=store, credential=credential
            )
        raise SetupFault(screens.openai_needs_key_or_login(ref.name))

    def _build_openai_embed(self, ref: ModelRef) -> EmbeddingModel:
        if not self.env.get("OPENAI_API_KEY", "").strip():
            from sempipe.config.credentials import credentials_path, load_oauth

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
            case "mistral":  # same wire as OpenAI, parametrized (workstream 10)
                return OpenAIChatModel(
                    ref=ref,
                    client=self.http_client,
                    base_url=resolve_base_url(self.env, MISTRAL_WIRE),
                    api_key=require_api_key(self.env, ref.name, MISTRAL_WIRE),
                    retry=self.retry,
                    wire=MISTRAL_WIRE,
                )
            case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                assert_never(unreachable)

    def _build_embed(self, ref: ModelRef) -> EmbeddingModel:
        match ref.provider:
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
                    "  sempipe config embed-model nomic-embed-text"
                )
            case "mistral":  # mistral-embed rides the same /v1/embeddings wire
                return OpenAIEmbeddingModel(
                    ref=ref,
                    client=self.http_client,
                    base_url=resolve_base_url(self.env, MISTRAL_WIRE),
                    api_key=require_api_key(self.env, ref.name, MISTRAL_WIRE),
                    retry=self.retry,
                    wire=MISTRAL_WIRE,
                )
            case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                assert_never(unreachable)

    async def probe_ollama(self) -> tuple[str, ...] | None:
        """Installed ollama model names, or None if nothing is listening."""
        return await ollama_model_names(self.http_client, resolve_host(self.env))


def _resolve_max_calls(environ: Mapping[str, str], flag: int | None) -> int | None:
    """--max-calls > SEMPIPE_MAX_CALLS > uncapped; bad values are loud (D18)."""
    if flag is not None:
        if flag < 1:
            raise UsageFault(f"--max-calls must be >= 1, got {flag}")
        return flag
    env_value = environ.get("SEMPIPE_MAX_CALLS", "").strip()
    if not env_value:
        return None
    if not (env_value.isdigit() and int(env_value) >= 1):
        raise UsageFault(f"SEMPIPE_MAX_CALLS must be a whole number >= 1, got {env_value!r}")
    return int(env_value)


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
    config = load_config(config_path(environ))
    client = make_client()
    try:
        yield AppContainer(
            env=dict(environ),
            config=config,
            http_client=client,
            color_mode=color_mode,
            budget=None if limit is None else CallBudget(limit=limit, stop=stop),
        )
    finally:
        await client.aclose()
