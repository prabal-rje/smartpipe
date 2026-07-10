# The granularity ladder

Every pipe starts with one decision: **what is one item?** This page is that
decision, top to bottom - when each cut applies, how streams end up mixed,
and what the census and `strict-rows` do about it.

## The ladder

From coarsest to finest:

| Rung | One item is | You get it from |
|---|---|---|
| **file** | a whole file (its extracted text + media) | naming a path, `--as file`, redirecting one document (`< report.pdf`) |
| **split** | a piece of a file: a page, N tokens, N minutes/seconds | `smartpipe split --by pages/tokens/minutes/seconds` |
| **lines / rows** | one text line, one JSONL record, one CSV row | piping text in, `.jsonl`/`.csv` paths, `--as lines/jsonl/csv` |

Three rules pick the rung when you don't say:

1. **Paths decide the default.** `.jsonl`/`.ndjson` cut into strict records,
   `.csv`/`.tsv` into header-named rows; every other path is one whole-file
   item.
2. **stdin sniffs per line.** A line that parses as a JSON object is a
   record; anything else is a text line. One redirected binary document is a
   single whole-file item.
3. **`--as` overrides everything.** `--as file` slurps (even stdin);
   `--as lines` keeps every line text, even lines that look like JSON;
   `--as jsonl` demands one record per line, loudly; `--as csv` reads the
   first line as the header.

`split` sits between file and lines: it cuts *below* the file into items
that remember how they were cut (each chunk carries a `__source` spine, so
`write` can reassemble them in order at the far end).

## When each cut applies

- **file** - the unit of meaning is the document: summarize a report,
  caption an image, transcribe a call.
- **split** - one document is too big, or you want the pieces addressable
  ("page 3 says..."). Media clips cut by minutes/seconds; documents by pages
  or tokens.
- **lines/rows** - the unit of meaning is the row: logs, exports, JSONL
  datasets. This is where per-item verbs earn their keep.

## How mixing happens

Auto mode sniffs stdin per line, so a stream can come out **mixed** - some
records, some plain text. It happens innocently:

- a log file where some lines are JSON events and some are free text,
- `cat a.jsonl b.txt` gluing two kinds together,
- a producer that writes headers, blank-ish banners, or progress lines
  between records.

Mixing is legal in an interactive pipe. But a verb given both kinds treats
them differently (a record has fields; a text line only has `text`), so
silence would lie. Hence the census.

## The census note

At end of stream, a mixed pipe gets exactly one stderr note:

```
note: input: 812 records · 3 plain lines
```

Nothing fails; the note tells you the stream wasn't what you probably
thought. Declare a kind to make it go away: `--as jsonl` (records) or
`--as lines` (text).

## strict-rows

When your pipeline *demands* one kind, make mixing an error:

- `--strict-rows` on the verb, or
- `SMARTPIPE_STRICT_ROWS=1` in the environment.

Strict mode fails at the **first** mixed row, naming it - before the verb
sees it, before any model call could spend on it:

```
error: input: line 4 is a plain text line in a record stream
  --strict-rows demands one kind - declare it: --as jsonl (records) or --as lines (text).
```

Strictness also covers field misses in the free verbs: `where` errors when
rows have no fields for a field predicate to read, `summarize` when rows
lack the `by` field.

### `.sem` runs are strict by default

A script runs unattended, and unattended means loud: `smartpipe run x.sem`
behaves as if `--strict-rows` were set, for single-stage files and every
pipeline stage alike. Mixed-by-design pipelines opt out in the file:

```toml
verb = "split"
strict-rows = false
```

(per stage or top-level in pipeline files; an explicit CLI flag or
environment variable still wins). Interactive pipes keep the permissive
census note. The details live in
[.sem files - strict rows by default](../reference/sem-files.md#strict-rows-by-default).

## See also

- [The item](the-item.md) - the five laws behind all of this
- [Feeding smartpipe](feeding-smartpipe.md) - the ingestion mechanics table
- [split](../verbs/split.md) - cutting below the file
