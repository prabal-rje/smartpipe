# Ingestion - getting data into the pipe

Load when: reading files, folders, globs, stdin, CSV/TSV, or media into smartpipe.
Parent: [SKILL.md](../../SKILL.md) · Siblings: [extraction](extraction.md) · [output](output.md)

## Three ways in

```console
smartpipe report.pdf | ...                    # a path as first arg: file(s) stream in as items
smartpipe map "Summarize" docs/*.pdf          # verbs take positional FILES after the prompt
cat rows.jsonl | smartpipe where 'total > 0'  # stdin
```

- Quote globs that cross directories (`'docs/**/*.pdf'`) so smartpipe expands them: sorted, deduped, no-match = loud error.
- A file literally named like a verb: `smartpipe ./map`.
- Some verbs read stdin ONLY - no positional files: `where`, `sample`, `sort`, `summarize`, `getschema`, `chart`, `diff`, `readable`, `write`.
  - WRONG: `smartpipe where 'level == "error"' events.jsonl` → exit 64, `error: Got unexpected extra argument (events.jsonl)`
  - RIGHT: `smartpipe where 'level == "error"' < events.jsonl`

## How input becomes items: --as

| `--as` | Each item is | Auto-default for |
|---|---|---|
| `file` | one whole file (on stdin: all of it, slurped) | every named path except `.jsonl`/`.csv`/`.tsv` |
| `lines` | one line of text (even if the line looks like JSON) | - |
| `jsonl` | one JSON object per line; anything else = loud error | `.jsonl` / `.ndjson` |
| `csv` | one row as a record; the header row names the fields | `.csv` (comma) / `.tsv` (tab) |
| unset, on stdin | per-line sniff: JSON-object lines become records, the rest is text | stdin |

- Strict dataset work: always say `--as jsonl` - it doubles as row validation.
- `--as lines` is the "treat JSON as dumb text" escape.
- CSV cells auto-coerce int → float → string; quoted multi-line cells work.
- A ragged CSV row fails loudly, naming file and line:
  `error: --as csv: ragged.csv line 3 has 2 columns, expected 3`
- Mixed record/text streams are legal; a stderr census notes them; `--strict-rows` errors instead.

What one ingested CSV row looks like:

```console
smartpipe contacts.csv
```
```
{"name":"Ada","email":"ada@example.com","age":36,"__source":{"path":"contacts.csv","as":"csv","line":2}}
```

## __source - provenance you can use

- Every item carries `__source`: the `path`, how it was cut (`as`), and its position (`line`, `page`, or `segment`).
- Use it: cite results as file + line ("contacts.csv line 2"), group by `__source.path`, trace a bad row to its origin.
- stdin items show `"path": "-"`.
- Never fabricate or edit `__` fields; they also drive [output](output.md) routing. Want them gone? `--bare`.

## Media: ingested natively, one item per file

| Type | The item carries | When the model can't take it |
|---|---|---|
| PDF | extracted text + embedded figures | figures dropped, disclosed per row |
| image | pixels (`__media`) | captioned via a chat model (paid, consent-gated `--allow-captions`) |
| audio | the waveform | transcribed (built-in whisper / configured STT) |
| video | watched whole on gemini | frames + audio track elsewhere; `--frame-every`, `--max-frames` |

- `--as lines`/`--as jsonl` on media = usage error (an image has no lines). Finer cuts = `split`:

```console
smartpipe split --by pages:10 big.pdf | ...
smartpipe split --by minutes:10 call.mp3 | ...
smartpipe split --by seconds:60 demo.mp4 | ...
```

- Segments stay real media; `__source` gains `segment` plus a citable `label` (`big.pdf §3/12`).

## Items bigger than the model window

- Handled automatically, loudly: `map`/`extend` chunk + combine; `filter`/`join` judge chunk-by-chunk (any match keeps the item); embedding verbs mean-pool.
- One stderr note per oversized row discloses the plan BEFORE spend (`~48,200 tokens over budget - 7 chunks + 1 combine call`).
- Every chunk call counts against `--max-calls`.
- `--whole` disables chunking: process whole or skip with an error.
- Want chunks visible and addressable instead? Split first:

```console
smartpipe split --by pages:10 big.pdf | smartpipe map "Summarize {summary string}" | smartpipe reduce "Write the executive summary"
```
