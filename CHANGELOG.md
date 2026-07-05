# Changelog

All notable changes to sempipe are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

## [0.5.0] — 2026-07-05

Documents become items — point any verb at files.

### Added
- **File inputs** — `--in 'reports/*.pdf'` reads each matching file as one item;
  `--from-files` treats each stdin line as a filename. Works with every verb.
- **Automatic parsing** — you never name a parser. Text files read directly; PDF,
  DOCX, PPTX, XLSX, HTML, and EPUB extract via the optional `sempipe[files]` extra;
  detection is by extension with a magic-byte fallback. Unreadable, unparseable, or
  unsupported files are skipped with a warning — a bad file never crashes the run.
  Missing an extra shows one-time install guidance, then skips those files.
- **File-mode output for `filter`/`top_k`** — they emit the matching/ranked
  **filenames** (with the score, for `top_k`), so filtering or ranking a folder of
  documents returns paths you can pipe onward.
- Docs: `docs/inputs/files.md`; extras table and pipes-and-items updated.

### Not yet
- Describing images with a vision model, and reading a single binary file from
  stdin (`sempipe map … < report.pdf`), are planned for a following release.

## [0.4.0] — 2026-07-05

The last of the five verbs — sempipe's full vocabulary now works end to end.

### Added
- **`sempipe reduce`** — synthesize all input items into one result. When the input
  exceeds the model's context, sempipe **automatically** chunks it, summarizes each
  chunk into dense notes, and recurses on the notes — no flags, no strategy to pick.
  `--group-by FIELD` produces one result per group (with `{field}` naming the
  group's value in the prompt); `--schema` validates the final result (same one-shot
  repair as `map`); `--verbose` prints the chunking tree. A chunk that fails is
  skipped with a warning (exit 1) rather than aborting the whole reduction.
- Docs: `reduce`.

## [0.3.0] — 2026-07-05

### Added
- **`sempipe embed`** — convert each item to a vector embedding, emitted as NDJSON
  (`{text, vector, source}`). Uses the embedding model (never a chat model);
  redirect to a file to reuse. `--embed-model`, `--concurrency`.
- **`sempipe top_k`** — rank items by cosine similarity to `--near`, keeping the
  top `K` and/or everything above `--threshold` (each result gains a `_score`).
  Reuses a precomputed `vector` from an `embed` record instead of re-embedding, so
  a corpus can be embedded once and queried many times. A query/corpus embedding
  dimension mismatch stops with a clear message. Spellings `top-k`/`topk` also work.
- Docs: `embed`, `top_k`.

## [0.2.0] — 2026-07-05

### Added
- **`sempipe filter`** — semantic grep. Keeps the items that match a plain-English
  condition, byte-for-byte unchanged and in input order (a strict subset of the
  input). `--not` inverts, like `grep -v`. `{field}` references pull values out of
  JSON Lines input (`"{priority} is wrong given {description}"`); comma-groups are
  rejected as map-only, and a field reference on non-JSON input fails fast with a
  clear message. An unparseable verdict is repaired once before the item is skipped.
  Zero matches is a successful (exit 0) empty result.
- Docs: `filter`, and a new "pipes & items" concept page.

## [0.1.0] — 2026-07-05

The first release: `sempipe map` works end to end, local-first.

### Added
- **`sempipe map`** — transform each input item with a prompt. Plain-text mode
  (one line in, one line out) and structured mode: put `{field}` names in the
  prompt (or pass `--schema file.json`) to get validated JSON back. A reply that
  fails schema validation is repaired once (re-asking the model with the error)
  before the item is skipped. `--model`, `--output`, `--concurrency` flags.
- **`sempipe config`** — interactive first-run setup, plus `config show` (effective
  settings with their origin), `config model`, and `config embed-model`.
- **Local-first models** — talks to a running Ollama by default (with model
  autodetection); any OpenAI-compatible endpoint via `--model`/`SEMPIPE_OPENAI_BASE_URL`;
  Claude via the optional `sempipe[anthropic]` extra. API keys are read from the
  environment, never stored.
- **Unix-native behavior** — results to stdout, diagnostics to stderr; TTY-adaptive
  output (human-readable at a terminal, NDJSON when piped); order-preserving
  bounded-concurrency execution; per-item failures skip with a warning instead of
  crashing; documented exit codes; a batch progress spinner (suppressed off-TTY).
- Docs: quickstart, install, `map`, models-and-providers, structured-output.

### Not yet
- `filter`, `embed`, `top_k`, `reduce`, file inputs, and streaming land in the
  following releases. The architecture for all of them is already in place.
