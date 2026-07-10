# split - break oversized items into chunks

`split` is the free verb: it turns items too big for a model's context window
into budget-sized chunk items, with **zero model calls**. It exists because a
300-page PDF is one item, and one item cannot fit an 8k
window without being split into smaller pieces.

```bash
smartpipe split '10k-filings/*.pdf' \
| smartpipe map "list the risk factors {risk}" \
| smartpipe reduce "merge into one deduplicated risk register"
```

## What comes out

One JSON record per chunk, provenance riding the `__source` spine
([the item](../concepts/the-item.md)):

```json
{"text": "…", "__source": {"path": "report.pdf", "as": "tokens", "segment": 3, "label": "report.pdf §3/12"}}
```

- Chunks break at **paragraph boundaries** first, then lines, then a hard cut -
  and the chunks of a document concatenate back to its exact text (nothing added, nothing lost).
- `__source` carries provenance: which document, how it was cut, which part -
  `smartpipe write` uses it to reassemble chunks in order.
- Items already under the budget pass through whole (one chunk, same spine).

## Units

`--by UNIT[:N]` picks what a chunk *is*:

| Unit | Example | What you get |
|---|---|---|
| `tokens` (default) | `--by tokens:2000` | text chunks at paragraph boundaries, `report.pdf §3/12` |
| `pages` | `--by pages:5` | PDF page groups with real page numbers, `report.pdf p.6-10` |
| `minutes` / `seconds` | `--by minutes:10` | **audio slices that stay audio** - each rides the pipe as a playable segment (`call.mp3 §00:10-00:20`), so the next verb can *hear* it natively |

```bash
smartpipe split --by minutes:10 call.wav \
| smartpipe map "what was agreed?" --model voxtral-mini-latest \
| smartpipe reduce "merge the agreements"
```

Notes: `--max-tokens N` is shorthand for `--by tokens:N`. `--by pages` reads PDF
files (DOCX has no fixed pages; the error says so). Audio slicing is native for
`wav`; other formats use the bundled `ffmpeg` (a static build ships in the box;
a PATH `ffmpeg` also works). Audio slices travel as base64 under the `__media` spine field, so segment
lines are large; expect larger output lines when slicing audio.

## `--media`: the images inside documents

`--media` extracts figures embedded in PDFs/DOCX/PPTX/XLSX as image items with
provenance (`report.pdf p.7 img.2`), byte-identical (decorative icons below a minimum size are skipped). Feed
them straight to a vision model:

```bash
smartpipe split --media 'decks/*.pptx' \
| smartpipe map "what does this chart claim? {claim}"
```

## Options

| Flag | Meaning |
|---|---|
| `--by UNIT[:N]` | the split unit (table above) |
| `--media` | extract embedded images as items instead (doesn't combine with `--by`) |
| `--max-tokens N` | shorthand for `--by tokens:N` |
| `--ocr-model TEXT` | Parse ingested PDFs/images with a document parsing model ([the role](../concepts/models-and-providers.md#the-ocr-model-role)) |
| `--max-calls N` | cap OCR parsing calls - the one way split ever calls a model |
| `FILES…`, `--from-files` | the usual [file inputs](../inputs/files.md) |

## When you need it

The other verbs handle overflow themselves, loudly: `map`/`extend` auto-chunk
and combine (with a disclosed plan; `--whole` restores the refusal), `filter`
and `join` judge chunk-by-chunk, `embed` and `top_k` mean-pool vectors - reach
for `split` when you want the chunking *visible* and the chunk results
*addressable*, e.g. to reduce them afterward.

## Scanned documents

split is free - zero model calls - UNLESS an [`ocr-model`](../concepts/models-and-providers.md#the-ocr-model-role) is
configured: then PDFs and images parse through it before cutting, disclosed
per row, and `--max-calls` caps that spend. `--by pages` keeps its exact page
grouping and provenance with the parsed pages. `--media` never consults the
role - it extracts embedded images, and there is no text to parse.
