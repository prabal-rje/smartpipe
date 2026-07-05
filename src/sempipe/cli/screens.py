"""Multi-line UX screens, verbatim from plan/ux.md (golden-pinned in tests).

Style contract (plan/ux.md): every screen contains its own fix — no error may
require opening a browser or reading docs to resolve.
"""

from __future__ import annotations

__all__ = [
    "FIELD_REF_ON_PLAIN_INPUT",
    "NO_MODEL",
    "WELCOME",
    "missing_anthropic_extra",
    "missing_api_key",
    "ollama_model_missing",
    "ollama_unreachable",
]

FIELD_REF_ON_PLAIN_INPUT = """\
error: the prompt references a {field}, but the input isn't JSON
  {field} substitution needs JSON Lines input (one object per line).
  Either drop the braces, or feed JSONL — e.g.: cat tickets.jsonl | sempipe filter ..."""

WELCOME = """\
sempipe — semantic pipes for your terminal

  map      Transform each item with a prompt
  filter   Keep items matching a semantic condition
  embed    Convert items to vector embeddings
  top_k    Rank items by similarity to a query
  reduce   Synthesize many items into one
  config   Configure models and settings

Get started:
  sempipe config                                     Interactive setup
  echo "hello" | sempipe map "translate to Spanish"

'sempipe <command> --help' shows examples for each command.
"""

NO_MODEL = """\
error: no model configured, and no local Ollama found

  Local (free, private):
    1. Install Ollama              https://ollama.com
    2. ollama pull qwen3:8b
    3. sempipe config model ollama/qwen3:8b

  Cloud (paid):
    sempipe config model claude-opus-4-8    then: export ANTHROPIC_API_KEY=sk-ant-...
    sempipe config model gpt-4o-mini        then: export OPENAI_API_KEY=sk-...

  Then rerun your command. 'sempipe config' walks you through this interactively."""


def ollama_unreachable(host: str, model: str, reason: str) -> str:
    return (
        f"error: can't reach ollama at {host} ({reason})\n"
        f"  The model '{model}' is configured, but nothing is listening there.\n"
        "  Start it with: ollama serve    (or check OLLAMA_HOST if it runs elsewhere)"
    )


def ollama_model_missing(name: str, host: str, detail: str) -> str:
    return (
        f"error: ollama doesn't have the model '{name}'\n"
        f"  ({host} answered: {detail})\n"
        f"  Fix: ollama pull {name}        (or check the name with: ollama list)"
    )


def missing_api_key(model: str, provider: str, env_var: str, key_shape: str) -> str:
    return (
        f"error: model '{model}' needs {_an(provider)} {provider} API key\n"
        f"  sempipe found no {env_var} in the environment. Keys are never stored in config.\n"
        f"  Fix: export {env_var}={key_shape}        (add it to your shell profile to persist)"
    )


def missing_anthropic_extra(model: str) -> str:
    return (
        f"error: model '{model}' needs the 'anthropic' extra\n"
        "  Claude models talk through the official SDK, which isn't installed\n"
        "  (sempipe stays small by default).\n"
        "  Fix: pip install 'sempipe[anthropic]'"
    )


def _an(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"
