# Ingestion — getting data into the pipe

Load when: reading files, folders, globs, stdin, or media into smartpipe.
Parent: [SKILL.md](../../SKILL.md) · Siblings: [extraction](extraction.md) · [output](output.md)

## The three doors in

```console
smartpipe report.pdf | ...                 # binary-as-reader: paths stream items
smartpipe map "summarize" docs/*.pdf       # verbs take positional FILES
cat rows.jsonl | smartpipe where '...'     # stdin: JSON-object lines = records
```

Quote globs that cross directories (`'docs/**/*.pdf'`) so smartpipe expands
them (sorted, deduped, no-match = loud error). A file named like a verb:
`smartpipe ./map`.

## The granularity dial: --as

| Value | Meaning | Default for |
|---|---|---|
| `file` | whole thing = ONE item | every named path except .jsonl; works on stdin too (slurps) |
| `lines` | each line = a TEXT item (even JSON-looking lines) | — |
| `jsonl` | each line MUST be a JSON object (loud error otherwise) | `.jsonl`/`.ndjson` paths |
| (unset on stdin) | per-line sniff: JSON objects become records, rest text | stdin |

- Strict dataset work: always say `--as jsonl` — it doubles as row validation.
- `--as lines` is the "treat JSON as dumb text" escape.
- Mixed streams are legal; a census note reports them; `--strict-rows` errors instead.

## Media: ingested natively, one item per file

| Type | The item carries | Models that can't take it |
|---|---|---|
| PDF | extracted text + embedded figures | figures dropped, disclosed per row |
| image | pixels (`__media`) | captioned via a chat model (paid, consent-gated) |
| audio | the waveform | transcribed (whisper built in / configured STT) |
| video | watched whole on gemini | frames+track elsewhere; `--frame-every`, `--max-frames` |

`--as lines/jsonl` on media = usage error pointing at `split`. Finer media
granularity IS split: `split --by pages:10 big.pdf`, `--by minutes:10 call.mp3`,
`--by seconds:60 demo.mp4` (segments stay real media, provenance rides).

## Oversized items (bigger than the model's window)

HANDLED automatically, loudly: `map`/`extend` chunk + combine, `filter`/`join`
judge chunk-by-chunk (any match keeps the item), embedding verbs mean-pool.
One stderr note per oversized row discloses the plan BEFORE spend
(`~48,200 tokens over budget - 7 chunks + 1 combine call`) and every chunk
call counts against `--max-calls`. `--whole` restores per-item refusal.
Want the chunks visible and addressable instead? Split first:

```console
smartpipe split --by pages:10 big.pdf | smartpipe map "..." | smartpipe reduce "..."
```

## Provenance

Every item carries `__source` (path, how it was cut, line/page/segment).
Never fabricate or edit `__` fields; they drive [output](output.md) routing.
