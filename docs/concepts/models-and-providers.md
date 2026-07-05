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

- **Explicit provider:** `ollama/qwen3:8b`, `openai/gpt-4o-mini`, `anthropic/claude-opus-4-8`.
- **Bare name:** sempipe routes by shape — `claude-*` → Anthropic, `gpt-*` / `o*` /
  `text-embedding-*` → OpenAI, anything else → Ollama. So `gpt-4o-mini` and
  `openai/gpt-4o-mini` mean the same thing.

Namespaced Ollama models keep working as bare names too: `hf.co/org/model` routes to
Ollama whole.

## Cloud credentials

Keys are read from the environment and **never stored** in sempipe's config:

```console
$ export OPENAI_API_KEY=sk-...
$ export ANTHROPIC_API_KEY=sk-ant-...
```

Claude models also need an optional package: `pip install 'sempipe[anthropic]'`.
(sempipe tells you if it's missing.) Any OpenAI-compatible endpoint — Groq,
Mistral, OpenRouter, a local llama.cpp server — works by pointing sempipe at it:

```console
$ export SEMPIPE_OPENAI_BASE_URL=https://api.groq.com/openai
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
