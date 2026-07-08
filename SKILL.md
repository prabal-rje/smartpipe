---
name: smartpipe
description: Drive smartpipe - a Unix CLI whose pipe verbs call LLMs. Use when a task needs semantic operations (extract, filter, dedupe, cluster, join, summarize, search) over text, JSONL, PDFs, images, audio, or video from a shell.
---

# Operating smartpipe (for AI agents)

smartpipe is a semantic-pipes CLI: Unix filters whose verbs call language
models. Everything composes with ordinary shell tools. This file is the
operating manual for agents driving it programmatically.

## The one law

Everything in a pipe is a record (JSONL when piped; pretty blocks only at a
human TTY). Plain text lines are records in disguise. Files become records
at ingestion. Tool metadata rides in reserved `__` fields (`__source`,
`__media`, `__score`, `__invalid`) - never write your own `__` fields.

## Reading input

```console
smartpipe report.pdf | ...              # a path as first arg = reader mode, whole file = one item
smartpipe map "..." docs/*.pdf          # verbs take positional FILES (globs: quote them for >1 dir)
smartpipe map "..." notes.txt --as lines   # granularity dial: file | lines | jsonl
cat rows.jsonl | smartpipe where '...'  # stdin: JSON-object lines become records, others text
```

- `--as jsonl` = strict rows (non-JSON line is a loud error). `--as lines` =
  everything is text. `--as file` on stdin slurps it whole.
- `.jsonl` files default to rows; every other extension defaults to
  whole-document. Media (pdf/png/mp3/mp4) is ingested natively - models see
  images, hear audio, watch video (or the tool converts, disclosed per row).

## Writing output

- stdout when piped is machine JSONL - parse it directly; never parse stderr
  (all diagnostics, notes, receipts live there).
- `... | smartpipe write 'out/{name}'` routes items back to FILES (egress
  mirrors ingestion: line-cut items reassemble in order). Emits written paths.
- `... | smartpipe readable` renders human-readable blocks (for reports/less).
- `--bare` strips `__` metadata from JSONL output. `--output json|csv|tsv`
  forces format.

## Cost discipline (matters: verbs spend real money)

- FREE verbs (no model, ever): `where`, `summarize`, `sort`, `sample`,
  `getschema`, `split`, `chart`, `schema`. Free rungs: `join --on` (key
  equality), `distinct --exact` (hash dedupe), `--dry-run` on map/extend.
- Paid: `map`, `extend`, `filter`, `reduce`, `join` (judged), `cluster`,
  `distinct`, `diff`, `outliers`, `embed`/`top_k` (embedding-priced).
- ALWAYS cut free-first: `where 'text has "ERROR"' | filter "real outage"`.
- Belt every exploratory run: `--max-calls N`. Rehearse prompts on
  `sample 20` (seeded, deterministic) before full runs.
- Results cache: rerunning an identical run is free and cache hits don't
  count against `--max-calls`. The receipt (stderr, end of run) reports
  tokens/media actually spent.

## Structured extraction

```console
smartpipe extend "Add {label enum(bug, praise), urgency number: 0 to 1, tags string[]}" --as jsonl data.jsonl
```

- Braces carry names, types, AND descriptions: `{vendor string: legal name}`.
  Types: string, number, integer, boolean, enum(a, b), string[], number[],
  integer[], boolean[]; any type + `?` = nullable. Bare fields = any scalar
  or scalar list, never null.
- QUOTE the whole prompt (braces are shell-hostile). Use double quotes
  outside, single inside, or escape.
- `smartpipe schema '{...}'` compiles braces to JSON Schema free;
  `--check file.jsonl` validates rows (exit 0/1); `--example` prints a
  sample instance. Iterate there before spending.
- `--keep-invalid` keeps model failures as `{"__invalid": true, "__raw": ...}`
  rows instead of skips - use it for robust batch jobs, then route with
  `where`.
- `extend` merges fields onto records (existing fields survive; same-name
  overwrites). `map` replaces: records in -> `{"result": ..., "__source": ...}`.

## Reliability

- Exit codes: 0 ok · 1 partial (some skips) · 2 setup fault · 3 all failed ·
  64 usage. Skips are disclosed on stderr per row.
- Oversized items are currently REFUSED per item with a note telling you to
  `split` first (auto-chunking is landing; check `--help`). Big docs:
  `smartpipe split --by pages:10 big.pdf | smartpipe map ... | smartpipe reduce ...`.
- 5 consecutive transport failures stop the run (circuit breaker); a
  configured `fallback-model` switches the rest of the run instead.
- Models: `--model REF` per run, `SMARTPIPE_MODEL` env, or `smartpipe config`.
  API keys are env-only (`OPENAI_API_KEY`, `GEMINI_API_KEY`, ...). `smartpipe
  doctor` diagnoses setup; `doctor --probe` tests real modality support
  (4 tiny paid calls).

## Recipes (composable one-liners)

```console
# extract fields from scanned invoices, reconcile against a ledger
smartpipe map "Extract {vendor string, total number}" invoices/*.pdf \
| smartpipe join "the same payment" --right ledger.jsonl --kind anti > missing.jsonl

# semantic search over a folder of recordings (index once, query free)
smartpipe embed 'sessions/**/*.mp4' > lib.embeddings
smartpipe top_k 3 --near "user hits the checkout bug" < lib.embeddings

# dedupe a training corpus, judge quality, keep the good rows
smartpipe distinct --as jsonl corpus.jsonl \
| smartpipe extend "Add {quality number: 0 to 1}" --tally quality --max-calls 50000 \
| smartpipe where 'quality >= 0.7' > clean.jsonl
```

Full docs: https://prabal-rje.github.io/smartpipe (Learn track for concepts,
Reference for every flag). Install: `pip install smartpipe-cli`.
