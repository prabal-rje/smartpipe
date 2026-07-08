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

Mixed streams are legal; `--strict-rows` (or `SMARTPIPE_STRICT_ROWS=1`) makes
them an error when your pipeline demands one kind.

## Named files

Files go after the prompt (quote globs). Each path's default:

- `.jsonl` / `.ndjson` → strict records, one per line; a bad row is a loud
  error naming file and line.
- everything else → one whole-file item: documents extract their text (and
  carry embedded figures to vision models), media carry their bytes.

`--from-files` reads *filenames* from stdin instead - compose with `find`,
`git ls-files`, anything that lists paths.

## The `--as` dial

`--as file|lines|jsonl` overrides every default, stdin included:

```bash
cat poem.txt | smartpipe map "translate, keep the shape" --as file
smartpipe map "translate" 'notes/*.txt' --as lines
smartpipe map "classify {label}" export.txt --as jsonl
```

An explicit `--as lines`/`jsonl` must hold for EVERY matched file: images
refuse (no finer granularity), audio/video point at `split --by
minutes/seconds`, documents point at `split --by pages` - with offender
counts, never silent partial application.

## Reader mode

`smartpipe PATH…` with no verb emits the crate's items as JSONL records -
ingestion made visible, and the front half of the
[write mirror](the-item.md#the-readwrite-mirror):

```bash
smartpipe report.pdf | head -1
smartpipe notes.txt --as lines | smartpipe write 'copy/{name}'
```

## What rides along

Every item carries its provenance in the `__source` spine field (path, cut
kind, position); media travel under `__media`. Both round-trip through any
number of pipe stages, which is what lets `write` reassemble chunks in order
at the far end.
