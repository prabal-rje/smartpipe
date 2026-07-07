# Cookbook

Real pipelines, copy-pasteable. Each recipe is a small composition of smartpipe verbs
with the Unix tools you already use.

| Recipe | What it does |
|---|---|
| [Contract & document extraction](contract-extraction.md) | Pull structured fields out of a folder of PDFs |
| [Video Q&A and scene digests](video-qa.md) | Ask questions of video — watched natively or sampled to your density |
| [`.sem` stage files](../reference/sem-files.md) | Save a pipe stage as an executable script |
| [Live stream enrichment](stream-enrichment.md) | Tag a live stream with the catalog rows it concerns (`join`) |
| [Log triage](log-triage.md) | Filter and summarize noisy logs by meaning |
| [Ranking documents](ranking-documents.md) | Find the most relevant files for a query |
| [Live monitoring](live-monitoring.md) | `tail -f` through semantic verbs, windows, and a live leaderboard |

## The shape of every recipe

smartpipe verbs are filters — they read stdin (or files), write stdout, and compose:

```console
$ cat data | smartpipe filter "..." | smartpipe map "Extract {...}" | jq ... > out.csv
```

Because structured output is NDJSON, everything downstream of a `map` speaks `jq`,
`csv`, spreadsheets, or another `smartpipe` verb. Because plain output is just text,
everything upstream can be `grep`, `head`, `find`, or `git`.

## A note on cost

Every verb calls a model once per item (plus a repair retry only when structured
output needs fixing). If you're on a paid API, `head`-limit while you iterate:

```console
$ cat big.jsonl | head -20 | smartpipe map "..."    # test on 20 before running 20,000
```

Or stay free with a local Ollama model — see [Models & providers](../concepts/models-and-providers.md).

- **[Visualizing results](visualizing-results.md)** — distributions, ranked tables,
  `--tally`, and the join threshold picker.
