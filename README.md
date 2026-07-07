# smartpipe

**Pipe anything with meaning through your terminal — PDFs, images, audio,
video, and text.** Unix verbs that understand their input, powered by a local
model by default and a cloud model when you ask.

> Install as `smartpipe`; the command is `sempipe` (a `smartpipe` alias works
> too). The import/package name stays `sempipe` — same tool, clearer name.

```console
$ pip install smartpipe

$ sempipe map "summarize the key risk" --in 'filings/*.pdf'     # documents, figures included
$ sempipe filter "the caller sounds frustrated" --in 'calls/*.mp3'
$ echo "hello world" | sempipe map "translate to Spanish"
hola mundo
```

A PDF arrives with its figures attached; a scanned page routes itself to a
vision model and says so; audio is heard natively or transcribed (whisper-1
automatically when your OpenAI key allows it); video is watched where the
wire supports it and decomposed into frames + transcript where it doesn't.
Every degradation is disclosed per row — nothing silently drops.

No server. No YAML. No vector database. stdin to stdout, composing with
`grep`, `jq`, `sort` — and `tail -f`: the per-item verbs stream.

## The verbs

**Semantic** (call a model):

| Verb | What it does | Feels like |
|---|---|---|
| `map` | transform each item — text or media — with a prompt | `sed`, but it understands |
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
Put them first — they cut the corpus before anything paid runs.

## Sixty seconds

```console
# 1. Point sempipe at a model (local & free via Ollama, or cloud):
$ sempipe config

# 2. Ask a question across a folder of mixed documents:
$ sempipe map "What does this say about pricing?" --in 'docs/*.pdf'

# 3. Typed extraction — braces carry names, types, AND guidance:
$ cat tickets.jsonl | sempipe extend "Add {label enum(bug, feature, praise), urgency number: 0 to 1}"

# 4. The analyst's Monday, one line:
$ cat feedback.txt | sempipe cluster --top 8 | sempipe chart cluster --save themes.svg

# 5. Free gates before paid judges — and watch the live token/media counts:
$ cat app.log | sempipe where 'text has "ERROR"' | sempipe filter "an actual outage"

# 6. Save the whole pipeline as a file; it becomes a command:
$ sempipe run triage.sem --dry-run     # the stage graph + cost posture, zero calls
```

New to any of this? The [ten-minute quickstart](docs/quickstart.md) assumes
nothing — including that you know what a "model" is.

## Local-first, honest about cost

Out of the box `sempipe` talks to [Ollama](https://ollama.com) on your
machine: free, private, no API key. Any invocation can use a cloud model
instead (`--model gpt-5.4-mini`, `claude-opus-4-8`, `gemini-2.5-flash`,
`mistral-large-latest`, `openrouter/…` — keys via environment variables,
never stored), and ChatGPT subscribers can skip keys with `sempipe auth
login`. Paid media conversions sit behind one consent (`allow-captions`),
every run shows **live token/media telemetry** in the status bar and ends
with a receipt (`run: 423 in · 75 out tokens`), `sempipe usage` tracks
hour/day/week/month/lifetime (resettable), and the opt-in result cache makes
re-runs free. Your data goes to the endpoint you configured and nowhere
else — no telemetry leaves your machine, no accounts, ever.

## It behaves like a real Unix tool

- **stdout is data, stderr is chatter.** Progress and receipts never contaminate your pipe.
- **TTY-aware.** Human-readable at the terminal, NDJSON when piped — automatically.
- **Order-preserving.** Output order matches input order, even with parallel calls.
- **Failure-tolerant.** One bad item is a warning, not a crash.
- **Reproducible.** Temperature 0 everywhere, seeded sampling, deterministic clustering.

## Learn more

Full docs in [`docs/`](docs/index.md) (or as a site — `uv run --group docs mkdocs serve`):

- [Quickstart](docs/quickstart.md) — zero to first result, gently · [Install](docs/install.md)
- [Working with files & media](docs/inputs/files.md) — PDFs, scans, images, audio, video
- The verbs — [`map`](docs/verbs/map.md), [`extend`](docs/verbs/extend.md), [`filter`](docs/verbs/filter.md), [`cluster`](docs/verbs/cluster.md), [`distinct`](docs/verbs/distinct.md), [`diff`](docs/verbs/diff.md), [`where`](docs/verbs/where.md), [and the rest](docs/reference/cli.md)
- [Training-data prep, end to end](docs/cookbook/training-data-prep.md) — the curator's loop with receipts
- [Custom verbs](docs/reference/custom-verbs.md) · [`.sem` pipelines](docs/reference/sem-files.md) · [Troubleshooting](docs/troubleshooting.md) · [Privacy](docs/privacy.md)

## How to cite

If smartpipe is useful in your research, cite it (or run `sempipe cite`):

```bibtex
@software{gupta_smartpipe_2026,
  author = {Gupta, Prabal},
  title = {smartpipe: semantic pipes for your terminal},
  year = {2026},
  version = {1.2.0},
  license = {Apache-2.0},
  url = {https://github.com/prabal-rje/smartpipe}
}
```

GitHub's "Cite this repository" button (from [CITATION.cff](CITATION.cff)) gives APA too.

## Development

Built in the open, under **Apache-2.0**. Contributor setup and the quality
gates are in [CONTRIBUTING.md](CONTRIBUTING.md); the manual release pass
lives in [`qa/`](qa/README.md). The CLI surface is a SemVer contract.
