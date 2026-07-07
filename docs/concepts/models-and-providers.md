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
| Privacy | Nothing leaves your machine | Text goes to the provider |
| Setup | Install Ollama, pull a model | Get an API key |
| Speed / quality | Depends on your hardware | Usually faster and stronger |

smartpipe is **local-first**: with no configuration it looks for a running Ollama and
uses it. It will never silently call a paid API - if nothing is configured and no
Ollama is found, it prints a short setup screen and stops.

## Model strings

You name a model with a string. Two forms:

- **Explicit provider:** `ollama/qwen3:8b`, `openai/gpt-5.4-mini`,
  `anthropic/claude-opus-4-8`, `mistral/mistral-large-latest`,
  `gemini/gemini-2.5-flash`, `openrouter/deepseek/deepseek-chat`
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

```console
$ export OPENAI_API_KEY=sk-...
$ export ANTHROPIC_API_KEY=sk-ant-...
$ export MISTRAL_API_KEY=...            # console.mistral.ai
$ export GEMINI_API_KEY=...             # aistudio.google.com
$ export OPENROUTER_API_KEY=sk-or-...   # openrouter.ai/keys
```

Claude models also need an optional package: `pip install 'smartpipe[anthropic]'`.
(smartpipe tells you if it's missing.) Mistral needs nothing extra - chat,
`mistral-embed` embeddings, and `pixtral-*` vision all ride the built-in adapter.
Any *other* OpenAI-compatible endpoint - Groq, OpenRouter, a local llama.cpp
server - works by pointing smartpipe at it:

```console
$ export SMARTPIPE_OPENAI_BASE_URL=https://api.groq.com/openai
```

## Log in with ChatGPT (no API key)

If you have a ChatGPT Plus/Pro plan, you can use it directly:

```console
$ smartpipe auth login              # opens your browser (or --headless for a code)
$ echo "hi" \
    | smartpipe map "translate to French" --model gpt-5.4
```

What to know:

- **Which models:** the ChatGPT wire serves the Codex-era family (`gpt-5.x`,
  `gpt-*-codex`). Older platform models like `gpt-5.4-mini` still need an API key.
- **Precedence:** an exported `OPENAI_API_KEY` always wins over a login - a key is
  an explicit, billable choice. Unset it to use your plan.
- **No embeddings:** `embed`/`top_k` need an API key or a local model.
- **Where tokens live:** `~/.config/smartpipe/auth.json`, permissions `0600`,
  refreshed automatically, removed with `smartpipe auth logout`. (API keys are still
  never stored - this file holds only login tokens.)
- **Why no login for Anthropic/Mistral:** they don't offer one to third-party
  tools. OpenAI's login uses the same public OAuth client the Codex CLI and other
  open-source tools use, and smartpipe identifies itself honestly (`originator:
  smartpipe`).

## Forcing a path (D24)

There is deliberately no `--auth` knob and no `--api-key`/`--base-url` flags
(argv leaks into `ps` and shell history). The environment *is* the override:

```console
$ OPENAI_API_KEY=sk-... smartpipe map …          # force the key path
$ env -u OPENAI_API_KEY smartpipe map …          # force the ChatGPT login path
$ SMARTPIPE_OPENAI_BASE_URL=https://… smartpipe …  # point the wire elsewhere
```

## Gemini rides its native wire (and watches video)

Gemini chat uses Google's native endpoint, the only wired endpoint that takes
**video input**: `map "what happens?" --in demo.mp4 --model gemini-2.5-flash`
sends the actual video (visuals and soundtrack heard together, no conversion).
On every other model the video ladder converts to frames + audio automatically.
Structured output translates to Gemini's response-schema dialect; embeddings
stay on the compat wire. `SMARTPIPE_GEMINI_BASE_URL` still points both.

## Context windows: probed, not guessed

smartpipe keeps a conservative window table per provider, and when an input
actually exceeds it, asks the provider for the real number (one cached
metadata call - Ollama, Mistral, Gemini, and OpenRouter publish it; OpenAI and
Anthropic don't). A live example: the table floors Gemini at 128k, but the
probe discovers `gemini-2.5-flash` really holds 1M and widens the budget 8x.
`SMARTPIPE_CONTEXT_TOKENS=32000` overrides everything. And if every estimate is
wrong anyway, `reduce` self-corrects: a chunk the wire rejects as too big is
split in half and retried (you'll see one note: `splitting further and
retrying`).

## Profiles: named setups you can switch between (D30)

A profile bundles the existing config keys (model, embed-model, concurrency,
output) under a name. Three ship built in:

| Profile | Chat | Embeddings | For |
|---|---|---|---|
| `openai` | gpt-5.4-mini | text-embedding-3-small | the fast cloud default |
| `gemini` | gemini-2.5-flash | gemini/gemini-embedding-001 | the most multimodal wire |
| `local` | ollama/gemma-4-e2b | embeddinggemma | multimodal, nothing leaves the machine |

```console
$ smartpipe config profile              # list (the active one marked)
$ smartpipe config profile local        # switch
$ SMARTPIPE_PROFILE=gemini smartpipe map …  # one-off, no file change (D24: env is the override)
```

The cloud presets are **multimodal by default**: they set
`allow-captions = true`, so images and audio convert to text through the
profile's own model when a run needs it (fractions of a cent each, every
conversion disclosed per row). Picking the profile is the consent; the wizard
says so out loud. Bare no-profile setups keep the conservative `--allow-captions`
opt-in.

Create your own as a `[profiles.NAME]` table in the config file (keys: model,
embed-model, concurrency, output, allow-captions). Direct keys beat the active
profile (a direct set is the most recent intent); flags and env vars beat
both. Profiles never hold API keys.

## Setting a default

```console
$ smartpipe config model ollama/qwen3:8b     # save a default
$ smartpipe config show                       # see the effective settings + where each comes from
```

Override the default for a single command with `--model`:

```console
$ cat data.txt \
    | smartpipe map "summarize" --model claude-opus-4-8
```

## Precedence

When the same setting is specified more than one way, the most specific wins:

```
--model flag  >  SMARTPIPE_MODEL env var  >  config file  >  Ollama autodetect
```

`smartpipe config show` prints each value with its origin, so precedence is never a
mystery.

## Two models: chat and embedding

Most verbs use a **chat** model. `embed` and `top_k` (coming soon) use a separate
**embedding** model, configured independently:

```console
$ smartpipe config embed-model nomic-embed-text
```

## See also

- [Quickstart](../quickstart.md) - get your first model running
- [Install](../install.md) - the optional extras, including `[anthropic]`


## The stt-model role

`smartpipe config stt-model openai/whisper-1` names a dedicated remote
transcriber. When set, it runs FIRST in the audio ladder (a configured
transcriber signals wanting verbatim text - LLM hearing paraphrases),
falling back to the LLM rung and local whisper on failure. It is a paid
cloud conversion, so the `allow-captions` consent gates it like every other
one. Unset, smartpipe picks the sensible strategy automatically:

| Your situation | Transcription |
|---|---|
| OpenAI **API key** | `whisper-1` via the API (it supports it) |
| OpenAI **ChatGPT login** only | local whisper (the login wire has no STT) |
| Gemini | the model hears audio natively |
| Ollama | local whisper (no STT endpoint) |

`SMARTPIPE_STT_MODEL` / `stt-model` override the matrix per run or per
account. Only the openai wire exists today; the key accepts
`provider/model` so more can land behind the same seam.


## The usage ledger

`smartpipe usage` shows what the meter observed over the past hour, day, week,
month, and lifetime - runs, tokens in/out, media, audio time, paid
conversions. `smartpipe usage reset` zeroes it (printing the previous lifetime
so the number isn't lost) and remembers the reset time. Only model-touching
runs count; the ledger lives in `~/.local/state/smartpipe/` and never leaves
your machine.
