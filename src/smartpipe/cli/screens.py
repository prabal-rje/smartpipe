"""Multi-line UX screens, verbatim from plan/ux.md (golden-pinned in tests).

Style contract (plan/ux.md): every screen contains its own fix — no error may
require opening a browser or reading docs to resolve.
"""

from __future__ import annotations

__all__ = [
    "BINARY_STDIN_UNPARSEABLE",
    "CHATGPT_LOGIN_EXPIRED",
    "DEMO_ALREADY_HERE",
    "DEMO_CONFIRM",
    "DEMO_DIR_IN_THE_WAY",
    "DEMO_READY",
    "EMBEDDINGS_NEED_KEY",
    "FIELD_REF_ON_PLAIN_INPUT",
    "NO_MODEL",
    "WELCOME",
    "cloud_model_missing",
    "demo_download_failed",
    "demo_verify_failed",
    "missing_anthropic_extra",
    "missing_api_key",
    "ollama_model_missing",
    "ollama_unreachable",
    "openai_needs_key_or_login",
    "schema_rejected",
    "stdin_document_failed",
    "update_available",
    "update_done",
    "update_failed",
    "update_plan",
    "update_tool_missing",
    "update_unknown_channel",
]

CHATGPT_LOGIN_EXPIRED = """\
error: the ChatGPT login has expired and couldn't be refreshed
  Fix: smartpipe auth login"""

EMBEDDINGS_NEED_KEY = """\
error: embeddings aren't available through ChatGPT login
  The ChatGPT plan wire serves chat models only.
  Fix: export OPENAI_API_KEY=sk-...
   or pick a local embedder: smartpipe use    (the embeddings stage)"""


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
  For files on disk, name the file: smartpipe map "Summarize" report.pdf"""


def stdin_document_failed(reason: str) -> str:
    return (
        f"error: stdin looks like a document, but it couldn't be read ({reason})\n"
        "  smartpipe reads ONE binary document per run from stdin.\n"
        '  Alternative: smartpipe map "…" report.pdf'
    )


FIELD_REF_ON_PLAIN_INPUT = """\
error: the prompt references a {field}, but the first input line isn't JSON
  {field} substitution needs JSON Lines input (one object per line).
  Either drop the braces, or feed JSONL — e.g.: cat tickets.jsonl | smartpipe filter ..."""


def tint(text: str, code: str) -> str:
    """Style stdout REPORT text (headers, marks): TTY-only, NO_COLOR wins.
    click.echo also strips ANSI when piped, so goldens stay plain (D42)."""
    import os
    import sys

    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def heading(text: str) -> str:
    return tint(text, "1;36")  # bold cyan — every section/column title, one voice


def stage_title(name: str, *, optional: bool = False, hint: str | None = None) -> str:
    """The wizard's stage headers, one voice (owner ruling 2026-07-12): the
    stage NAME in caps + the heading color, "(optional)" for the genuinely
    skippable role stages (dim), any hint dim after a dash. A piped stdout or
    NO_COLOR renders plain caps (``tint``'s gate), so goldens stay ANSI-free."""
    title = heading(name.upper())
    if optional:
        title += " " + tint("(optional)", "2")
    if hint is not None:
        title += tint(f" - {hint}", "2")
    return title


def good(text: str) -> str:
    return tint(text, "32")


def bad(text: str) -> str:
    return tint(text, "31")


def _c(text: str, code: str) -> str:
    """Bake ANSI into the welcome — click.echo strips it when piped, and the
    NO_COLOR gate lives at the echo site (D42)."""
    return f"\x1b[{code}m{text}\x1b[0m"


_WORDMARK = r"""
                       _         _
 ___ _ __   __ _  _ _ | |_  _ __ (_) _ __  ___
(_-<| '  \ / _` || '_||  _|| '_ \| || '_ \/ -_)
/__/|_|_|_|\__,_||_|   \__|| .__/|_|| .__/\___|
                           |_|      |_|"""

_VERBS: tuple[tuple[str, str], ...] = (
    ("map", "Transform each item with a prompt"),
    ("extend", "Add extracted fields to each record"),
    ("filter", "Keep items matching a semantic condition"),
    ("embed", "Convert items to vector embeddings"),
    ("top_k", "Rank items by similarity to a query"),
    ("reduce", "Synthesize many items into one"),
    ("join", "Match stdin against a second input, semantically"),
    ("cluster", "Group items by meaning; label each group"),
    ("diff", "What distinguishes two sets of items"),
    ("distinct", "Fold near-duplicate items (embeddings only)"),
    ("outliers", "Rank the items least like the rest (embeddings only)"),
    ("graph", "Corpus → knowledge graph (--fast is free)"),
)

_UTILITIES: tuple[tuple[str, str], ...] = (
    ("where", "Keep rows matching a deterministic predicate"),
    ("summarize", "Aggregate records: count/avg/percentiles by field"),
    ("sample", "Keep N random rows (seeded, reproducible)"),
    ("agree", "Score inter-rater agreement between two label files"),
    ("getschema", "Report the stream's fields, types, coverage"),
    ("sort", "Order records by a field (numbers, then strings)"),
    ("split", "Break oversized items into chunks"),
    ("write", "Route items to files (the egress door)"),
    ("readable", "Render records as blocks for human eyes"),
    ("chart", "Draw a bar chart of results (--save writes SVG/PNG)"),
    ("use", "Set up models (interactive, or: use gemini)"),
    ("config", "Show settings and toggle postures"),
    ("update", "Upgrade smartpipe with the tool that installed it"),
    ("demo", "Fetch the 26 MB practice corpus (free download)"),
)

_NAME_WIDTH = max(len(name) for name, _ in (*_VERBS, *_UTILITIES)) + 2


def _rows(entries: tuple[tuple[str, str], ...]) -> str:
    return "\n".join(f"  {_c(name.ljust(_NAME_WIDTH), '32')}{text}" for name, text in entries)


_GET_STARTED = (
    f"  smartpipe use{' ' * 40}{_c('# one-minute interactive setup', '2')}\n"
    f'  echo "hello" | smartpipe map "translate to Spanish"'
)

WELCOME = f"""\
{_c(_WORDMARK.lstrip(chr(10)), "36")}
{_c("smartpipe", "1")} — semantic pipes for your terminal
{_c("PDFs, images, audio, video, and text — verbs that understand their input.", "2")}

{_c("Verbs (call a model):", "1;36")}
{_rows(_VERBS)}

{_c("Utilities (free — no model calls):", "1;36")}
{_rows(_UTILITIES)}

{_c("Get started:", "1;36")}
{_GET_STARTED}

'smartpipe <command> --help' shows examples for each command.

{_c("docs      https://prabal-rje.github.io/smartpipe", "2")}
{_c("cookbook  https://prabal-rje.github.io/smartpipe/cookbook/", "2")}
{_c("issues    https://github.com/prabal-rje/smartpipe/issues", "2")}
"""

NO_MODEL = """\
error: no model configured, and no local Ollama found

  Cloud (paid):
    smartpipe auth login               (use your ChatGPT Plus/Pro plan)
    smartpipe use claude-opus-4-8      then: export ANTHROPIC_API_KEY=sk-ant-...
    smartpipe use gpt-5.4-mini         then: export OPENAI_API_KEY=sk-...

  Local (free, private):
    1. Install Ollama              https://ollama.com
    2. ollama pull qwen3:8b
    3. smartpipe use ollama/qwen3:8b

  Then rerun your command. 'smartpipe use' walks you through this interactively."""


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


# provider display name → `smartpipe auth login` id; openai's KEY door is
# openai-api (bare `auth login openai` means the ChatGPT OAuth flow)
_AUTH_LOGIN_IDS = {"openai": "openai-api"}


def missing_api_key(
    model: str,
    provider: str,
    env_var: str,
    key_shape: str,
    note: str = "add it to your shell profile to persist",
) -> str:
    login_id = _AUTH_LOGIN_IDS.get(provider.lower(), provider.lower())
    return (
        f"error: model '{model}' needs {_an(provider)} {provider} API key\n"
        f"  smartpipe found no {env_var} in the environment and no stored key.\n"
        f"  Fix: export {env_var}={key_shape}        ({note})\n"
        f"   or: smartpipe auth login {login_id}        (checks the key, stores it for every run)"
    )


def cloud_model_missing(model: str, host: str) -> str:
    """D18: a 404 for the model dooms every item identically — stop at the first."""
    return (
        f"error: the endpoint doesn't know the model '{model}'\n"
        f"  {host} answered 404 — every item would fail identically, "
        "so smartpipe stopped at the first.\n"
        "  Fix: check the name, or set one that exists: smartpipe use gpt-5.4-mini"
    )


def schema_rejected(host: str, detail: str) -> str:
    """D18: a schema the endpoint rejects dooms every item identically."""
    return (
        "error: the endpoint rejected the --schema\n"
        f"  {host} answered 400 (response_format): {detail}\n"
        "  Fix: simplify the schema — or drop --schema and validate downstream."
    )


def provider_down(provider: str, failures: int) -> str:
    """The circuit breaker screen (problems.md #6): consecutive wire-level
    failures mean the provider is down — stop paying a retry ladder per item."""
    return (
        f"error: {provider} looks down — {failures} consecutive transport failures\n"
        "  Every recent call died on the wire (timeouts, connection errors, 5xx),\n"
        "  so smartpipe stopped early instead of failing the rest one by one.\n"
        "  Work already done is safe — rerunning is cheap (cached answers are free).\n"
        "  Try again in a minute, or pick another model: --model …"
    )


def update_available(latest: str, current: str) -> str:
    """The end-of-run update notice (stderr, via diagnostics.note, TTY-only)."""
    return f"smartpipe {latest} is available (you have {current}) — run: smartpipe update"


def update_plan(channel: str, command: str, version: str) -> str:
    """What `smartpipe update` detected and will run — shown BEFORE consent."""
    return f"smartpipe {version} — installed with {channel}\n  will run: {command}"


def update_done(command: str) -> str:
    return f"done — {command} finished cleanly; check: smartpipe --version"


def update_failed(command: str, exit_code: int) -> str:
    return (
        f"error: the upgrade command failed (exit {exit_code})\n"
        f"  smartpipe ran: {command}\n"
        "  Check the tool's output above, then rerun it by hand."
    )


def update_tool_missing(tool: str, command: str) -> str:
    return (
        f"error: can't run '{tool}' — it isn't on PATH here\n"
        f"  smartpipe tried: {command}\n"
        f"  Run that yourself in a shell where {tool} works."
    )


def update_unknown_channel(version: str) -> str:
    """No recognizable installer fingerprint: guidance, never a guess (exit 0)."""
    return (
        f"smartpipe {version} — install channel not recognized\n"
        "  Upgrade with the tool that installed smartpipe:\n"
        "    brew upgrade smartpipe             (Homebrew)\n"
        "    uv tool upgrade smartpipe-cli      (uv)\n"
        "    pipx upgrade smartpipe-cli         (pipx)\n"
        "    pip install -U smartpipe-cli       (pip)"
    )


DEMO_CONFIRM = "~26 MB download to ./smartpipe-playground - continue?"
# confirm_on_stderr(default=True) renders the pinned prompt:
#   ~26 MB download to ./smartpipe-playground - continue? [Y/n]:

_DEMO_NEXT_STEPS = """\
Try these (free - no model calls):
  cd smartpipe-playground
  smartpipe 'reports/*.pdf'                  # reader mode: the PDFs become items
  smartpipe summarize 'count(), avg(total) by region' < data/tickets.jsonl
  smartpipe graph --fast 'reports/*.pdf' 'recordings/*.mp3' data/feedback.txt --save corpus.html

Every cookbook recipe runs on this corpus: https://prabal-rje.github.io/smartpipe/cookbook/"""

DEMO_READY = f"""\
playground ready: ./smartpipe-playground - invoices, reports, photos, recordings, sessions, JSONL

{_DEMO_NEXT_STEPS}"""

DEMO_ALREADY_HERE = f"""\
playground already here: ./smartpipe-playground - nothing downloaded

{_DEMO_NEXT_STEPS}"""

DEMO_DIR_IN_THE_WAY = """\
error: ./smartpipe-playground already exists and doesn't look like the playground
  smartpipe won't overwrite files it didn't put there.
  Fix: move or remove it, or run smartpipe demo from another directory."""


def demo_download_failed(url: str, reason: str) -> str:
    return (
        f"error: the playground download failed ({reason})\n"
        f"  URL: {url}\n"
        "  Check your connection and retry. Fetching by hand works too:\n"
        f"  curl -L {url} | tar xz"
    )


def demo_verify_failed(expected: str, actual: str) -> str:
    return (
        "error: the downloaded playground didn't match its published checksum\n"
        f"  expected sha256 {expected}\n"
        f"  got      sha256 {actual}\n"
        "  Nothing was unpacked. A truncated download or a proxy can cause this - retry,\n"
        "  and if it persists, report it: https://github.com/prabal-rje/smartpipe/issues"
    )


def missing_anthropic_extra(model: str) -> str:
    return (
        f"error: the SDK for '{model}' is unavailable\n"
        "  Claude models talk through the official anthropic SDK, which ships\n"
        "  with smartpipe — a broken environment is the only way here.\n"
        "  Fix: reinstall smartpipe"
    )


def _an(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"
