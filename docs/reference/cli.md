# CLI reference

The complete surface, in one page. As of 1.0 this is a contract governed by
[SemVer](https://semver.org) — flags, formats, and exit codes won't change under you
within a major version.

## Synopsis

```
sempipe <verb> [PROMPT] [OPTIONS]
```

Input comes from stdin (each line an item) or from files (`--in` / `--from-files`,
each file an item). Results go to **stdout**; progress and warnings go to **stderr**.

## Verbs

| Verb | Purpose | Page |
|---|---|---|
| [`map`](../verbs/map.md) | transform each item with a prompt | one item in, one out |
| [`filter`](../verbs/filter.md) | keep items matching a condition | semantic grep |
| [`embed`](../verbs/embed.md) | items → vectors (NDJSON) | plumbing for `top_k` |
| [`top_k`](../verbs/top-k.md) | rank by similarity to a query | `sort \| head`, by meaning |
| [`reduce`](../verbs/reduce.md) | synthesize many items into one | recursive, automatic |
| [`config`](#config) | view and set defaults | interactive setup |

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

## Verb-specific options

| Verb | Options |
|---|---|
| `map` | `--schema FILE`, `--output {auto,text,json,csv,tsv}` |
| `filter` | `--not` (invert, like `grep -v`) |
| `top_k` | `K` (positional), `--near TEXT` (required), `--threshold FLOAT` |
| `reduce` | `--schema FILE`, `--group-by FIELD`, `--verbose` |

## `config`

```console
$ sempipe config                     # interactive first-run setup
$ sempipe config show                # effective settings + where each comes from
$ sempipe config model MODEL         # set the default chat model
$ sempipe config embed-model MODEL   # set the default embedding model
```

API keys are **never** stored — they're read from the environment.

## `cite`

```console
$ sempipe cite                       # print a BibTeX entry for citing sempipe
```

## Output formats

`--output` (or `SEMPIPE_OUTPUT`): `auto` (default), `text`, `json`, `csv`, `tsv`.
See [Output formats](../concepts/output-formats.md). `auto` shows a readable view at a
terminal and NDJSON when piped; `csv`/`tsv` need structured (named-field) output.

## Environment variables

| Variable | Effect |
|---|---|
| `SEMPIPE_MODEL` | Default chat model. |
| `SEMPIPE_EMBED_MODEL` | Default embedding model. |
| `SEMPIPE_OUTPUT` | Default output format. |
| `SEMPIPE_OPENAI_BASE_URL` | Point the OpenAI-compatible adapter at any endpoint. |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Cloud credentials (read, never stored). |
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
| `130` | INTERRUPTED — Ctrl-C. |

## See also

- [Quickstart](../quickstart.md) · [Cookbook](../cookbook/README.md) ·
  [Troubleshooting](../troubleshooting.md)
