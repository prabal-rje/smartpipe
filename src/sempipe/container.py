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

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_never

from sempipe.config.paths import config_path
from sempipe.config.store import Config, load_config
from sempipe.core.errors import SetupFault, UsageFault
from sempipe.io import diagnostics, tty
from sempipe.io.tty import ColorMode
from sempipe.io.writers import OutputFormat, WriterConfig, make_writer, resolve_format
from sempipe.models.anthropic_adapter import build_anthropic_chat_model
from sempipe.models.base import ModelRef
from sempipe.models.http_support import make_client
from sempipe.models.ollama import (
    OllamaChatModel,
    OllamaEmbeddingModel,
    ollama_model_names,
    resolve_host,
)
from sempipe.models.openai_compat import (
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

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        resolved = await resolve_chat_ref(flag, self.env, self.config, self.probe_ollama)
        if resolved.notice is not None:
            diagnostics.note(resolved.notice)
        return self._build_chat(resolved.ref)

    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel:
        return self._build_embed(resolve_embed_ref(flag, self.env, self.config))

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
        self, output_flag: OutputFormat, *, structured: bool, stdout: TextIO
    ) -> ResultWriter:
        mode = resolve_format(
            output_flag, self.env, stdout_tty=tty.stdout_is_tty(), structured=structured
        )
        config = WriterConfig(
            mode=mode,
            color=tty.stdout_supports_color(self.color_mode),
            width=tty.terminal_width(),
        )
        return make_writer(config, stdout)

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
                return OpenAIChatModel(
                    ref=ref,
                    client=self.http_client,
                    base_url=resolve_base_url(self.env),
                    api_key=require_api_key(self.env, ref.name),
                    retry=self.retry,
                )
            case "anthropic":
                return build_anthropic_chat_model(ref)
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
                return OpenAIEmbeddingModel(
                    ref=ref,
                    client=self.http_client,
                    base_url=resolve_base_url(self.env),
                    api_key=require_api_key(self.env, ref.name),
                    retry=self.retry,
                )
            case "anthropic":
                raise SetupFault(
                    f"error: '{ref.name}' is a chat model, not an embedding model\n"
                    "  Claude models don't provide embeddings. Use a local one:\n"
                    "  sempipe config embed-model nomic-embed-text"
                )
            case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                assert_never(unreachable)

    async def probe_ollama(self) -> tuple[str, ...] | None:
        """Installed ollama model names, or None if nothing is listening."""
        return await ollama_model_names(self.http_client, resolve_host(self.env))


@asynccontextmanager
async def build_container(
    environ: Mapping[str, str],
    *,
    color_mode: ColorMode = ColorMode.AUTO,
) -> AsyncGenerator[AppContainer]:
    """Build the container for one invocation and own the HTTP client's lifecycle."""
    config = load_config(config_path(environ))
    client = make_client()
    try:
        yield AppContainer(
            env=dict(environ),
            config=config,
            http_client=client,
            color_mode=color_mode,
        )
    finally:
        await client.aclose()
