# split — break oversized items into chunks

`split` is the free verb: it turns items too big for a model's context window
into budget-sized chunk items, with **zero model calls**. It exists because a
300-page PDF is one item, and no amount of cleverness makes one item fit an 8k
window without changing what you asked.

```console
$ sempipe split --in '10k-filings/*.pdf' \
    | sempipe map "list the risk factors {risk}" \
    | sempipe reduce "merge into one deduplicated risk register"
```

## What comes out

One JSON record per chunk:

```json
{"text": "…", "source": "report.pdf §3/12"}
```

- Chunks break at **paragraph boundaries** first, then lines, then a hard cut —
  and the chunks of a document concatenate back to its exact text (a
  property-based test pins this: nothing added, nothing lost).
- `source` carries provenance: which document, which part.
- Items already under the budget pass through whole, `source` unchanged.

## Options

| Flag | Meaning |
|---|---|
| `--max-tokens N` | chunk budget (default 2000 — comfortable for every wired provider) |
| `--in GLOB`, `--from-files` | the usual [file inputs](../inputs/files.md) |

## When you need it

The other verbs tell you. `map` refuses an over-window item with the exact
pipeline above (silently chunking would change what you asked); `filter`,
`embed`, and `top_k` handle oversized items automatically (chunk-judging and
vector mean-pooling) — reach for `split` when you want the chunking *visible*
and the chunk results *addressable*, e.g. to reduce them afterward.
