# sempipe

**Semantic pipes for your terminal.** Verbs that understand — `map`, `filter`,
`top_k`, `reduce` — that put a language model into Unix pipelines. Text in, text out;
local-first; composes with everything you already use.

```console
$ cat reviews.txt | sempipe filter "the reviewer is sarcastic" | sempipe map "Extract {product, complaint}"
```

## Start here

- **[Install](install.md)** — one line, plus the optional extras.
- **[Quickstart](quickstart.md)** — from zero to a working pipeline in a minute,
  local or cloud.

## The verbs

| Verb | Does | Like |
|---|---|---|
| [`map`](verbs/map.md) | transform each item with a prompt | `sed`, but it understands |
| [`filter`](verbs/filter.md) | keep items matching a condition | `grep`, but semantic |
| [`embed`](verbs/embed.md) | items → vectors | plumbing for `top_k` |
| [`top_k`](verbs/top-k.md) | rank by similarity to a query | `sort \| head`, by meaning |
| [`reduce`](verbs/reduce.md) | synthesize many items into one | `awk` END, but literate |
| [`extend`](verbs/extend.md) | add extracted fields, keep the rest | your record, plus columns |
| [`join`](verbs/join.md) | match two inputs semantically | `join`, but it understands |
| [`cluster`](verbs/cluster.md) | group by meaning, label each group | themes with sizes and quotes |
| [`distinct`](verbs/distinct.md) | fold near-duplicates | `sort -u`, by meaning |
| [`diff`](verbs/diff.md) | what distinguishes two sets | the post-incident answer |
| [`outliers`](verbs/outliers.md) | the items least like the rest | novelty, surfaced |

**Free utilities** (never call a model): [`where`](verbs/where.md) ·
[`summarize`](verbs/summarize.md) · [`sort`](verbs/sort.md) ·
[`sample`](verbs/sample.md) · [`getschema`](verbs/getschema.md) ·
[`split`](verbs/split.md) · `chart` — deterministic prep and reporting
around the semantic core, so paid stages only see what matters.

## Concepts

- [Pipes & items](concepts/pipes-and-items.md) — the mental model (what's "one item"?)
- [Models & providers](concepts/models-and-providers.md) — local vs cloud, model strings
- [Structured output](concepts/structured-output.md) — braces and `--schema`
- [Output formats](concepts/output-formats.md) — auto, json, csv, tsv
- [File inputs](inputs/files.md) — point any verb at documents

## Recipes & reference

- [Cookbook](cookbook/README.md) — contract extraction, log triage, ranking documents, live monitoring, stream enrichment
- [CLI reference](reference/cli.md) — every flag, format, and exit code
- [`.sem` stage files](reference/sem-files.md) — save a pipe stage as an executable script
- [Troubleshooting](troubleshooting.md) — find your error message
- [How sempipe compares](comparison.md) — where it fits among the alternatives
- [Privacy & security](privacy.md) — local-first, no telemetry, no tool-use surface

## Why sempipe

It brings the semantic-operator vocabulary of data frameworks like DocETL to real
Unix pipes — local-first, with automatic file parsing, automatic recursive chunking,
and terminal-adaptive output. See [the comparison](comparison.md) for the honest map
of the landscape.
