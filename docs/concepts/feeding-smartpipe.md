# Feeding smartpipe

Everything a pipe can eat, and exactly what each thing becomes. The five laws
live in [the item](the-item.md); this page is the ingestion mechanics.

## stdin

| You pipe in | It becomes |
|---|---|
| text lines | one text item per line (`as: lines`) |
| JSONL rows | one record per line (`as: jsonl`) - detected per line |
| a mixed stream | both, plus one census note: `input: 812 records · 3 plain lines` |
| ONE binary document (`< report.pdf`) | one whole-document item, spooled and extracted |
| an image / audio / video stream | one media item, bytes carried to the model |

Mixed streams are legal in an interactive pipe; `--strict-rows` (or
`SMARTPIPE_STRICT_ROWS=1`) makes them an error naming the first mixed row,
and `.sem` runs are strict by default (unattended = loud) - the full story is
[the granularity ladder](granularity.md).

## Named files

Files go after the prompt (quote globs). Each path's default:

- `.jsonl` / `.ndjson` → strict records, one per line; a bad row is a loud
  error naming file and line.
- `.csv` / `.tsv` → header-named records, one per row (`.tsv` cuts on tabs);
  see [csv rows](#csv-rows) below.
- everything else → one whole-file item: documents extract their text (and
  carry embedded figures to vision models), media carry their bytes.

`--from-files` reads *filenames* from stdin instead - compose with `find`,
`git ls-files`, anything that lists paths.

## The `--as` dial

`--as file|lines|jsonl|csv` overrides every default, stdin included:

```bash
cat poem.txt | smartpipe map "translate, keep the shape" --as file
smartpipe map "translate" 'notes/*.txt' --as lines
smartpipe map "classify {label}" export.txt --as jsonl
cat export.txt | smartpipe map "classify {label}" --as csv
```

An explicit `--as lines`/`jsonl`/`csv` must hold for EVERY matched file:
images refuse (no finer granularity), audio/video point at `split --by
minutes/seconds`, documents point at `split --by pages` - with offender
counts, never silent partial application.

## csv rows

`--as csv` (the default for `.csv`/`.tsv` paths) reads the header row as the
field names and every later row as one record - `name,age` + `alice,31`
becomes `{"name": "alice", "age": 31}`. On stdin the header is simply the
first line. Rows stream one at a time, exactly like jsonl - a 10 GB export
never materializes in memory.

The details, all deliberate:

- **Cell coercion is int → float → string.** A cell that is a whole number
  becomes an int (`31`), a decimal or scientific number becomes a float
  (`2.5`, `1e3`), and anything else - empty cells included - stays the string
  it was (`007x` keeps its leading zeros). `NaN`/`Infinity` spellings stay
  strings; they have no JSON form.
- **`__source` carries the PHYSICAL line number.** The header is line 1, the
  first data row line 2 - so `grep -n`/`sed -n` line references match the
  file exactly. A quoted cell may span lines; such a row carries its FIRST
  physical line.
- **Dialect by extension.** `.tsv` cuts on tabs, with or without an explicit
  `--as csv`; everything else - stdin included - cuts on commas. There is no
  delimiter sniffing beyond that: extensions are predictable, sniffers guess.
- **Errors are loud, jsonl-style.** A ragged row names the file, the physical
  line, and both column counts; an empty file (or blank header cell, or
  duplicate column name) refuses with the fix. `--as csv` on media or
  documents refuses with the same signposts as `lines`/`jsonl`.

Egress is the mirror you already have: `--output csv` on the verbs that emit
records, so `csv in → csv out` round-trips.

## Reader mode

`smartpipe PATH…` with no verb emits the crate's items as JSONL records -
ingestion made visible, and the front half of the
[write mirror](the-item.md#the-readwrite-mirror):

```bash
smartpipe report.pdf | head -1
smartpipe notes.txt --as lines | smartpipe write 'copy/{name}'
# {name} = the source file's name, carried by __source - notes.txt becomes copy/notes.txt
```

## What rides along

Every item carries its provenance in the `__source` spine field (path, cut
kind, position); media travel under `__media`. Both round-trip through any
number of pipe stages, which is what lets `write` reassemble chunks in order
at the far end.

## When it doesn't fit

No item is silently truncated, and none is refused just for being big -
an item past the model's window is HANDLED, loudly, per verb:

| Verb | An item past the model's window |
|---|---|
| `map` / `extend` | auto-chunks: the same prompt runs per chunk, then ONE synthesis call combines the partial answers (with braces or `--schema`, one merge call folds the partial extractions into one record) |
| `filter` | judged chunk by chunk - ANY matching chunk keeps the whole item, byte-verbatim; stops at the first match |
| `join` | an oversized side is chunk-embedded for blocking; the judge reads its chunks best-first with the same any-match rule |
| `reduce` | never blows: the recursive tree chunks, condenses, and recurses by design |
| the embedding verbs (`embed`, `top-k`, `distinct`, `cluster`, `outliers`, `sort`, `diff`) | chunk-embedded and mean-pooled into one vector - never blows |

Disclosure comes BEFORE spend - one note per oversized row names the plan:

```
note: report.pdf ~48,200 tokens over budget - 7 chunks + 1 combine call
```

and every chunk call is metered in the receipt and counted by `--max-calls`.

Two refinements keep the arithmetic honest:

- **The estimate is media-aware.** Images (priced from their real header
  dimensions), audio and video (priced per second) spend context too, and
  CJK text counts roughly one token per character instead of one per four.
- **The wire gets the last word.** If a provider still rejects a
  machine-cut chunk with a context-length error, that chunk re-splits in
  half and retries (bounded depth, disclosed:
  `chunk re-split: provider rejected the estimate`). Items YOU cut
  (`as: file|lines|jsonl`) are never re-cut - a rejection there stays a
  per-item error.

Reproducibility purists can opt out: `--whole` on `map`/`extend`/`filter`/
`join` restores the refusal - process whole or skip with an error naming the
`split` recipe.
