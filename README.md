# sempipe

**Semantic pipes for your terminal.** Five Unix verbs that understand meaning —
powered by a local model by default, a cloud model when you ask.

```console
$ pip install sempipe
$ echo "hello world" | sempipe map "translate to Spanish"
hola mundo
```

`sempipe` treats an LLM the way Unix treats everything: text in, text out.
No server. No YAML. No vector database. It reads stdin, writes stdout, and
composes with `grep`, `jq`, `sort` — and `tail -f`: the per-item verbs stream,
so `tail -f app.log | sempipe filter "a real error"` works with no flag, and
`reduce --window` / `top_k --stream` turn live feeds into rolling summaries and
a live leaderboard.

## The verbs

| Verb | What it does | Feels like | Status |
|---|---|---|---|
| `map` | transform each item with a prompt | `sed`, but it understands | ✅ shipped |
| `filter` | keep items matching a plain-English condition | `grep`, but semantic | ✅ shipped |
| `embed` | turn items into vectors | plumbing for `top_k` | ✅ shipped |
| `top_k` | rank items by similarity to a query | `sort \| head`, by meaning | ✅ shipped |
| `reduce` | synthesize many items into one | `awk` END block, but literate | ✅ shipped |
| `config` | one-minute interactive setup | — | ✅ shipped |

> **Stable surface** (SemVer since 1.0). All five verbs plus `config`, file inputs
> (`--in 'reports/*.pdf'`, parsed automatically), CSV/TSV output — and, new on main:
> true streaming (`tail -f`, `--window`, the live leaderboard). See [CHANGELOG.md](CHANGELOG.md).

## Sixty seconds

```console
# 1. Point sempipe at a model (local & free via Ollama, or cloud):
$ sempipe config

# 2. Transform each line:
$ cat notes.txt | sempipe map "translate to French"

# 3. Structured extraction — braces name the fields you want back:
$ cat receipts.txt | sempipe map "Extract {vendor, date, total}"
{"vendor": "Acme Corp", "date": "2026-01-15", "total": 1250.00}

# 4. Semantic grep, then count:
$ cat server.log | sempipe filter "indicates a real bug" | wc -l

# 5. Compose. That's the whole point:
$ cat receipts.txt | sempipe map "Extract {vendor, total}" | jq -r .total

# 6. Save a stage you use often as a .sem file, and it becomes a command:
$ sempipe run extract.sem < cards.txt        # or chmod +x and just ./extract.sem
```

New to any of this? The [ten-minute quickstart](docs/quickstart.md) assumes nothing —
including that you know what a "model" is.

## Local-first, by default

Out of the box `sempipe` talks to [Ollama](https://ollama.com) on your machine: free,
private, no API key. Any invocation can use a cloud model instead
(`--model claude-opus-4-8`, `--model gpt-4o-mini` — keys via environment variables,
never stored), and ChatGPT Plus/Pro subscribers can skip keys entirely with
`sempipe auth login`. Your text goes to the endpoint you configured and nowhere else —
no telemetry, no accounts, ever.

## It behaves like a real Unix tool

- **stdout is data, stderr is chatter.** Progress bars never contaminate your pipe.
- **TTY-aware.** Human-readable at the terminal, NDJSON when piped — automatically.
- **Order-preserving.** Output order always matches input order, even with parallel calls.
- **Failure-tolerant.** One bad item is a warning, not a crash.

## Learn more

Full docs in [`docs/`](docs/index.md) (or as a site — `uv run --group docs mkdocs serve`):

- [Quickstart](docs/quickstart.md) — zero to first result, gently · [Install](docs/install.md)
- The verbs — [`map`](docs/verbs/map.md), [`filter`](docs/verbs/filter.md), [`embed`](docs/verbs/embed.md), [`top_k`](docs/verbs/top-k.md), [`reduce`](docs/verbs/reduce.md) — examples first
- Concepts — [pipes & items](docs/concepts/pipes-and-items.md), [models & providers](docs/concepts/models-and-providers.md), [structured output](docs/concepts/structured-output.md), [output formats](docs/concepts/output-formats.md), [file inputs](docs/inputs/files.md)
- [Cookbook](docs/cookbook/README.md) — contract extraction, log triage, ranking documents
- [CLI reference](docs/reference/cli.md) · [Troubleshooting](docs/troubleshooting.md) · [Comparison](docs/comparison.md) · [Privacy](docs/privacy.md)

## How to cite

If sempipe is useful in your research, cite it (or run `sempipe cite` for the same):

```bibtex
@software{gupta_sempipe_2026,
  author = {Gupta, Prabal},
  title = {sempipe: semantic pipes for your terminal},
  year = {2026},
  version = {1.0.0},
  license = {Apache-2.0},
  url = {https://github.com/prabal-rje/sempipe}
}
```

GitHub's "Cite this repository" button (from [CITATION.cff](CITATION.cff)) gives APA too.

## Development

Built in the open, under **Apache-2.0**. The design docs, staged plan, and progress
ledger live in [`plan/`](plan/README.md); contributor setup and the quality gates are
in [CONTRIBUTING.md](CONTRIBUTING.md). As of 1.0 the CLI surface is a SemVer contract.
