# Models & providers

## What's a "model"?

A model is the AI that reads your instruction and produces the answer. sempipe
doesn't contain one — it sends your text to a model running either **locally** (on
your machine, via Ollama) or in the **cloud** (OpenAI, Anthropic, or any
compatible service). You choose which; sempipe just talks to it.

## Local vs. cloud

| | Local (Ollama) | Cloud |
|---|---|---|
| Cost | Free | Small charge per use |
| Privacy | Nothing leaves your machine | Text goes to the provider |
| Setup | Install Ollama, pull a model | Get an API key |
| Speed / quality | Depends on your hardware | Usually faster and stronger |

sempipe is **local-first**: with no configuration it looks for a running Ollama and
uses it. It will never silently call a paid API — if nothing is configured and no
Ollama is found, it prints a short setup screen and stops.

## Model strings

You name a model with a string. Two forms:

- **Explicit provider:** `ollama/qwen3:8b`, `openai/gpt-4o-mini`,
  `anthropic/claude-opus-4-8`, `mistral/mistral-large-latest`,
  `gemini/gemini-2.5-flash`, `openrouter/deepseek/deepseek-chat`
  (OpenRouter is explicit-only — its names are other vendors' names).
- **Bare name:** sempipe routes by shape — `claude-*` → Anthropic, `gpt-*` / `o*` /
  `text-embedding-*` → OpenAI, the Mistral family (`mistral-*`, `ministral-*`,
  `codestral-*`, `magistral-*`, `devstral-*`, `pixtral-*`, `open-mistral-*`,
  `open-mixtral-*`, `voxtral-*`) → Mistral, `gemini-*` → Gemini, anything else
  → Ollama. OpenRouter never routes bare. So `gpt-4o-mini` and
  `openai/gpt-4o-mini` mean the same thing.

Namespaced Ollama models keep working as bare names too: `hf.co/org/model` routes to
Ollama whole — including `hf.co/mistralai/...`, which is an Ollama name, not a
Mistral cloud model.

## Cloud credentials

Keys are read from the environment and **never stored** in sempipe's config:

```console
$ export OPENAI_API_KEY=sk-...
$ export ANTHROPIC_API_KEY=sk-ant-...
$ export MISTRAL_API_KEY=...            # console.mistral.ai
$ export GEMINI_API_KEY=...             # aistudio.google.com
$ export OPENROUTER_API_KEY=sk-or-...   # openrouter.ai/keys
```

Claude models also need an optional package: `pip install 'sempipe[anthropic]'`.
(sempipe tells you if it's missing.) Mistral needs nothing extra — chat,
`mistral-embed` embeddings, and `pixtral-*` vision all ride the built-in adapter.
Any *other* OpenAI-compatible endpoint — Groq, OpenRouter, a local llama.cpp
server — works by pointing sempipe at it:

```console
$ export SEMPIPE_OPENAI_BASE_URL=https://api.groq.com/openai
```

## Log in with ChatGPT (no API key)

If you have a ChatGPT Plus/Pro plan, you can use it directly:

```console
$ sempipe auth login              # opens your browser (or --headless for a code)
$ echo "hi" | sempipe map "translate to French" --model gpt-5.4
```

What to know:

- **Which models:** the ChatGPT wire serves the Codex-era family (`gpt-5.x`,
  `gpt-*-codex`). Older platform models like `gpt-4o-mini` still need an API key.
- **Precedence:** an exported `OPENAI_API_KEY` always wins over a login — a key is
  an explicit, billable choice. Unset it to use your plan.
- **No embeddings:** `embed`/`top_k` need an API key or a local model.
- **Where tokens live:** `~/.config/sempipe/auth.json`, permissions `0600`,
  refreshed automatically, removed with `sempipe auth logout`. (API keys are still
  never stored — this file holds only login tokens.)
- **Why no login for Anthropic/Mistral:** they don't offer one to third-party
  tools. OpenAI's login uses the same public OAuth client the Codex CLI and other
  open-source tools use, and sempipe identifies itself honestly (`originator:
  sempipe`).

## Forcing a path (D24)

There is deliberately no `--auth` knob and no `--api-key`/`--base-url` flags
(argv leaks into `ps` and shell history). The environment *is* the override:

```console
$ OPENAI_API_KEY=sk-... sempipe map …          # force the key path
$ env -u OPENAI_API_KEY sempipe map …          # force the ChatGPT login path
$ SEMPIPE_OPENAI_BASE_URL=https://… sempipe …  # point the wire elsewhere
```

## Setting a default

```console
$ sempipe config model ollama/qwen3:8b     # save a default
$ sempipe config show                       # see the effective settings + where each comes from
```

Override the default for a single command with `--model`:

```console
$ cat data.txt | sempipe map "summarize" --model claude-opus-4-8
```

## Precedence

When the same setting is specified more than one way, the most specific wins:

```
--model flag  >  SEMPIPE_MODEL env var  >  config file  >  Ollama autodetect
```

`sempipe config show` prints each value with its origin, so precedence is never a
mystery.

## Two models: chat and embedding

Most verbs use a **chat** model. `embed` and `top_k` (coming soon) use a separate
**embedding** model, configured independently:

```console
$ sempipe config embed-model nomic-embed-text
```

## See also

- [Quickstart](../quickstart.md) — get your first model running
- [Install](../install.md) — the optional extras, including `[anthropic]`
