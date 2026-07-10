# Cookbook

Real pipelines, copy-pasteable. Each recipe is a small composition of smartpipe verbs
with the Unix tools you already use (or don't yet - the
[five-line toolbox intro](../concepts/pipes-and-items.md#the-unix-toolbox-in-five-lines) covers them).

| Recipe | What it does |
|---|---|
| [Contract & document extraction](contract-extraction.md) | Pull structured fields out of a folder of PDFs |
| [Invoice reconciliation](invoice-reconciliation.md) | Scanned invoices become rows; an anti-join finds what never hit the ledger |
| [Training-data prep](training-data-prep.md) | The curator's loop: dedupe, gate, label at scale, decontaminate |
| [Video Q&A and scene digests](video-qa.md) | Ask questions of video - and video RAG over a folder of recordings |
| [Knowledge graph](knowledge-graph.md) | Who and what connects across a mixed corpus - free NER first, cited edges always |
| [Meeting digests](meeting-digest.md) | A week of call recordings into one digest that cites recording and minute |
| [Customer feedback](customer-feedback.md) | Detractor themes, week-over-week drift, deck-ready charts |
| [`.sem` stage files](../reference/sem-files.md) | Save a pipe stage as an executable script |
| [Live stream enrichment](stream-enrichment.md) | Tag a live stream with the catalog rows it concerns (`join`) |
| [Log triage](log-triage.md) | Filter, cluster, and diff noisy logs by meaning |
| [Ranking documents](ranking-documents.md) | Find the most relevant files for a query |
| [Live monitoring](live-monitoring.md) | `tail -f` through semantic verbs, windows, and a live leaderboard |

## Try the recipes on real files

Every recipe assumes a folder of your own data, but none requires one:
[smartpipe-playground](https://github.com/prabal-rje/smartpipe-playground)
ships 26 MB of CC0 / public-domain invoices, reports, photos, recordings,
screen sessions, and JSONL data:

```bash
curl -L https://github.com/prabal-rje/smartpipe-playground/releases/download/v1/smartpipe-playground-v1.tar.gz | tar xz
cd smartpipe-playground

smartpipe map "Extract {vendor, invoice_number, total number}" 'invoices/*.pdf'
smartpipe embed 'sessions/*.mp4' > sessions.embeddings
```

## The shape of every recipe

smartpipe verbs are filters - they read `stdin` (or files), write `stdout`, and compose:

```bash
cat data \
| smartpipe filter "..." \
| smartpipe map "Extract {...}" \
| jq ... > out.csv
```

Because structured output is JSONL, everything downstream of a `map` speaks `jq`,
`csv`, spreadsheets, or another `smartpipe` verb. Because plain output is just text,
everything upstream can be `grep`, `head`, `find`, or `git`.

## A note on cost

Every verb calls a model once per item (plus a repair retry only when structured
output needs fixing). If you're on a paid API, `head`-limit while you iterate:

```bash
cat big.jsonl \
| head -20 \
| smartpipe map "..."    # test on 20 before running 20,000
```

Or stay free with a local Ollama model - see [Models & providers](../concepts/models-and-providers.md).

- **[Visualizing results](visualizing-results.md)** - distributions, ranked tables,
  `--tally`, and the join threshold picker.
