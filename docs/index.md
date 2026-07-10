# smartpipe

**Semantic pipes and queries for your terminal.**

Run PDFs, images, audio, video, and text through Unix verbs that understand
their input: `map`, `filter`, `top_k`, `reduce`, and more. Use Ollama for local
models, or choose a cloud provider explicitly. Either way it stays ordinary
`stdin`/`stdout` composition.

<video controls playsinline preload="metadata" poster="assets/demo-thumb.jpg" width="100%">
  <source src="demo/smartpipe-demo.mp4" type="video/mp4">
  Your browser can't play the video here - <a href="demo/">watch the 86-second demo</a>.
</video>

```bash
cat reviews.txt \
| smartpipe filter "the reviewer is sarcastic" \
| smartpipe map "Extract {product, complaint, anger number: 0 to 1}"
```

Each line of `reviews.txt` is one item; `--as file` treats a whole file as one
item ([feeding smartpipe](concepts/feeding-smartpipe.md) has the full table).

## Pick your door

- **New to smartpipe?** The [Learn track](learn/1-first-pipeline.md) - six
  short chapters from first pipeline to production habits
  (after [installing](install.md)).
- **Have a task in mind?** The [Cookbook](cookbook/README.md) - complete,
  copy-pasteable recipes: contracts, video, logs, training data.
- **Wondering how something works?** [Concepts](concepts/the-item.md) - the
  item, ingestion, models, structured output.
- **Need the exact flag?** The [CLI reference](reference/cli.md) - the whole
  surface, one page, contract-stable.

## Try it on real files

No corpus handy? [smartpipe-playground](https://github.com/prabal-rje/smartpipe-playground)
ships 26 MB of CC0 / public-domain practice files - invoices, reports, photos,
recordings, screen sessions, and JSONL data:

```bash
curl -L https://github.com/prabal-rje/smartpipe-playground/releases/download/v1/smartpipe-playground-v1.tar.gz | tar xz
cd smartpipe-playground

smartpipe map "Extract {vendor, invoice_number, total number}" 'invoices/*.pdf'
smartpipe filter "the customer sounds frustrated" 'recordings/*.mp3'
```

## Verbs

A **verb** is one operation on your data. Each reads `stdin` (or named FILES)
and writes `stdout`, so verbs pipe into each other and into ordinary Unix tools.

**Semantic verbs** call a model:

| Verb | What it does | Feels like |
|---|---|---|
| [`map`](verbs/map.md) | transform each item with a prompt | `sed`, but it understands |
| [`filter`](verbs/filter.md) | keep items matching a condition | `grep`, but semantic |
| [`extend`](verbs/extend.md) | add extracted fields, keep the rest | your record, plus columns |
| [`embed`](verbs/embed.md) / [`top_k`](verbs/top-k.md) | vectors; rank by similarity | `sort \| head`, by meaning |
| [`reduce`](verbs/reduce.md) | synthesize many items into one | `awk` END, but literate |
| [`join`](verbs/join.md) | match two inputs semantically | SQL join, but semantic |
| [`cluster`](verbs/cluster.md) | group by meaning, label each group | themes with sizes and quotes |
| [`distinct`](verbs/distinct.md) | fold near-duplicates; `--exact` is free | `sort -u`, by meaning |
| [`diff`](verbs/diff.md) | what distinguishes two sets | the post-incident answer |
| [`outliers`](verbs/outliers.md) | the items least like the rest | novelty, surfaced |
| [`graph`](verbs/graph.md) | corpus → entity/relationship graph; `--fast` is free | the case wall, with citations |

**Free verbs** never call a model. Run them first to cut the corpus before any
paid stage:

| Verb | What it does | Feels like |
|---|---|---|
| [`where`](verbs/where.md) | filter on exact field predicates | SQL `WHERE` |
| [`summarize`](verbs/summarize.md) | count, average, percentiles, time buckets | SQL `GROUP BY` |
| [`sort`](verbs/sort.md) | order items by a field | `sort` |
| [`sample`](verbs/sample.md) | take a seeded random subset | `shuf` |
| [`getschema`](verbs/getschema.md) | list fields, types, and coverage | `head`, for structure |
| [`split`](verbs/split.md) | break items into pieces (pages, minutes) | `split` |
| [`chart`](reference/cli.md) | terminal bars, SVG/PNG, facets, time series | quick plots |

Some semantic verbs have a **conditionally free mode**: `join --on` (key
equality, no prompt), `distinct --exact` (hash-only folding), `graph --fast`
(local NER, on-device), `map`/`extend` `--dry-run` (compose without sending),
and `smartpipe schema` with a braces/DSL expression. Each stays at zero model
calls by construction.

## Concepts

- [The item](concepts/the-item.md) - the five laws everything follows from
- [Pipes & items](concepts/pipes-and-items.md) - the mental model (what is "one item"?)
- [Models & providers](concepts/models-and-providers.md) - local vs cloud, model strings
- [Structured output](concepts/structured-output.md) - braces and `--schema`
- [Output formats](concepts/output-formats.md) - `auto`, `json`, `csv`, `tsv`
- [File inputs](inputs/files.md) - point any verb at documents

## Recipes & reference

- [Cookbook](cookbook/README.md) - invoice reconciliation, video RAG, meeting
  digests, log triage, training-data prep, live monitoring, and more
- [CLI reference](reference/cli.md) - every flag, format, and exit code
- [`.sem` stage files](reference/sem-files.md) - save a pipe stage as an executable script
- [Troubleshooting](troubleshooting.md) - find your error message
- [Comparison](comparison.md) - where smartpipe fits among the alternatives
- [Privacy & security](privacy.md) - where data goes
## Background

smartpipe brings the semantic-operator vocabulary of data frameworks like DocETL
to Unix pipes, and adds file parsing, recursive chunking, and terminal-adaptive
output. See [the comparison](comparison.md) for the landscape.
