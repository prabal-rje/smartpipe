"""Multi-line UX screens, verbatim from plan/ux.md (golden-pinned in tests).

Style contract (plan/ux.md): every screen contains its own fix — no error may
require opening a browser or reading docs to resolve.
"""

from __future__ import annotations

__all__ = [
    "BINARY_STDIN_UNPARSEABLE",
    "CHATGPT_LOGIN_EXPIRED",
    "EMBEDDINGS_NEED_KEY",
    "FIELD_REF_ON_PLAIN_INPUT",
    "NO_MODEL",
    "WELCOME",
    "cloud_model_missing",
    "missing_anthropic_extra",
    "missing_api_key",
    "ollama_model_missing",
    "ollama_unreachable",
    "openai_needs_key_or_login",
    "schema_rejected",
    "stdin_document_failed",
]

CHATGPT_LOGIN_EXPIRED = """\
error: the ChatGPT login has expired and couldn't be refreshed
  Fix: smartpipe auth login"""

EMBEDDINGS_NEED_KEY = """\
error: embeddings aren't available through ChatGPT login
  The ChatGPT plan wire serves chat models only.
  Fix: export OPENAI_API_KEY=sk-...
   or use a local model: smartpipe config embed-model nomic-embed-text"""


def openai_needs_key_or_login(model: str) -> str:
    return (
        f"error: model '{model}' needs an OpenAI API key or a ChatGPT login\n"
        "  smartpipe found no OPENAI_API_KEY and no ChatGPT login. Keys are never\n"
        "  stored in config.\n"
        "  Fix: export OPENAI_API_KEY=sk-...        (platform billing)\n"
        "   or: smartpipe auth login                  (use your ChatGPT Plus/Pro plan)"
    )


BINARY_STDIN_UNPARSEABLE = """\
error: stdin looks like binary data smartpipe can't parse
  Recognized on stdin: text lines, or a single PDF/DOCX/PPTX/XLSX/audio/image document.
  For files on disk use --in: smartpipe map "Summarize" --in 'report.pdf'"""


def stdin_document_failed(reason: str) -> str:
    return (
        f"error: stdin looks like a document, but it couldn't be read ({reason})\n"
        "  smartpipe reads ONE binary document per run from stdin.\n"
        "  Alternative: smartpipe map \"…\" --in 'report.pdf'"
    )


FIELD_REF_ON_PLAIN_INPUT = """\
error: the prompt references a {field}, but the first input line isn't JSON
  {field} substitution needs JSON Lines input (one object per line).
  Either drop the braces, or feed JSONL — e.g.: cat tickets.jsonl | smartpipe filter ..."""

WELCOME = """\
smartpipe — semantic pipes for your terminal
PDFs, images, audio, video, and text — verbs that understand their input.

Verbs (call a model):
  map      Transform each item with a prompt
  extend   Add extracted fields to each record
  filter   Keep items matching a semantic condition
  embed    Convert items to vector embeddings
  top_k    Rank items by similarity to a query
  reduce   Synthesize many items into one
  join     Match stdin against a second input, semantically
  cluster  Group items by meaning; label each group
  diff     What distinguishes two sets of items
  distinct Fold near-duplicate items (embeddings only)
  outliers Rank the items least like the rest (embeddings only)

Utilities (free — no model calls):
  where    Keep rows matching a deterministic predicate
  summarize Aggregate records: count/avg/percentiles by field
  sample   Keep N random rows (seeded, reproducible)
  getschema Report the stream's fields, types, coverage
  sort     Order records by a field (numbers, then strings)
  split    Break oversized items into chunks
  chart    Draw a bar chart of results (--save writes SVG)
  config   Configure models and settings

Get started:
  smartpipe config                                     Interactive setup
  echo "hello" | smartpipe map "translate to Spanish"

'smartpipe <command> --help' shows examples for each command.
"""

NO_MODEL = """\
error: no model configured, and no local Ollama found

  Local (free, private):
    1. Install Ollama              https://ollama.com
    2. ollama pull qwen3:8b
    3. smartpipe config model ollama/qwen3:8b

  Cloud (paid):
    smartpipe config model claude-opus-4-8    then: export ANTHROPIC_API_KEY=sk-ant-...
    smartpipe config model gpt-5.4-mini        then: export OPENAI_API_KEY=sk-...

  Then rerun your command. 'smartpipe config' walks you through this interactively."""


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


def missing_api_key(
    model: str,
    provider: str,
    env_var: str,
    key_shape: str,
    note: str = "add it to your shell profile to persist",
) -> str:
    return (
        f"error: model '{model}' needs {_an(provider)} {provider} API key\n"
        f"  smartpipe found no {env_var} in the environment. Keys are never stored in config.\n"
        f"  Fix: export {env_var}={key_shape}        ({note})"
    )


def cloud_model_missing(model: str, host: str) -> str:
    """D18: a 404 for the model dooms every item identically — stop at the first."""
    return (
        f"error: the endpoint doesn't know the model '{model}'\n"
        f"  {host} answered 404 — every item would fail identically, "
        "so smartpipe stopped at the first.\n"
        "  Fix: check the name, or set one that exists: smartpipe config model gpt-5.4-mini"
    )


def schema_rejected(host: str, detail: str) -> str:
    """D18: a schema the endpoint rejects dooms every item identically."""
    return (
        "error: the endpoint rejected the --schema\n"
        f"  {host} answered 400 (response_format): {detail}\n"
        "  Fix: simplify the schema — or drop --schema and validate downstream."
    )


def missing_anthropic_extra(model: str) -> str:
    return (
        f"error: model '{model}' needs the 'anthropic' extra\n"
        "  Claude models talk through the official SDK, which isn't installed\n"
        "  (smartpipe stays small by default).\n"
        "  Fix: pip install 'smartpipe[anthropic]'"
    )


def _an(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"
