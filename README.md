# smartpipe

[![CI](https://github.com/prabal-rje/smartpipe/actions/workflows/ci.yml/badge.svg)](https://github.com/prabal-rje/smartpipe/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/smartpipe)](https://pypi.org/project/smartpipe/)
[![Python](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue)](LICENSE)

**Semantic pipes for your terminal.**

Run PDFs, images, audio, video, and text through Unix verbs that understand their
input. Use Ollama for local models, or choose a cloud provider explicitly.

> Formerly `sempipe` (which still works as a command alias). The import name,
> `SMARTPIPE_*` env vars, and `~/.config/smartpipe` keep the old spelling.

```console
$ uvx smartpipe          # zero-install trial (or: pip install smartpipe)

$ smartpipe map "summarize the key risk" --in 'filings/*.pdf'     # documents, figures included
$ smartpipe filter "the caller sounds frustrated" --in 'calls/*.mp3'
$ echo "hello world" \
    | smartpipe map "translate to Spanish"
hola mundo
```

A PDF arrives with its figures attached. A scanned page routes itself to a vision
model and says so.

Audio is heard natively or transcribed. Video is watched where the wire supports
it, and decomposed into frames plus transcript where it does not. Every degradation
is disclosed per row.

No server. No YAML. No vector database. stdin to stdout, composing with
`grep`, `jq`, `sort` - and `tail -f`: the per-item verbs stream.

## The verbs

**Semantic** (call a model):

| Verb | What it does | Feels like |
|---|---|---|
| `map` | transform each item - text or media - with a prompt | `sed`, but it understands |
| `extend` | add extracted fields; everything else survives | your record, plus columns |
| `filter` | keep items matching a plain-English condition | `grep`, but semantic |
| `embed` / `top_k` | vectors; rank by similarity | `sort \| head`, by meaning |
| `reduce` | synthesize many items into one | `awk` END, but literate |
| `join` | match two inputs (`--kind inner\|leftouter\|anti`) | SQL join, but semantic |
| `cluster` | group by meaning, label each group | themes with sizes and quotes |
| `distinct` | fold near-duplicates | `sort -u`, by meaning |
| `diff` | what distinguishes two sets | the post-incident answer |
| `outliers` | the items least like the rest | novelty, surfaced |

**Free utilities** (never call a model): `where` (KQL-style predicates),
`summarize` (count/avg/percentiles, time buckets), `sort`, `sample` (seeded),
`getschema`, `split`, `chart` (terminal bars, SVG, facets, time series).
Put them first - they cut the corpus before anything paid runs.

## Sixty seconds

```console
# 1. Point smartpipe at a model (local & free via Ollama, or cloud):
$ smartpipe config

# 2. Ask a question across a folder of mixed documents:
$ smartpipe map "What does this say about pricing?" --in 'docs/*.pdf'

# 3. Typed extraction - braces carry names, types, AND guidance:
$ cat tickets.jsonl \
    | smartpipe extend "Add {label enum(bug, feature, praise), urgency number: 0 to 1}"

# 4. The analyst's Monday, one line:
# group by meaning, label each theme; chart it for the deck
$ cat feedback.txt \
    | smartpipe cluster --top 8 \
    | smartpipe chart cluster --save themes.svg

# 5. Free gates before paid judges - and watch the live token/media counts:
# where cuts for free; the model judges only what remains
$ cat app.log \
    | smartpipe where 'text has "ERROR"' \
    | smartpipe filter "an actual outage"

# 6. Save the whole pipeline as a file; it becomes a command:
$ smartpipe run triage.sem --dry-run     # the stage graph + cost posture, zero calls
```

New to any of this? The [ten-minute quickstart](docs/quickstart.md) assumes
nothing - including that you know what a "model" is.

## Honest about where your data goes, and what it costs

Some of smartpipe runs locally regardless of chat model choice: local embeddings
(fastembed) and local transcription (whisper) ship built in.

For chat, [Ollama](https://ollama.com) gives you a local path when it runs on your
machine. If you choose a cloud model, that provider sees the data for that run.
Examples: `gpt-5.4-mini`, `claude-opus-4-8`, `gemini-3.1-flash-lite`,
`mistral-large-latest`, and `openrouter/...`.

API keys come from environment variables and are never stored. ChatGPT subscribers
can use `smartpipe auth login` instead.

Paid media conversions require `allow-captions`. Runs show live token/media counts
and end with a receipt. `smartpipe usage` keeps local hour/day/week/month/lifetime
totals, and the opt-in result cache makes repeated calls free.

## It behaves like a real Unix tool

- **stdout is data, stderr is chatter.** Progress and receipts never contaminate your pipe.
- **TTY-aware.** Human-readable at the terminal, NDJSON when piped - automatically.
- **Order-preserving.** Output order matches input order, even with parallel calls.
- **Failure-tolerant.** One bad item is a warning, not a crash.
- **Reproducible.** Temperature 0 everywhere, seeded sampling, deterministic clustering.

## Learn more

Full docs in [`docs/`](docs/index.md) (or as a site - `uv run --group docs mkdocs serve`):

- [Quickstart](docs/quickstart.md) - zero to first result, gently
- [Install](docs/install.md) - package and platform notes
- [Working with files & media](docs/inputs/files.md) - PDFs, scans, images, audio, video
- [The verbs](docs/reference/cli.md) - `map`, `extend`, `filter`, `cluster`,
  `distinct`, `diff`, `where`, and the rest
- [Training-data prep](docs/cookbook/training-data-prep.md) - the curator's loop
  with receipts
- [Custom verbs](docs/reference/custom-verbs.md), [`.sem` pipelines](docs/reference/sem-files.md),
  [Troubleshooting](docs/troubleshooting.md), and [Privacy](docs/privacy.md)

## How to cite

If smartpipe is useful in your research, cite it (or run `smartpipe cite`):

```bibtex
@software{gupta_smartpipe_2026,
  author = {Gupta, Prabal},
  title = {smartpipe: semantic pipes for your terminal},
  year = {2026},
  version = {1.3.0},
  license = {Apache-2.0},
  url = {https://github.com/prabal-rje/smartpipe}
}
```

GitHub's "Cite this repository" button (from [CITATION.cff](CITATION.cff)) gives APA too.

## Development

Built in the open, under **Apache-2.0**. Contributor setup and the quality
gates are in [CONTRIBUTING.md](CONTRIBUTING.md); the manual release pass
lives in [`qa/`](qa/README.md). The CLI surface is a SemVer contract.
