# sempipe (planning repository)

> Semantic pipes for your terminal — `map`, `filter`, `embed`, `top_k`, `reduce` as
> composable Unix verbs, powered by a local model by default.

**Status: planning.** No code yet — this repository currently holds the product spec and
a complete, staged implementation plan for building `sempipe` as an installable Python
package. (The idea was drafted under the name *UNIPIPE*; it is being built as `sempipe`
because `unipipe` is taken on PyPI — [why](plan/decisions.md#d01--the-name-unipipe-is-taken--rename-to-sempipe).)

## Start here

| You want to… | Read |
|---|---|
| Understand the idea and why it's worth building | [`idea.md`](idea.md) — the original design doc + a survey showing the niche is open |
| See what we're building and in what order | [`plan/README.md`](plan/README.md) — the plan's front door (5-minute read) |
| Check a specific design choice | [`plan/decisions.md`](plan/decisions.md) |
| See what's happening right now | [`TODO.md`](TODO.md) |

## The elevator pitch

```console
$ pip install sempipe
$ echo "hello world" | sempipe map "translate to Spanish"
hola mundo
$ cat reviews.jsonl | sempipe filter "review is negative" \
    | sempipe reduce "What are the top 3 complaints?"
```

Text in, text out. No server, no YAML, no vector database, no telemetry. Local-first via
Ollama; any cloud model on request. It composes with `grep`, `jq`, and `tail -f` because
it behaves like the tools that came before it.

## Roadmap at a glance

Eleven stages, each ending runnable, releases from Stage 3 (`v0.1.0`, the first
`pip install`-able release) to Stage 10 (`v1.0.0`). Full map with links:
[`plan/README.md → The stages`](plan/README.md#the-stages).
