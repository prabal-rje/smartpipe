# Models & providers

## What's a "model"?

A model is the AI that reads your instruction and produces the answer. smartpipe
doesn't contain one - it sends your text to a model running either **locally** (on
your machine, via Ollama) or in the **cloud** (OpenAI, Anthropic, or any
compatible service). You choose which; smartpipe just talks to it.

## Local vs. cloud

| | Local (Ollama) | Cloud |
|---|---|---|
| Cost | Free | Small charge per use |
| Privacy | Nothing leaves your machine when Ollama is local | Text and supported media go to the provider |
| Setup | Install Ollama, pull a model | Get an API key |
| Speed / quality | Depends on your hardware | Usually faster and stronger |

With no configured chat model, smartpipe tries local Ollama first. If it finds a
non-embedding model, it uses that model and prints the exact `ollama/...` name.

If no model is configured and no usable Ollama model is found, smartpipe stops with
a setup screen. It does not silently fall through to a cloud provider just because
a key or login exists.

Once you choose a cloud model, profile, or ChatGPT login path, the relevant item data
for that run goes to that provider. That is an explicit provider choice, not a hidden
fallback.

## Model strings

You name a model with a string. Two forms:

- **Explicit provider:** `ollama/qwen3:8b`, `openai/gpt-5.4-mini`,
  `anthropic/claude-opus-4-8`, `mistral/mistral-large-latest`,
  `gemini/gemini-3.1-flash-lite`, `openrouter/deepseek/deepseek-chat`
  (OpenRouter is explicit-only - its names are other vendors' names).
- **Bare name:** smartpipe routes by shape - `claude-*` → Anthropic, `gpt-*` / `o*` /
  `text-embedding-*` → OpenAI, the Mistral family (`mistral-*`, `ministral-*`,
  `codestral-*`, `magistral-*`, `devstral-*`, `pixtral-*`, `open-mistral-*`,
  `open-mixtral-*`, `voxtral-*`) → Mistral, `gemini-*` → Gemini, anything else
  → Ollama. OpenRouter never routes bare. So `gpt-5.4-mini` and
  `openai/gpt-5.4-mini` mean the same thing.

Namespaced Ollama models keep working as bare names too: `hf.co/org/model` routes to
Ollama whole - including `hf.co/mistralai/...`, which is an Ollama name, not a
Mistral cloud model.

## Cloud credentials

Keys are read from the environment and **never stored** in smartpipe's config:

```bash
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
export MISTRAL_API_KEY=...            # console.mistral.ai
export GEMINI_API_KEY=...             # aistudio.google.com
export OPENROUTER_API_KEY=sk-or-...   # openrouter.ai/keys
```

The `anthropic` SDK ships with smartpipe - Claude models work out of the box.
(smartpipe tells you if it's missing.) Mistral needs nothing extra - chat,
`mistral-embed` embeddings, and `pixtral-*` vision all ride the built-in adapter.
Any *other* OpenAI-compatible endpoint - Groq, OpenRouter, a local `llama.cpp`
server - works by pointing smartpipe at it:

```bash
export SMARTPIPE_OPENAI_BASE_URL=https://api.groq.com/openai
```

## Log in with ChatGPT (no API key)

If you have a ChatGPT Plus/Pro plan, you can use it directly:

```bash
smartpipe auth login              # opens your browser (or --headless for a code)
echo "hi" \
| smartpipe map "translate to French" --model gpt-5.4
```

What to know:

- **Which models:** the ChatGPT login serves the Codex-era family (`gpt-5.x`,
  `gpt-*-codex`). The low-cost platform tiers - `gpt-5.4-mini` and `gpt-5.4-nano` -
  are rejected on this wire (`"not supported ... with a ChatGPT account"`) and
  need an API key. For cheap, high-volume runs, set `OPENAI_API_KEY` and use
  `gpt-5.4-nano`, the low-cost OpenAI tier.
- **Precedence:** an exported `OPENAI_API_KEY` always wins over a login - a key is
  an explicit, billable choice. Unset it to use your plan.
- **No embeddings:** `embed`/`top_k` need an API key or a local model.
- **Where tokens live:** `~/.config/smartpipe/auth.json`, permissions `0600`,
  refreshed automatically, removed with `smartpipe auth logout`. (API keys are still
  never stored - this file holds only login tokens.)
- **Why no login for Anthropic/Mistral:** they don't offer one to third-party
  tools. OpenAI's login uses the same public OAuth client the Codex CLI and other
  open-source tools use, and smartpipe identifies itself (`originator:
  smartpipe`).

## Forcing a path (D24)

There is deliberately no `--auth` knob and no `--api-key`/`--base-url` flags
(argv leaks into `ps` and shell history). The environment *is* the override:

```bash
OPENAI_API_KEY=sk-... smartpipe map …          # force the key path
env -u OPENAI_API_KEY smartpipe map …          # force the ChatGPT login path
SMARTPIPE_OPENAI_BASE_URL=https://… smartpipe …  # point the wire elsewhere
```

## Gemini rides its native wire (and watches video)

Gemini chat uses Google's native endpoint, the only wired endpoint that takes
**video input**: `map "what happens?" demo.mp4 --model gemini-3.1-flash-lite`
sends the actual video (visuals and soundtrack heard together, no conversion).
On every other model the video ladder converts to frames + audio automatically.
Structured output translates to Gemini's response-schema dialect; embeddings
stay on the compat wire. `SMARTPIPE_GEMINI_BASE_URL` still points both.

## Context windows: probed, not guessed

smartpipe keeps a conservative context-window table per provider. When an input
exceeds that table, it asks providers that publish metadata for the real number:
Ollama, Mistral, Gemini, and OpenRouter. OpenAI and Anthropic do not publish it.

Example: the table floors Gemini at 128k, but the probe can discover that
`gemini-2.5-flash` holds 1M and widen the budget.

`SMARTPIPE_CONTEXT_TOKENS=32000` overrides everything. If an estimate is still wrong,
`reduce` self-corrects: a rejected chunk is split in half and retried.

## Profiles: named setups you can switch between (D30)

A profile bundles the existing config keys (model, embed-model, concurrency,
output) under a name. Three ship built in:

| Profile | Chat | Embeddings | For |
|---|---|---|---|
| `openai` | gpt-5.4-mini | text-embedding-3-small | cloud preset |
| `gemini` | gemini-3.1-flash-lite | gemini/gemini-embedding-001 | cloud multimodal preset |
| `local` | ollama/gemma-4-e2b | embeddinggemma | local preset when Ollama runs on your machine |

```bash
smartpipe config profile              # list (the active one marked)
smartpipe config profile local        # switch
SMARTPIPE_PROFILE=gemini smartpipe map …  # one-off, no file change (D24: env is the override)
```

The cloud presets are **multimodal by default**: they set
`allow-captions = true`, so images and audio convert to text through the
profile's own model when a run needs it (fractions of a cent each, every
conversion disclosed per row). Picking the profile is the consent; the wizard
states this. Bare no-profile setups keep the conservative `--allow-captions`
opt-in.

Create your own as a `[profiles.NAME]` table in the config file (keys: model,
embed-model, concurrency, output, allow-captions). Direct keys beat the active
profile (a direct set is the most recent intent); flags and env vars beat
both. Profiles never hold API keys.

## Setting a default

```bash
smartpipe config model ollama/qwen3:8b     # save a default
smartpipe config show                       # see the effective settings + where each comes from
```

Override the default for a single command with `--model`:

```bash
cat data.txt \
| smartpipe map "summarize" --model claude-opus-4-8
```

## Precedence

When the same setting is specified more than one way, the most specific wins:

```
--model flag  >  SMARTPIPE_MODEL env var  >  config file  >  Ollama autodetect
```

`smartpipe config show` prints each value with its origin, so you can see which value takes effect.

## Two models: chat and embedding

Most verbs use a **chat** model. `embed` and `top_k` use a separate
**embedding** model, configured independently:

```bash
smartpipe config embed-model nomic-embed-text
```

## See also

- [Quickstart](../quickstart.md) - get your first model running
- [Install](../install.md) - package contents and environment notes


## The stt-model role

`smartpipe config stt-model openai/whisper-1` names a dedicated remote
transcriber. When set, it runs FIRST in the audio ladder (a configured
transcriber signals wanting verbatim text - LLM hearing paraphrases),
falling back to the LLM rung and local `whisper` on failure. It is a paid
cloud conversion, so the `allow-captions` consent gates it like every other
one. Unset, smartpipe picks the sensible strategy automatically:

| Your situation | Transcription |
|---|---|
| OpenAI **API key** | `whisper-1` via the API (it supports it) |
| OpenAI **ChatGPT login** only | local whisper (the login wire has no speech-to-text (STT)) |
| Gemini | the model hears audio natively |
| Ollama | local whisper (no STT endpoint) |

`SMARTPIPE_STT_MODEL` / `stt-model` override the matrix per run or per
account. Only the `openai` wire exists today; the key accepts
`provider/model` so more can land behind the same seam.


## The usage ledger

`smartpipe usage` shows what the meter observed over the past hour, day, week,
month, and lifetime - runs, tokens in/out, media, audio time, paid
conversions. `smartpipe usage reset` zeroes it (printing the previous lifetime
so the number isn't lost) and remembers the reset time. Only model-touching
runs count; the ledger lives in `~/.local/state/smartpipe/` and never leaves
your machine.
