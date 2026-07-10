# CLI reference

The complete surface, in one page. `smartpipe PATH…` (no verb) is reader
mode: it emits the files' items as JSONL records, cut per `--as` - zero model
calls unless an `ocr-model` is configured (then PDFs/images parse through it,
disclosed per row; `--ocr-model` overrides, `--max-calls` caps).
As of 1.0 this is a contract governed by
[SemVer](https://semver.org) - flags, formats, and exit codes won't change
within a major version.

## Synopsis

```
smartpipe <verb> [PROMPT] [OPTIONS]
```

Input comes from `stdin` (each line an item - or ONE redirected binary document),
from named FILES after the prompt (each file an item by default; `--in GLOB`
remains a hidden compatibility alias), or both (named files
first, then the piped lines). Results go to `stdout`; progress and warnings go
to `stderr`.

## Verbs

| Verb | Purpose | Page |
|---|---|---|
| [`map`](../verbs/map.md) | transform each item with a prompt | one item in, one out |
| `write` | route items to files (the egress door; `TEMPLATE` with `{name}` `{stem}` `{ext}` `{path}` `{index}` from `__source` provenance + record fields for fan-out; `--field`, `--keep-meta`, `--as file\|lines`) | free |
| [`readable`](../verbs/readable.md) | render records as blocks for eyes (`--full`, `--bare`); media previews at a color terminal | free |
| [`filter`](../verbs/filter.md) | keep items matching a condition | semantic grep |
| [`embed`](../verbs/embed.md) | items → vectors (JSONL) | plumbing for `top_k` |
| [`top_k`](../verbs/top-k.md) | rank by similarity to a query | `sort \| head`, by meaning |
| [`reduce`](../verbs/reduce.md) | synthesize many items into one | recursive, automatic |
| [`join`](../verbs/join.md) | match stdin against a second input | embed-block-judge |
| [`extend`](../verbs/extend.md) | add extracted fields to each record (map that merges) | 1 call per item |
| [`distinct`](../verbs/distinct.md) | fold near-duplicate items, first occurrence wins | embeddings only |
| [`outliers`](../verbs/outliers.md) | rank the N items least like the rest | embeddings only |
| [`cluster`](../verbs/cluster.md) | group items by meaning; label each group | embeddings + 1 call per cluster |
| [`diff`](../verbs/diff.md) | themes that distinguish stdin from --right FILE | embeddings + labels |
| [`graph`](../verbs/graph.md) | entity/relationship graph with cited edges | `--fast` = free local NER |
| [`where`](../verbs/where.md) | keep rows matching a deterministic predicate | free - no model calls |
| [`summarize`](../verbs/summarize.md) | count/avg/percentiles by field | free - no model calls |
| [`sample`](../verbs/sample.md) | keep N random rows, seeded + reproducible | free - no model calls |
| [`getschema`](../verbs/getschema.md) | report the stream's fields, types, coverage | free - no model calls |
| [`sort`](../verbs/sort.md) | order records by a field | free - no model calls |
| [`split`](../verbs/split.md) | break oversized items into chunk items | free - no model calls |
| `chart` | bar-chart a field's values; `--save` writes SVG or PNG | free - no model calls |
| [`config`](#config) | view and set defaults | interactive setup |
| [`run`](#run) | execute a saved `.sem` stage file | [format](sem-files.md) |
| [`doctor`](#doctor) | check the whole setup, spend nothing (`--probe` adds the paid modality matrix) | exit 0 = ready |
| `schema` | braces/DSL compile FREE (`--check FILE`, `--example`, stdin REPL); bare at a terminal opens the [workshop](#schema-workshop); plain English drafts with a model (one call, validated) | [ladder](../concepts/structured-output.md#the-ladder-top-to-bottom) |

## Common options

These apply to the model-using verbs (`map`, `filter`, `top_k`, `reduce`; `embed` and
`top_k` use `--embed-model` instead of `--model`):

| Option | Meaning |
|---|---|
| `--model TEXT` | Model for this run - overrides the config and `SMARTPIPE_MODEL`. |
| `--embed-model TEXT` | Embedding model (`embed`, `top_k`). |
| `--concurrency N` | Max parallel model calls (default 4). |
| `FILES…` (positional) | Read each named file/glob as items (quote globs). `--in GLOB` is a hidden compatibility alias. |
| `--as {file,lines,jsonl,csv}` | Cut granularity: whole crates, text rows, strict records, or header-named csv rows ([the item](../concepts/the-item.md), [csv rows](../concepts/feeding-smartpipe.md#csv-rows)). Auto: `.jsonl` paths cut into records, `.csv`/`.tsv` into csv rows (`.tsv` on tabs); other paths are one item; stdin sniffs per line. |
| `--from-files` | Treat each `stdin` line as a filename. |
| `--strict-rows` | A mixed record/text stream (or a field-less row in `where`/`summarize`) is an error, not a note. `SMARTPIPE_STRICT_ROWS` is the env form. |
| `--bare` | Strip `__` metadata from record output (`map`, `extend`, `join`, reader mode). |
| `--full` | Terminal preview: no truncation (`map`, `extend`, `join`, `readable`). |
| `--fallback-model TEXT` | Chat model to switch to if the primary looks down (circuit breaker; `map`, `extend`, `filter`, `join`). |
| `--fields A,B` | Select + order columns of structured output (`map`, `embed`, `top_k`, `reduce` - never `filter`). |
| `--allow-captions` | Let a CLOUD model convert images/audio/video to text for embedding/text verbs (paid; local models convert free; the `openai`/`gemini` profiles set this by default). |
| `@file` / `--prompt-file FILE` | Read the prompt from a file (`map`, `extend`, `filter`, `reduce`, `join`). Missing file = loud exit 64; `@@` escapes a literal leading `@`. |
| `--max-calls N` | Hard ceiling on model calls (cost cap). Per-item verbs stop intake and drain; whole-set `top_k`/`reduce` treat exhaustion as fatal (nothing usable from a partial collection). A capped run never exits 0. |

## Verb-specific options

| Verb | Options |
|---|---|
| `map` | `--schema FILE`, `--schema-from DSL`, `--tally FIELD`, `--explode FIELD`, `--output {auto,text,json,csv,tsv}`, `--keep-invalid` (failed validations become `{"__invalid": …}` rows), `--dry-run` (print the composed first request, spend nothing) |
| `filter` | `--not` (invert, like `grep -v`) |
| `top_k` | `K` (positional), `--near TEXT` (required), `--threshold FLOAT`, `--stream` (live leaderboard) |
| `reduce` | `--schema FILE`, `--schema-from DSL`, `--group-by FIELD`, `--verbose`, `--window N [--every M]` (stream mode) |
| `join` | `--right FILE` (required), `--on 'left.F == right.F'` (repeatable; alone = free key join, with a prompt = blocking), `--k N` (default 5), `--threshold FLOAT`, `--kind inner|leftouter|anti`, `--unmatched FILE`, `--embed-model` |
| `extend` | map's flags (braces/--schema/--schema-from/--tally/--explode/--fields/--keep-invalid/--dry-run) |
| `map`/`extend` video | `--frame-every SECONDS` (density guarantee), `--max-frames N` (budget; smaller wins) |
| `distinct` | `--show-groups`, `--threshold F` (cosine, default 0.90), `--exact` (hash rung only - free), `--embed-model` |
| `outliers` | `N` (default 5), `--embed-model` |
| `cluster` | `--k N`, `--top N`, `--explode members`, `--model` (labels), `--embed-model` |
| `diff` | `--right FILE` (required), `--top N`, `--all`, `--model`, `--embed-model` |
| `graph` | `--fast` (free), `--entities "a, b"`, `--relations "pays, owns"`, `--name-top N` (hybrid), `--window {sentence,chunk,document}`, `--min-weight N`, `--save PATH` (`.graphml`/`.dot`/`.mmd`/`.csv`/`.html` or `directory/` = Obsidian vault), `--top N` (display cap), `--ocr-model` |
| `where` | `'PREDICATE'` (has, contains, matches /re/, == != > >= < <=, and/or/not) |
| `summarize` | `'AGG[, AGG…] [by FIELD,…]'` (count/sum/avg/min/max/p50-p99/dcount) |
| `sample` | `N`, `--seed K` (default 0 - reproducible by default) |
| `getschema` | `--all` (scan past the first 10,000 rows) |
| *(custom)* | [your own verbs](custom-verbs.md): `~/.config/smartpipe/verbs/*.sem` or entry points |
| `usage` | model usage over hour/day/week/month/lifetime; `usage reset` remembers when |
| `cache` | `stats` · `clear` (auto-swept: 30-day TTL + 500 MB LRU cap - `cache-days`, `cache-max-mb`) |
| `sort` | `--by FIELD` (required), `--desc` |
| `split` | `--by UNIT[:N]` (tokens, pages, minutes, seconds), `--media` (embedded images), `--max-tokens N` (= `--by tokens:N`) |
| `chart` | `FIELD` (or whole lines), `--facet f1,f2,…`, `--by-time FIELD:BUCKET`, `--top N`, `--save FILE.svg` / `FILE.png`, `--title` |

## `config`

```bash
smartpipe config                     # interactive setup: text model, embeddings, OCR
smartpipe config show                # effective settings + where each comes from
smartpipe config model MODEL         # set the default chat model
smartpipe config embed-model MODEL   # set the default embedding model
smartpipe config media-previews off  # terminal media previews (thumbnails,
                                     # waveforms, play links) - default on
```

Bare `smartpipe config` runs three stages in order - the text model, the
embedding model (the auto-pair suggestion preselected), then an optional OCR
model (one keypress skips it). Every provider appears with a connected badge;
picking an unconnected one drops into the `auth login` connect flow inline
and continues. Every stage has a `back` row (typing `back` or `b` works too),
re-runs preselect your current choices and restamp only changes, and Ctrl-C
anywhere leaves the config untouched. At the end it offers to
verify what the chosen models can actually do (~5 tiny requests, consent
first; a failed text control reports a setup fault and concludes nothing).
Menu rows carry capability chips (`text · image · audio`) sourced probed >
registry (models.dev, day-cached) > declared; a `model-capabilities =
["image"]` config key declares chips for self-hosted models the registry
can't know. Chips are display only - runtime stays attempt-based.

API keys are read from the environment first, then from the `auth login`
store - never from the config file.

Edits via `smartpipe config` rewrite the file atomically; unknown keys are
preserved, comments are not.

## `auth`

```bash
smartpipe auth login             # pick a provider from the list (all of them)
smartpipe auth login mistral     # store a Mistral API key (masked prompt, live check)
smartpipe auth login openai      # log in with ChatGPT (browser) - back-compat
smartpipe auth login openai-api  # store an OpenAI API key
smartpipe auth login --headless  # ChatGPT device-code flow for remote machines
smartpipe auth list              # provider · type · MASKED key · live source
smartpipe auth status            # ChatGPT login state
smartpipe auth logout [PROVIDER] # remove one credential (picker when omitted)
```

OpenAI appears twice in the list because its wires differ: the ChatGPT login
serves Codex-family chat only (no embeddings), the API key serves everything.
Keys store at `~/.local/share/smartpipe/auth.json` (owner-only, `0600`); the
ChatGPT tokens keep living at `~/.config/smartpipe/auth.json`. A key entry is
validated with one catalog request first - on failure you choose retry, store
anyway (the provider may be down), or skip. An exported environment variable
always wins over anything stored.

## `cite`

```bash
smartpipe cite                       # print a BibTeX entry for citing smartpipe
```

## `doctor`

```bash
smartpipe doctor        # config · Ollama · models · keys · login · extras · completions
```

One line per check with its fix inline; exit 0 all-green, 1 if anything needs
attention. Never makes a paid model call; key lines report presence, never values.

## `run`

```bash
smartpipe run extract.sem < cards.txt        # execute a saved stage
smartpipe run extract.sem --model ollama/qwen3:8b   # flags override the file
```

A `.sem` file pins one verb invocation in TOML; with a
`#!/usr/bin/env -S smartpipe run` shebang it runs directly (`./extract.sem`).
Unknown keys in the file are errors (scripts run unattended). Full format:
[.sem stage files](sem-files.md).

## `schema` workshop

Bare `smartpipe schema` at a terminal opens a small interactive workshop for
building a schema. Every command is free - zero model calls. The header shows
the live draft and repaints after each command (pinned at the top on a capable
terminal, reprinted in the plain fallback):

```
schema workshop — free, no model calls
{vendor string: legal name, total number}
✓ compiles · 2 fields
/add NAME TYPE [: guidance] · /drop NAME · /test FILE · /example · /save [PATH] · /quit
```

| Command | Effect |
|---|---|
| `/add NAME TYPE [: guidance]` | Add a field. Types are the braces vocabulary: `string`, `number`, `integer`, `boolean`, `enum(a, b)`, `string[]`, `number[]`, `{a, b}[]` (an object list), a trailing `?` for nullable. |
| `/drop NAME` | Remove a field. |
| `/test FILE` | Validate the file's JSONL rows: a pass/fail tally, then a coverage bar per field (presence %, type misses). |
| `/example` | One deterministic instance that validates - the same machinery as `--example`. |
| `/save [PATH]` | Write the compiled JSON Schema (default `schema.json`), then print the two paste-ready lines: the braces string for inline use, and `--schema PATH`. |
| `/quit` (or Ctrl-D) | Leave; the paste-ready lines print on the way out. |

Pasting a whole braces string (for example `{vendor string, total number}`)
replaces the draft. Every edit runs through the real braces compiler, so a bad
type shows the compiler's own error and leaves the draft unchanged. Bare
`schema` with **piped** stdin keeps its old behavior: one expression per line,
one compiled schema per line.

## Output formats

`--output` (or `SMARTPIPE_OUTPUT`): `auto` (default), `text`, `json`, `csv`, `tsv`.
See [Output formats](../concepts/output-formats.md). `auto` shows a readable view at a
terminal and JSONL when piped; `csv`/`tsv` need structured (named-field) output.
`--fields a,b` projects structured output to just those columns, in that order,
identically in every format.

## Shell completion

Tab completion for bash, zsh, and fish - including live model-name suggestions on
`--model`/`--embed-model` and `config model`. One-liners per shell in
[Installing smartpipe → Tab completion](../install.md#tab-completion).

## Environment variables

| Variable | Effect |
|---|---|
| `SMARTPIPE_MODEL` | Default chat model. |
| `SMARTPIPE_EMBED_MODEL` | Default embedding model. |
| `SMARTPIPE_OUTPUT` | Default output format. |
| `SMARTPIPE_MAX_CALLS` | Default call ceiling (see `--max-calls`). |
| `SMARTPIPE_OPENAI_BASE_URL` | Point the OpenAI-compatible adapter at any endpoint. |
| `SMARTPIPE_MISTRAL_BASE_URL` / `SMARTPIPE_GEMINI_BASE_URL` / `SMARTPIPE_OPENROUTER_BASE_URL` | Point a provider's wire elsewhere (proxies, gateways). |
| `SMARTPIPE_PROFILE` | One-off profile pick for this invocation ([profiles](../concepts/models-and-providers.md)). |
| `SMARTPIPE_CONTEXT_TOKENS` | Assert your model's context window (beats the table and the probe; the fix for OpenAI/Anthropic deployments the table underestimates). |
| `SMARTPIPE_WHISPER_MODEL` | Local transcription size: `tiny` (default), `base`, `small`, `medium`, `large-v3`. |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `MISTRAL_API_KEY` / `GEMINI_API_KEY` / `OPENROUTER_API_KEY` / `JINA_API_KEY` | Cloud credentials - the environment always wins over a key stored by `auth login`. |
| `OLLAMA_HOST` | Ollama endpoint (default `http://localhost:11434`). |
| `NO_COLOR` | Disable color. |

## Exit codes

Chosen so a script can branch on *how* a run went, not just pass/fail:

| Code | Meaning |
|---|---|
| `0` | OK - everything succeeded (including zero matches for `filter`). |
| `1` | PARTIAL - some items were skipped; the rest succeeded. |
| `2` | SETUP - misconfiguration (no model, unreachable Ollama, missing key). |
| `3` | ALL_FAILED - every item failed. |
| `64` | USAGE - bad flags or input. |
| `70` | BUG - an internal error (please report it). |
| `130` | INTERRUPTED - Ctrl-C (before anything finished, or pressed twice). |
| `141` | SIGPIPE - downstream closed the pipe (normal in `\| head` pipelines); smartpipe prints nothing. |

### What Ctrl-C does

For per-item verbs (`map`, `filter`, `embed`), the **first Ctrl-C** stops new work and
lets in-flight work finish for up to 10 seconds. Finished results are emitted in
order.

The command then prints `done: interrupted - N processed · M skipped` on `stderr` and
exits with the run's normal outcome code (`0`/`1`/`3`). Scripts can still tell whether
the partial output is trustworthy.

A **second Ctrl-C** exits `130` immediately. The same drain applies to stream modes:
`reduce --window` flushes its partial window, and `top_k --stream` already has its
board on screen.

Whole-set `reduce` and `top_k` exit `130` at once. They produce one result at the end,
so there is nothing to drain.

## See also

- [Quickstart](../quickstart.md) · [Cookbook](../cookbook/README.md) ·
  [Troubleshooting](../troubleshooting.md)
