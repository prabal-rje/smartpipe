# smartpipe

[![CI](https://github.com/prabal-rje/smartpipe/actions/workflows/ci.yml/badge.svg)](https://github.com/prabal-rje/smartpipe/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/smartpipe-cli)](https://pypi.org/project/smartpipe-cli/)
[![Python](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue)](pyproject.toml)
[![Docs](https://img.shields.io/badge/docs-prabal--rje.github.io%2Fsmartpipe-blue)](https://prabal-rje.github.io/smartpipe/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue)](LICENSE)

**Semantic pipes and queries for your terminal.**

Run PDFs, images, audio, video, and text through Unix verbs that understand
their input. Use Ollama for local models, or choose a cloud provider explicitly.

## Install

```bash
# zero-install trial (or: pip install smartpipe-cli)
uvx --from smartpipe-cli smartpipe
```

## Point it at a model

```bash
# log in with a ChatGPT account - covers most people
smartpipe auth login
```

No ChatGPT plan? Use local [Ollama][ollama] or a cloud API key - see
[Models & providers][models].

## Examples

```bash
# summarize each filing, figures included
smartpipe map "summarize the key risk" 'filings/*.pdf'

# keep only the calls that sound frustrated - audio, understood
smartpipe filter "the caller sounds frustrated" 'calls/*.mp3'

# text on stdin works the same way
echo "hello world" \
| smartpipe map "translate to Spanish"
# → hola mundo
```

PDFs pass through with their figures. Scanned pages route to a vision model.
Audio is sent natively where the model hears it, transcribed locally otherwise.
Video is sent whole where the wire supports it, or split into frames plus a
transcript. Each conversion is noted per row.

It composes with `grep`, `jq`, `sort`, and `tail -f`: `stdin` to `stdout`,
one item at a time.

## Verbs

A **verb** is one operation on your data - `map`, `filter`, `cluster`. Each reads
`stdin` (or named FILES) and writes `stdout`, so verbs pipe into each other and
into ordinary Unix tools. Every verb is documented at
[prabal-rje.github.io/smartpipe][docs].

**Semantic verbs** call a model:

| Verb | What it does | Feels like |
|---|---|---|
| [`map`][map] | transform each item - text or media - with a prompt | `sed`, but it understands |
| [`extend`][extend] | add extracted fields; keep everything else | your record, plus columns |
| [`filter`][filter] | keep items matching a plain-English condition | `grep`, but semantic |
| [`embed`][embed] / [`top_k`][top_k] | vectors; rank by similarity | `sort \| head`, by meaning |
| [`reduce`][reduce] | synthesize many items into one | `awk` END, but literate |
| [`join`][join] | match two inputs (`--kind inner\|leftouter\|anti`); `--on` alone is free | SQL join, but semantic |
| [`cluster`][cluster] | group by meaning, label each group | themes with sizes and quotes |
| [`distinct`][distinct] | fold near-duplicates; `--exact` is free | `sort -u`, by meaning |
| [`diff`][diff] | what distinguishes two sets | the post-incident answer |
| [`outliers`][outliers] | the items least like the rest | novelty, surfaced |

**Free verbs** never call a model. Run them first to cut the corpus before any
paid stage:

| Verb | What it does | Feels like |
|---|---|---|
| [`where`][where] | filter on exact field predicates | SQL `WHERE` |
| [`summarize`][summarize] | count, average, percentiles, time buckets | SQL `GROUP BY` |
| [`sort`][sort] | order items by a field | `sort` |
| [`sample`][sample] | take a seeded random subset | `shuf` |
| [`getschema`][getschema] | list fields, types, and coverage | `head`, for structure |
| [`split`][split] | break items into pieces (pages, minutes) | `split` |
| [`chart`][cli] | terminal bars, SVG, facets, time series | quick plots |

Some semantic verbs have a **conditionally free mode**: `join --on` (key
equality, no prompt), `distinct --exact` (hash-only folding), `map`/`extend`
`--dry-run` (compose without sending), and `smartpipe schema` with a
braces/DSL expression. Each stays at zero model calls by construction.

## A one-minute tour

```bash
# 1. point smartpipe at a model (ChatGPT login, a cloud key, or local Ollama)
smartpipe config

# 2. ask one question across a folder of mixed documents
smartpipe map "What does this say about pricing?" 'docs/*.pdf'

# 3. typed extraction - braces carry names, types, and guidance
cat tickets.jsonl \
| smartpipe extend "Add {label enum(bug, feature, praise), urgency number: 0 to 1}"

# 4. group feedback by meaning, label each theme, chart it
cat feedback.txt \
| smartpipe cluster --top 8 \
| smartpipe chart cluster --save themes.svg

# 5. cut for free with `where`, then let the model judge only what is left
cat app.log \
| smartpipe where 'text has "ERROR"' \
| smartpipe filter "an actual outage"

# 6. save a whole pipeline as a file; it runs as a command
smartpipe run triage.sem --dry-run   # prints the stage graph and cost, makes zero calls

# 7. month-end close: the vision model IS the OCR; the anti-join is the worklist
smartpipe map "Extract {vendor string, invoice_number string, total number}" 'invoices/2026-06/*.pdf' \
| tee june-invoices.ndjson \
| smartpipe join "the same payment" --right ledger.jsonl --kind anti > missing-from-ledger.jsonl

# 8. video RAG, no vector database: index a folder of recordings once, ask any day
smartpipe embed 'sessions/**/*.mp4' > sessions.embeddings
smartpipe top_k 3 --near "user gives up after the coupon fails" < sessions.embeddings
```

Numbers 7 and 8 are full recipes -
[invoice reconciliation](docs/cookbook/invoice-reconciliation.md) and
[video RAG](docs/cookbook/video-qa.md) - two of a dozen in the
[cookbook](docs/cookbook/README.md).

New to this? The [Learn track][quickstart] starts at zero and assumes nothing, including
what a "model" is.

## Where your data goes

Some steps run locally no matter which chat model you pick: embeddings
(`fastembed`) and transcription (`whisper`) are built in.

For chat, [Ollama][ollama] runs models on your machine. Any cloud model sends
that run's data to its provider - `gpt-5.4-mini`, `claude-opus-4-8`,
`gemini-3.1-flash-lite`, `mistral-large-latest`, `openrouter/…`.

API keys come from environment variables and are never stored. ChatGPT
subscribers can run `smartpipe auth login` instead. `smartpipe usage` keeps
local run and token totals; see [Privacy & security][privacy] for the details.

## Unix behavior

- **`stdout` is data, `stderr` is diagnostics.** Progress and receipts never
  touch your pipe.
- **Adapts to where it runs.** Readable tables at a terminal; `JSONL` when piped
  into another command.
- **Order-preserving.** Output order matches input order, even with parallel calls.
- **Failure-tolerant.** One bad item is a warning, not a crash.

## Learn more

Full docs: **[prabal-rje.github.io/smartpipe][docs]**.

- [Learn track][quickstart] - zero to first result, six short chapters
- [Install][install] - packages and platforms
- [Working with files & media][files] - PDFs, scans, images, audio, video
- [CLI reference][cli] - every flag, format, and exit code
- [Models & providers][models] - local Ollama, cloud keys, ChatGPT login
- [Privacy & security][privacy]

## How to cite

If smartpipe is useful in your research, cite it (or run `smartpipe cite`):

```bibtex
@software{gupta_smartpipe_2026,
  author = {Gupta, Prabal},
  title = {smartpipe: semantic pipes for your terminal},
  year = {2026},
  version = {1.3.1},
  license = {Apache-2.0},
  url = {https://github.com/prabal-rje/smartpipe}
}
```

GitHub's "Cite this repository" button (from [CITATION.cff](CITATION.cff)) gives APA too.

## Development

Built in the open, under **Apache-2.0**. Contributor setup and the quality
gates are in [CONTRIBUTING.md](CONTRIBUTING.md); the manual release pass
lives in [`qa/`](qa/README.md). The CLI surface is a SemVer contract.

[docs]: https://prabal-rje.github.io/smartpipe/
[quickstart]: https://prabal-rje.github.io/smartpipe/learn/1-first-pipeline/
[install]: https://prabal-rje.github.io/smartpipe/install/
[files]: https://prabal-rje.github.io/smartpipe/inputs/files/
[cli]: https://prabal-rje.github.io/smartpipe/reference/cli/
[models]: https://prabal-rje.github.io/smartpipe/concepts/models-and-providers/
[privacy]: https://prabal-rje.github.io/smartpipe/privacy/
[ollama]: https://ollama.com
[map]: https://prabal-rje.github.io/smartpipe/verbs/map/
[extend]: https://prabal-rje.github.io/smartpipe/verbs/extend/
[filter]: https://prabal-rje.github.io/smartpipe/verbs/filter/
[embed]: https://prabal-rje.github.io/smartpipe/verbs/embed/
[top_k]: https://prabal-rje.github.io/smartpipe/verbs/top-k/
[reduce]: https://prabal-rje.github.io/smartpipe/verbs/reduce/
[join]: https://prabal-rje.github.io/smartpipe/verbs/join/
[cluster]: https://prabal-rje.github.io/smartpipe/verbs/cluster/
[distinct]: https://prabal-rje.github.io/smartpipe/verbs/distinct/
[diff]: https://prabal-rje.github.io/smartpipe/verbs/diff/
[outliers]: https://prabal-rje.github.io/smartpipe/verbs/outliers/
[where]: https://prabal-rje.github.io/smartpipe/verbs/where/
[summarize]: https://prabal-rje.github.io/smartpipe/verbs/summarize/
[sort]: https://prabal-rje.github.io/smartpipe/verbs/sort/
[sample]: https://prabal-rje.github.io/smartpipe/verbs/sample/
[getschema]: https://prabal-rje.github.io/smartpipe/verbs/getschema/
[split]: https://prabal-rje.github.io/smartpipe/verbs/split/
