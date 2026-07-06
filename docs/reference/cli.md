# CLI reference

The complete surface, in one page. As of 1.0 this is a contract governed by
[SemVer](https://semver.org) — flags, formats, and exit codes won't change under you
within a major version.

## Synopsis

```
sempipe <verb> [PROMPT] [OPTIONS]
```

Input comes from stdin (each line an item — or ONE redirected binary document),
from files (`--in` / `--from-files`, each file an item), or both (`--in` files
first, then the piped lines). Results go to **stdout**; progress and warnings go
to **stderr**.

## Verbs

| Verb | Purpose | Page |
|---|---|---|
| [`map`](../verbs/map.md) | transform each item with a prompt | one item in, one out |
| [`filter`](../verbs/filter.md) | keep items matching a condition | semantic grep |
| [`embed`](../verbs/embed.md) | items → vectors (NDJSON) | plumbing for `top_k` |
| [`top_k`](../verbs/top-k.md) | rank by similarity to a query | `sort \| head`, by meaning |
| [`reduce`](../verbs/reduce.md) | synthesize many items into one | recursive, automatic |
| [`join`](../verbs/join.md) | match stdin against a second input | embed-block-judge |
| [`config`](#config) | view and set defaults | interactive setup |
| [`run`](#run) | execute a saved `.sem` stage file | [format](sem-files.md) |
| [`doctor`](#doctor) | check the whole setup, spend nothing | exit 0 = ready |
| `schema` | draft a JSON Schema from English (one call, validated) | [ladder](../concepts/structured-output.md#the-ladder-top-to-bottom) |

## Common options

These apply to the model-using verbs (`map`, `filter`, `top_k`, `reduce`; `embed` and
`top_k` use `--embed-model` instead of `--model`):

| Option | Meaning |
|---|---|
| `--model TEXT` | Model for this run — overrides the config and `SEMPIPE_MODEL`. |
| `--embed-model TEXT` | Embedding model (`embed`, `top_k`). |
| `--concurrency N` | Max parallel model calls (default 4). |
| `--in GLOB` | Read each matching file as one item (repeatable). |
| `--from-files` | Treat each stdin line as a filename. |
| `--fields A,B` | Select + order columns of structured output (`map`, `embed`, `top_k`, `reduce` — never `filter`). |
| `--max-calls N` | Hard ceiling on model calls (cost cap). Per-item verbs stop intake and drain; whole-set `top_k`/`reduce` treat exhaustion as fatal (nothing usable from a partial collection). A capped run never exits 0. |

## Verb-specific options

| Verb | Options |
|---|---|
| `map` | `--schema FILE`, `--schema-from DSL`, `--output {auto,text,json,csv,tsv}` |
| `filter` | `--not` (invert, like `grep -v`) |
| `top_k` | `K` (positional), `--near TEXT` (required), `--threshold FLOAT`, `--stream` (live leaderboard) |
| `reduce` | `--schema FILE`, `--schema-from DSL`, `--group-by FIELD`, `--verbose`, `--window N [--every M]` (stream mode) |
| `join` | `--right FILE` (required), `--k N` (default 5), `--threshold FLOAT`, `--embed-model` |

## `config`

```console
$ sempipe config                     # interactive first-run setup
$ sempipe config show                # effective settings + where each comes from
$ sempipe config model MODEL         # set the default chat model
$ sempipe config embed-model MODEL   # set the default embedding model
```

API keys are **never** stored — they're read from the environment.

Edits via `sempipe config` rewrite the file atomically; unknown keys are
preserved, comments are not.

## `auth`

```console
$ sempipe auth login             # log in with ChatGPT (browser)
$ sempipe auth login --headless  # device-code flow for remote machines
$ sempipe auth status            # logged in? which account?
$ sempipe auth logout            # remove the stored tokens
```

With a login and no `OPENAI_API_KEY`, OpenAI models ride your ChatGPT plan
(Codex-family models). An exported key always takes precedence.

## `cite`

```console
$ sempipe cite                       # print a BibTeX entry for citing sempipe
```

## `doctor`

```console
$ sempipe doctor        # config · Ollama · models · keys · login · extras · completions
```

One line per check with its fix inline; exit 0 all-green, 1 if anything needs
attention. Never makes a paid model call; key lines report presence, never values.

## `run`

```console
$ sempipe run extract.sem < cards.txt        # execute a saved stage
$ sempipe run extract.sem --model ollama/qwen3:8b   # flags override the file
```

A `.sem` file pins one verb invocation in TOML; with a
`#!/usr/bin/env -S sempipe run` shebang it runs directly (`./extract.sem`).
Unknown keys in the file are errors (scripts run unattended). Full format:
[.sem stage files](sem-files.md).

## Output formats

`--output` (or `SEMPIPE_OUTPUT`): `auto` (default), `text`, `json`, `csv`, `tsv`.
See [Output formats](../concepts/output-formats.md). `auto` shows a readable view at a
terminal and NDJSON when piped; `csv`/`tsv` need structured (named-field) output.
`--fields a,b` projects structured output to just those columns, in that order,
identically in every format.

## Shell completion

Tab completion for bash, zsh, and fish — including live model-name suggestions on
`--model`/`--embed-model` and `config model`. One-liners per shell in
[Installing sempipe → Tab completion](../install.md#tab-completion).

## Environment variables

| Variable | Effect |
|---|---|
| `SEMPIPE_MODEL` | Default chat model. |
| `SEMPIPE_EMBED_MODEL` | Default embedding model. |
| `SEMPIPE_OUTPUT` | Default output format. |
| `SEMPIPE_MAX_CALLS` | Default call ceiling (see `--max-calls`). |
| `SEMPIPE_OPENAI_BASE_URL` | Point the OpenAI-compatible adapter at any endpoint. |
| `SEMPIPE_MISTRAL_BASE_URL` | Point the Mistral adapter elsewhere (proxies, gateways). |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `MISTRAL_API_KEY` | Cloud credentials (read, never stored). |
| `OLLAMA_HOST` | Ollama endpoint (default `http://localhost:11434`). |
| `NO_COLOR` | Disable color. |

## Exit codes

Chosen so a script can branch on *how* a run went, not just pass/fail:

| Code | Meaning |
|---|---|
| `0` | OK — everything succeeded (including zero matches for `filter`). |
| `1` | PARTIAL — some items were skipped; the rest succeeded. |
| `2` | SETUP — misconfiguration (no model, unreachable Ollama, missing key). |
| `3` | ALL_FAILED — every item failed. |
| `64` | USAGE — bad flags or input. |
| `70` | BUG — an internal error (please report it). |
| `130` | INTERRUPTED — Ctrl-C (before anything finished, or pressed twice). |
| `141` | SIGPIPE — downstream closed the pipe (normal in `\| head` pipelines); sempipe prints nothing. |

### What Ctrl-C does

For the per-item verbs (`map`, `filter`, `embed`), the **first Ctrl-C** stops new work,
lets what's already in flight finish (up to 10 s), emits those results in order, prints
`done: interrupted — N processed · M skipped` on stderr, and exits with the run's normal
outcome code (`0`/`1`/`3`) — so a script still learns whether the partial output is
trustworthy. A **second Ctrl-C** exits `130` immediately. The same drain applies to the stream
modes (`reduce --window` flushes its partial window; `top_k --stream`'s board is
already on screen). Whole-set `reduce`/`top_k` exit `130` at once — they produce one
result at the end, so there's nothing to drain.

## See also

- [Quickstart](../quickstart.md) · [Cookbook](../cookbook/README.md) ·
  [Troubleshooting](../troubleshooting.md)
