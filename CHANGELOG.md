# Changelog

All notable changes to sempipe are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Added
- **`--max-calls N` — a hard cost ceiling (D18).** Counts model calls (a repair
  re-ask counts; wire retries don't). Per-item verbs stop intake at the cap and
  drain gracefully (`note: stopped by --max-calls (N calls made)`); whole-set
  `top_k`/`reduce` treat mid-collection exhaustion as fatal with a fix screen —
  a partial collection is nothing usable. A capped run never exits 0. Also
  `SEMPIPE_MAX_CALLS` and the `.sem` key `max-calls`.
- **Mistral, first-class.** `--model mistral-large-latest` (or any of the
  family's bare prefixes — `ministral-*`, `codestral-*`, `magistral-*`,
  `devstral-*`, `pixtral-*`, `open-mistral-*`, `open-mixtral-*`) just works:
  routing, `MISTRAL_API_KEY`, `mistral-embed` embeddings, `pixtral-*` vision,
  structured output, and the same retry/`Retry-After` resilience — all on the
  built-in OpenAI-wire adapter, no extra install. `SEMPIPE_MISTRAL_BASE_URL`
  redirects it; `hf.co/mistralai/...` still routes to Ollama, untouched.
- **`.sem` stage files + `sempipe run`.** Save one verb invocation as TOML
  (`verb = "map"`, `prompt = …`, the verb's flags as keys) and execute it with
  `sempipe run stage.sem` — or add `#!/usr/bin/env -S sempipe run`, `chmod +x`,
  and pipe stages together like any other command. CLI flags override the
  file's values; `schema-file` resolves next to the script; unknown keys are
  loud errors (scripts run unattended — unlike config.toml's forward-compat
  ignore). Composition stays in the shell: one stage per file, by design.
  Docs: `docs/reference/sem-files.md`.
- **`--fields a,b` column projection** — select and order the columns of
  structured output, identically in NDJSON / terminal / CSV / TSV, on `map`,
  `embed`, `top_k`, and `reduce` (never `filter` — its output stays byte-faithful).
  A field the results don't carry keeps its place (null / empty cell) with a
  one-time stderr warning; on a plain-text run the flag is a clear usage error.
- **Shell completions** for bash, zsh, and fish (click's `_SEMPIPE_COMPLETE`
  machinery; setup one-liners in the install docs). `--model`, `--embed-model`,
  and `config model`/`config embed-model` complete with your configured model
  plus the locally installed Ollama models — probed with a 150 ms cap so `<TAB>`
  never hangs, and any probe failure just means no suggestions.
- **Log in with ChatGPT.** `sempipe auth login` (browser PKCE, or `--headless`
  device codes) lets ChatGPT Plus/Pro subscribers use OpenAI's Codex-family models
  without an API key. Tokens live in `~/.config/sempipe/auth.json` (0600,
  self-refreshing, `sempipe auth logout` removes them); an exported
  `OPENAI_API_KEY` always takes precedence. Embeddings still need a key or a
  local model — sempipe says so instead of failing cryptically.
- **Vision.** Images (`--in 'photos/*.jpg'`, or one redirected to stdin) flow to
  the chat model as images — describe them, or extract `{fields}` from them —
  across all three providers. Non-vision models skip the item with a hint; the
  other verbs point you to `map`.
- **Binary stdin.** `sempipe map "Summarize" < report.pdf` works: the bytes are
  sniffed, spooled, and parsed as one document item. Unrecognizable binary input
  stops with a clear screen instead of garbling.
- **Mixed input.** `--in` now composes with a pipe: files first (glob-sorted),
  then stdin lines, one run.
- **Streaming, flag-free.** `map`, `filter`, and `embed` now read stdin
  incrementally — `tail -f app.log | sempipe filter "…"` just works, with results
  flowing out as lines arrive and a count+rate status line (`· N matched` for
  filter). Finite-input behavior is byte-identical to 1.0.
- **`reduce --window N [--every M]`** — rolling synthesis over a stream: one
  reduce per window (tumbling, or sliding with `--every`), each emitted as
  `{"window_end": …, "result": …}`; the trailing partial window is flushed on
  Ctrl-C/EOF with `"partial": true`.
- **`top_k K --stream --near "…"`** — the live leaderboard: repaints the top-K
  block in place at a terminal; in a pipe, emits an NDJSON snapshot (a
  `{"_snapshot": n}` marker + K ranked records) whenever membership changes.
- **`sempipe cite`** — print a copy-paste BibTeX entry; `CITATION.cff` ships in the
  repo (GitHub's "Cite this repository" reads it) and in the sdist.
- **Unix death done right.** Downstream closing the pipe (`… | head`) now kills
  sempipe instantly and silently with the conventional exit 141 — never the BUG
  screen. The first Ctrl-C on `map`/`filter`/`embed` stops intake, drains in-flight
  work (10 s cap), emits completed results in order, prints a
  `done: interrupted — N processed · M skipped` summary, and exits with the run's
  true outcome code; a second Ctrl-C exits 130 immediately.

### Fixed
- **Doomed runs stop before the spend (D18).** A cloud 404 (model doesn't
  exist) or a schema the endpoint rejects now stops the whole run at the *first*
  occurrence with a fix screen — previously each item skipped individually,
  burning a paid call per input line. Five consecutive failures with zero
  successes also halt (a run that never worked was doomed from item 1); one
  success anywhere disarms that rule, so a working run with a bad patch of
  input still survives on the ordinary >50 % policy.
- **Optional-field schemas no longer 400 on OpenAI/Mistral.** sempipe claimed
  `strict: true` structured output unconditionally; strict mode rejects any
  schema whose fields aren't all required (with `additionalProperties: false`),
  so a `--schema` with optional fields skipped every item for the wrong reason.
  Strictness is now claimed only when the schema qualifies; either way, replies
  are validated client-side with one repair retry.
- **Config edits are atomic and lossless (except comments).** `sempipe config
  model …` now rewrites `config.toml` via a same-directory temp file +
  `os.replace` (a concurrent reader can never see a torn file), and keys it
  doesn't know survive the rewrite — an older sempipe no longer strips a newer
  one's settings. Comments still don't survive (tomli-w can't round-trip them;
  the CLI reference says so).
- **CJK-safe alignment.** `config show` columns and the terminal view's value
  truncation now measure display width (wide chars = 2 cells, combining marks
  = 0) instead of code points, so East-Asian values line up and never overshoot
  the terminal width.

### Changed
- **License: Apache-2.0** (from MIT), with a `NOTICE` file — matching the published
  repository. The `v1.0.0` tag points at the relicensed tree.
- **Embeddings travel in batches.** `embed` over a file corpus and `top_k`'s
  collection pass now send up to 64 texts per call (sequentially) instead of one
  — 64× fewer round-trips on batch inputs. A failed chunk re-runs item by item,
  so a single poison item skips alone; output order and NDJSON shape are
  byte-identical. Piped/streaming input stays per-item (latency beats
  throughput on a live stream). No new flags.
- **Rate limits back off exactly as asked.** A 429 carrying `Retry-After`
  (seconds or HTTP-date) now sleeps the server's number — no jitter, capped at a
  60 s abuse ceiling — instead of guessing with exponential backoff. Ollama and
  OpenAI-compatible endpoints; the Anthropic SDK already did this itself.
- **Startup stays fast, now enforced.** `sempipe --help` no longer imports
  `httpx` or `jsonschema` (they load only when a command actually runs);
  an `importtime`-based test gates it in CI and `make startup` reports the
  advisory wall-clock number (~64 ms median locally).

## [1.0.0] — 2026-07-05

**1.0.** The CLI surface — verbs, flags, output formats, exit codes, env vars — is now
a contract governed by SemVer. The five semantic verbs, local-first defaults, file
inputs, and adaptive output are complete and stable. Licensed under **Apache-2.0**.

### The whole thing, in one place
- **Five verbs as Unix pipe stages:** `map` (transform), `filter` (semantic grep),
  `embed` (vectors), `top_k` (similarity ranking), `reduce` (recursive synthesis) —
  plus `config`.
- **Local-first:** talks to a running Ollama by default; never silently calls a paid
  API. OpenAI-compatible and Anthropic (optional extra) supported; API keys read from
  the environment, never stored.
- **Text in, text out, Unix-native:** results to stdout, diagnostics to stderr;
  TTY-adaptive output (readable at a terminal, NDJSON when piped); order-preserving
  bounded concurrency; per-item failures skip with a warning; documented exit codes.
- **Structured output:** inline `{braces}` or `--schema`, with a one-shot repair.
- **File inputs:** `--in`/`--from-files`; documents parsed automatically.
- **Output formats:** `auto`, `text`, `json`, `csv`, `tsv`.
- **Docs:** install, quickstart, a page per verb, concept guides, a cookbook, a CLI
  reference, troubleshooting, a comparison, and a privacy/security note. A
  mkdocs-material site config ships in the repo.

### Quality
- ~400 tests, 98% coverage (100% on every pure `engine`, `verbs`, and `parsing`
  module); ruff + pyright-strict clean; no network calls in the test suite.

### Post-1.0 roadmap (tracked, not blocking)
- Streaming (`tail -f` per-line processing), `--fields` column projection, shell
  completions, the vision image path, and binary-stdin sniffing. See the technical
  plan's deferred-work ledger.

## [0.6.0] — 2026-07-05

For the spreadsheet people.

### Added
- **`--output csv` / `--output tsv`** — structured results as a real table. Columns
  are fixed by the schema order or the first record; missing values are empty cells;
  a surprise key is dropped with a one-time warning (a rectangle is the contract).
  Nested values become compact JSON in the cell, CSV follows RFC 4180 (quoting +
  CRLF), TSV strips embedded tabs/newlines, and a `_score` column (from `top_k`)
  sorts last. CSV/TSV require named columns — a plain-text prompt with `--output
  csv` is a clear usage error.
- Docs: `docs/concepts/output-formats.md`.

### Note
- `--fields` column projection, shell completions, and the startup-time budget gate
  (the rest of the output-ergonomics stage) are planned follow-ups.

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
