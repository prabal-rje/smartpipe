# Changelog

All notable changes to sempipe are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Fixed
- **CI on Python 3.11 and macOS runners.** The signals-test harness closed the
  child's stdin and later called `communicate()`, which 3.11 rejects
  (`ValueError`, fixed in 3.12+); the OAuth callback test depended on the
  host's `localhost` resolution order. Both the harness and the callback
  server now use the explicit v4 loopback.

### Changed
- **Audio transcription is now local (faster-whisper).** The `[audio]` extra
  swaps Google's Web Speech API for a local Whisper model (`tiny` by default,
  `SEMPIPE_WHISPER_MODEL=small|medium|large-v3` to trade speed for accuracy;
  first use downloads the weights once). Audio bytes never leave the machine
  on the fallback path. Verified end to end on synthesized speech.
- **Honest audio-transcription disclosure.** The `[audio]` extra's transcriber
  (markitdown → SpeechRecognition) sends audio to **Google's Web Speech API**,
  not a local model — the one-time note, `docs/inputs/files.md`,
  troubleshooting, and the privacy page now say exactly that. Native audio
  models remain the private path (audio rides your configured endpoint only).
- Docs: recipe for exporting **Pydantic / Zod** models to `--schema` files
  (JSON Schema is the interchange; no plugin, one line each).

## [1.1.0] — 2026-07-05

**The everything release.** Two design-and-build trains shipped as one: the
post-1.0 plan (streaming, resilience, inputs, ergonomics, `.sem` files) and the
post-1.1 plan (cost guardrails, the `join` verb, multimodality under the D20
constitution, the schema ladder, five providers). Every bullet below was
validated by gates AND — where a wire exists — by live calls against real
endpoints, which caught and fixed four real bugs along the way.

### Added
- **Prompts from files (D23).** `sempipe map @prompt.md` reads the prompt from
  a file (`--prompt-file FILE` is the explicit form; the `.sem` key
  `prompt-file` resolves beside the script). Only a *leading* `@` is special
  (`@@` escapes it; `$file` was rejected — the shell eats it silently, the
  exact failure class this release hunts). A missing file is a loud exit 64:
  `error: prompt file not found: prompt.md`. Braces inside the file are live
  grammar — version your prompts in git.
- **Gemini and OpenRouter, first-class.** `--model gemini-2.5-flash` (bare
  `gemini-*` routes; Google's OpenAI-compat endpoint, live-verified) and
  `--model openrouter/vendor/model` (explicit-only — OpenRouter names are other
  vendors' names, so bare prefixes never hijack) — chat, structured output,
  embeddings where the endpoint carries them, the same retry/Retry-After
  resilience, `GEMINI_API_KEY`/`OPENROUTER_API_KEY`, per-wire base-URL env
  vars. No attribution headers are sent to OpenRouter (telemetry-adjacent,
  D24). And the auth surface stays small, deliberately: no `--auth` cascade
  knob, no `--api-key`/`--base-url` argv (leaks into `ps`/history) — the
  documented override is the environment itself.
- **The schema-authoring ladder (D22).** Three new rungs between braces and a
  hand-written file: `{vendor: the supplier name}` brace *descriptions* (plain
  English guidance riding the synthesized schema, map only);
  `--schema-from "vendor string; total number >= 0; status enum(paid, unpaid)"`
  — a deterministic DSL parsed with zero model calls, typos fail free at argv
  time; and `sempipe schema "an invoice with …" > invoice.json` — a drafted
  schema file (one call + one repair, meta-validated; a failed draft exits 3
  with **empty stdout**, so a broken schema can never slip into a pipe).
  Braces never grow type syntax — descriptions in braces, types in the DSL.
- **Native audio Q&A (D20).** `sempipe map "what does the caller want?" --in
  'calls/*.wav'` sends the *sound itself* to models that can hear
  (`gpt-4o-audio-preview`-family, Voxtral — wav/mp3 as `input_audio` parts,
  byte-verified); models that can't trigger a transcription fallback when the
  `[audio]` extra is installed (with a one-time note), else a skip naming both
  fixes. Text verbs (`filter`, `embed`, `top_k`, `reduce`, `join`) transcribe
  on demand instead of eagerly at read time. All of it lands on the one media
  union (`Item.media`), so vision behavior is byte-identical — and the whole
  audio diff came in *smaller* than vision's, as the constitution demands.
- **`sempipe join` — the sixth semantic verb (D21).** Match stdin against a
  second input wherever a plain-English predicate holds:
  `join "{left.text} concerns {right.name}" --right products.jsonl`. Embed →
  block → judge keeps cost at lines×k (default k=5) instead of lines×catalog;
  the right side is indexed up front so a bad file costs zero chat calls; a
  TTY cost preview appears before big runs; the left side streams flag-free
  (live enrichment). Output nests `{"left", "right", "_score"}` per matched
  pair; `--fields` grows dotted paths. `make join-eval` publishes the recall@k
  table that justifies the default, and the docs teach the `--k 20` spot-check.
- **`sempipe doctor`** — every no-cost setup check on one screen: config parses,
  Ollama reachable, configured chat/embed models installed, API keys present
  (never printed, never validated — validation costs a call), ChatGPT login,
  optional extras, shell completions. Each ✗ carries its fix; exit 0 all-green,
  1 otherwise. A broken config is a reported line, not a crash.
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
- **Process, promoted to artifacts.** `RELEASING.md` (machine gate + a
  ten-command human UX walkthrough), `MAINTENANCE.md` (weekly live-Ollama CI
  smoke — scheduled, allowed-failure — monthly dependency/security pass,
  quarterly comparison re-read, and the live-smoke log), `make docs-check`
  (internal link scan + strict site build, now in CI), `make live-smoke`
  (owner-run, env-gated, cost-capped provider matrix — first full run: 10/10
  cells green), and `make join-eval` (the recall table behind join's default).
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
