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
composes with `grep`, `jq`, `sort`, and `tail -f` like it was always there.

## The verbs

| Verb | What it does | Feels like | Status |
|---|---|---|---|
| `map` | transform each item with a prompt | `sed`, but it understands | ✅ shipped |
| `filter` | keep items matching a plain-English condition | `grep`, but semantic | ✅ shipped |
| `embed` | turn items into vectors | plumbing for `top_k` | 🔜 next |
| `top_k` | rank items by similarity to a query | `sort \| head`, by meaning | 🔜 |
| `reduce` | synthesize many items into one | `awk` END block, but literate | 🔜 |
| `config` | one-minute interactive setup | — | ✅ shipped |

> **v0.2.0** ships `map`, `filter`, and `config` end to end. The remaining verbs
> land in the releases that follow — the architecture for all five is already in
> place. Watch [CHANGELOG.md](CHANGELOG.md).

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
```

New to any of this? The [ten-minute quickstart](docs/quickstart.md) assumes nothing —
including that you know what a "model" is.

## Local-first, by default

Out of the box `sempipe` talks to [Ollama](https://ollama.com) on your machine: free,
private, no API key. Any invocation can use a cloud model instead
(`--model claude-opus-4-8`, `--model gpt-4o-mini` — keys via environment variables,
never stored). Your text goes to the endpoint you configured and nowhere else —
no telemetry, no accounts, ever.

## It behaves like a real Unix tool

- **stdout is data, stderr is chatter.** Progress bars never contaminate your pipe.
- **TTY-aware.** Human-readable at the terminal, NDJSON when piped — automatically.
- **Order-preserving.** Output order always matches input order, even with parallel calls.
- **Failure-tolerant.** One bad item is a warning, not a crash.

## Learn more

- [Quickstart](docs/quickstart.md) — zero to first result, gently
- [Install](docs/install.md) — pipx, pip, uv, and the optional extras
- [`map`](docs/verbs/map.md) and [`filter`](docs/verbs/filter.md) — the verbs, examples first
- [Pipes & items](docs/concepts/pipes-and-items.md) — the mental model
- [Models & providers](docs/concepts/models-and-providers.md) — local vs cloud, model strings, precedence
- [Structured output](docs/concepts/structured-output.md) — braces vs `--schema`

## Development

Built in the open. The design docs, staged plan, and progress ledger live in
[`plan/`](plan/README.md); contributor setup and the quality gates are in
[CONTRIBUTING.md](CONTRIBUTING.md). Pre-1.0: the verb surface is stable; flags may
still gain (rarely change) meaning.
