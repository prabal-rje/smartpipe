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

## Units

`--by UNIT[:N]` picks what a chunk *is*:

| Unit | Example | What you get |
|---|---|---|
| `tokens` (default) | `--by tokens:2000` | text chunks at paragraph boundaries, `report.pdf §3/12` |
| `pages` | `--by pages:5` | PDF page groups with real page numbers, `report.pdf p.6-10` |
| `minutes` / `seconds` | `--by minutes:10` | **audio slices that stay audio** — each rides the pipe as a playable segment (`call.mp3 §00:10-00:20`), so the next verb can *hear* it natively |

```console
$ sempipe split --by minutes:10 --in call.wav \
    | sempipe map "what was agreed?" --model voxtral-mini-latest \
    | sempipe reduce "merge the agreements"
```

Notes: `--max-tokens N` is shorthand for `--by tokens:N`. `--by pages` reads PDF
files (DOCX has no fixed pages; the error says so). Audio slicing is native for
wav; other formats need `ffmpeg` on PATH. Audio slices travel as base64 inside
the NDJSON records, so segment lines are large; that's the cost of a pipe that
carries sound.

## Options

| Flag | Meaning |
|---|---|
| `--by UNIT[:N]` | the split unit (table above) |
| `--max-tokens N` | shorthand for `--by tokens:N` |
| `--in GLOB`, `--from-files` | the usual [file inputs](../inputs/files.md) |

## When you need it

The other verbs tell you. `map` refuses an over-window item with the exact
pipeline above (silently chunking would change what you asked); `filter`,
`embed`, and `top_k` handle oversized items automatically (chunk-judging and
vector mean-pooling) — reach for `split` when you want the chunking *visible*
and the chunk results *addressable*, e.g. to reduce them afterward.
