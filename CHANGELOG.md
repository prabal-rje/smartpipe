# Changelog

All notable changes to smartpipe are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Added
- **Video frame control (D43).** `map`/`extend` gain `--frame-every SECONDS`
  (a density guarantee: one frame per period, lifting the default 24-frame
  cap) and `--max-frames N` (a budget: the smaller of the two wins). Defaults
  unchanged — 1 fps up to 24, evenly spread past that — so nobody's costs
  move silently; the per-row note and the run receipt make dense choices
  visible.
- **The face got fixed after owner review (D42b).** The box-drawing banner
  (which rendered gappy in real terminals) became a proper ASCII wordmark;
  the welcome's verb columns are computed, not hand-spaced; "Get started"
  realigned; and the probe matrix was rebuilt — dynamic ANSI-aware column
  widths with … truncation (overflow can no longer smash the embed column),
  and honest fallback semantics: `–*` instead of ✗ when a modality WORKS via
  a fallback, with a footnote naming the path (audio → whisper/STT, image
  embed → caption pivot, video → frames+track / halves).
- **The CLI got a face (D42).** A small pipe-flow banner on the bare
  invocation, color in every `--help` (cyan headings, green options and
  commands), colored doctor/probe marks and column titles, styled
  `config show`/`getschema`/`usage`/cache tables, a cyan spinner with a dim
  live-telemetry segment, yellow warnings, and dim notes — one voice, zero
  new dependencies. TTY-only, `NO_COLOR` always wins, pipes never see ANSI,
  and goldens pin the plain text so styling is never contract.
- **`smartpipe usage` — the resettable usage ledger (D41).** Hour, day, week,
  month, and lifetime windows over what the meter observed (runs, tokens,
  media, audio time, paid conversions); `usage reset` zeroes it, prints the
  previous lifetime, and remembers the reset time. Local state only.

### Changed
- **The rename is total: smartpipe everywhere** (old name collided with an
  existing project; nothing was ever published, so there is no installed
  base to protect). Package and import (`import smartpipe`,
  `python -m smartpipe`), the command (+ a `sempipe` compat alias), env
  vars (`SMARTPIPE_*`), config (`~/.config/smartpipe`), state, and cache
  directories, help, screens, docs, completion (`_SMARTPIPE_COMPLETE`;
  doctor still recognizes old rc lines). Pre-1.2.1 machines: move
  `~/.config/sempipe` to `~/.config/smartpipe` (and likewise
  `~/.local/state`, `~/.cache`) to keep logins and settings.
- **The pitch is multimodality-first.** README, docs front page, the repo
  description, `--help`, and the welcome screen now lead with what makes
  smartpipe different — PDFs (figures included), scans, images, audio, and
  video through Unix verbs — and the examples reflect it. Stale claims
  swept (v1.1-era verb tables, gpt-4o-audio references, old citation).
- **Renamed to `smartpipe`** (the old name collided with an existing
  project). The DISTRIBUTION and repository are `smartpipe`; the command,
  package, import name, env vars (`SMARTPIPE_*`), and config directory stay
  `smartpipe` — zero breakage, and `smartpipe` also works as a command alias.
  Install: `pip install smartpipe`.

## [1.2.0] — 2026-07-06

The biggest release yet, in five sentences: **eighteen verbs** now cover the
whole data loop — KQL-inspired free utilities (`where`, `summarize`, `sort`,
`sample`, `getschema`, `chart --facet/--by-time`) around a grown semantic
core (`extend`, `distinct`, `cluster`, `diff`, `outliers`, `join --kind`).
**Pipelines are artifacts**: multi-stage `.sem` files with `--dry-run` cost
posture, plus custom verbs (named `.sem` files or Python entry points).
**Iteration stops re-paying**: temperature-0 determinism everywhere, a
self-maintaining result cache, and seeded sampling. **Multimodality
deepened**: scanned-document auto-routing to vision, native image
embeddings (jina-clip-v2), a remote-STT role with a sensible auto-matrix,
and video embedded as the fair average of its visual and spoken halves.
**And you can see what you spend**: live token/media telemetry in the
status bar and a run receipt on every exit — observed units, never
estimated dollars.


### Changed
- **Video sampling grew up, and video vectors are the fair average (D36).**
  Frame sampling is now 1 frame/second capped at 24 (was six per video
  regardless of length); a video's embedding is the 50/50 mean of its two
  halves — the visual description and the speech transcript embedded
  separately — so a long transcript can't drown the picture. On a watching
  wire (gemini) both halves come from ONE call via a response schema; the
  fallback composes frame captions (4) plus the track. Text verbs concatenate
  the same halves.
- **The OpenAI default is now `gpt-5.4-mini`** (preset, screens, wizard,
  docs): `gpt-4o-mini` is rejected by ChatGPT-login (Codex) accounts — "The
  'gpt-4o-mini' model is not supported when using Codex with a ChatGPT
  account" — while 5.4-mini works on both the key and login wires. Audio
  input is assumed unsupported on OpenAI: the conversion ladder falls to
  whisper automatically, and the can't-hear suggestions now point at voxtral
  and gemini. **Determinism (D36):** every request now carries
  `temperature: 0.0` on every wire — a pipe is a data tool, and judging,
  extraction, and captions must be reproducible (models that reject explicit
  temperature, like the o-series, get an automatic strip-and-retry).
  Penalties remain per-wire opt-in mappings, so swapping models can never
  make them wrong — at worst unapplied.

### Fixed
- **Inline-schema edge cases, hunted and hardened.** Twenty-five adversarial
  grammar tests (whitespace, colons and URLs in descriptions, enum comma/paren
  torture, arrays, case, duplicates) surfaced and fixed five real gaps: an
  unbalanced `(` could silently swallow every following field into one
  description (now a loud error); duplicate fields produced duplicate
  `required` entries, which strict mode rejects (now deduped first-seen);
  a field typed twice differently won silently by last-write (now an error
  naming both types); `enum()` got a generic message (now "enum needs at
  least one value"); and descriptions keep their colons/URLs intact (split
  on the first colon only, verified).

### Added
- **Run telemetry (D40): observed units, never estimated dollars.** Every
  wire's usage fields now feed a per-run meter (ollama eval counts, compat
  and jina `usage`, gemini `usageMetadata`, anthropic and codex usage) along
  with real media bytes and WAV durations; the status line grows a live
  segment (`↑2.1M ↓340k tok · 38 MB img · 12m audio`) and every run ends
  with a receipt on stderr (`run: 423 in · 75 out tokens · …`) — the number
  that goes in the training report. Paid conversions are counted; local
  whisper is free and uncounted; absent usage under-counts rather than lies.
- **Custom verbs — the contract (D39/06).** Two legs: any `.sem` file
  (stage or pipeline) in `~/.config/smartpipe/verbs/` becomes `smartpipe NAME`
  (validated, shareable, listed in --help), and Python packages can expose
  a `click.Command` via an entry point in group `smartpipe.verbs`. Built-ins
  always win; broken plugins warn and skip (a third-party bug never takes
  down the CLI); discovery is lazy so built-in startup pays nothing.
- **Remote transcription — the `stt-model` role (D39/05).**
  `smartpipe config stt-model openai/whisper-1` (or `SMARTPIPE_STT_MODEL`)
  puts a verbatim transcriber at rung 0 of the audio ladder, ahead of the
  paraphrasing LLM rung, falling through on failure; consent-gated behind
  `allow-captions` like every paid conversion; unset means byte-identical
  behavior. Context protocols across the converter-building verbs carry the
  role. Live: probe.wav transcribed by openai/whisper-1, disclosed per row.
- **Native media embeddings — the model mention is the switch (D39/04).**
  `--embed-model jina/jina-clip-v2` (JINA_API_KEY) embeds text and images
  in one space: image-only items go to the embedder as pixels, skipping the
  caption pivot entirely (`note: media embedded natively — no captions`),
  which is the fix for caption-quality dedupe/cluster on image corpora.
  Text-bearing items keep embedding their text; audio/video keep the
  ladder; non-media embedders behave exactly as before. New `jina`
  provider (embeddings only; picking it for chat is a helpful fault).
- **Scanned documents route to vision, disclosed (D39/03).** A PDF with a
  thin text layer and embedded page images no longer looks like silent
  emptiness: the note names the situation ("thin text layer (11 chars) —
  scanned? routed 8 page image(s) to the vision path") and the past-the-cap
  fix (`split --by pages --media`). The LLM is the OCR — no tesseract. The
  setup wizard now nudges toward image+text-capable models (guides, never
  restricts).
- **The cache maintains itself (D39/02).** 30-day TTL plus a 500 MB
  size-bounded LRU (hits refresh recency), swept automatically at exit at
  most once a day — never at startup, never the user's problem; tunable via
  `cache-days`/`cache-max-mb`; `smartpipe cache stats` inspects entries, size,
  and age.
- **Result caching (D38/15, KQL `materialize`).** Opt-in
  (`smartpipe config cache on` or `SMARTPIPE_CACHE=1`): identical chat calls
  reuse stored replies, so editing stage 4 of a pipeline stops re-paying
  stages 1-3. Sound because of D36's temperature-0 contract; the key hashes
  everything that could change a reply (model, prompts, schema, sampling,
  media bytes). The cache wraps OUTSIDE the call budget — hits never count
  against `--max-calls` (the belt caps spend, not answers). Closing receipt
  (`cache: 9,412 hits · 588 calls`), atomic writes, corrupt entries read as
  misses, `smartpipe cache clear` reports the space freed, privacy page
  discloses the storage. This deliberately reverses the old "no caching"
  parking — determinism changed the calculus.
- **Multi-stage `.sem` pipelines (D38/14).** `[stage.NAME]` tables run in
  order, each stage feeding the next (`input = "name"` picks any earlier
  stage); first reads stdin, last writes stdout; stage receipts are
  name-prefixed; `smartpipe run triage.sem --dry-run` prints the graph with
  per-stage cost posture (free / embeddings / model calls) and runs
  nothing — D18 at pipeline scale. Stage keys validate as loudly as
  single-stage files, and every D38 verb is now scriptable in both shapes.
- **Time bucketing (D38/13, KQL `bin()`).** `chart --by-time ts:1h` draws
  chronological, zero-filled buckets (gaps are signal); `summarize '… by
  bin(ts, 1h)'` groups by the same UTC bucket labels. The fence is hard:
  ISO-8601 or epoch seconds/milliseconds only — unparseable timestamps are
  counted and disclosed, and every other format is jq/date's job.
- **`chart --facet label,severity,region` (D38/12).** Several distributions
  in one pass: stacked sections in the terminal, one multi-panel SVG with
  `--save`, per-facet honest `(missing)` bars and dropped-tail notes.
  The analyst's first-look ritual in one command.
- **`join --kind anti|leftouter` (D38/11).** Reconciliation's set shapes,
  first-class: `anti` emits only the UNMATCHED left rows, verbatim on
  stdout (the mismatch list IS the deliverable — orders with no invoice,
  tickets with no KB article); `leftouter` keeps every left row with
  `"right": null` where nothing matched. `inner` stays the default;
  `--unmatched FILE` remains for inner (and is a usage error with anti,
  which already owns stdout).
- **`smartpipe sort` — order records by a field, free (D38/10).** Numbers
  numerically before strings lexically; missing-field rows always last (both
  directions) with a note; stable ties; byte-faithful passthrough. No `take`
  verb on purpose — head already counts NDJSON rows.
- **`smartpipe getschema` — the stream's field/type/coverage table, free
  (D38/09).** A table on a terminal, NDJSON when piped; mixed types union
  visibly (`integer|string` is the dirt worth seeing); first 10k rows by
  default with `--all` to scan everything; plain-text input gets a one-line
  answer; the footer suggests the next move (`chart`/`where` on the
  best-covered field).
- **`smartpipe sample` — seeded random subsets, free (D38/08).** Reservoir
  sampling, input order preserved, and deterministic BY DEFAULT (seed 0):
  the same input gives the same sample with no flags, so prompt iterations
  compare prompts and the sample is citable; `--seed` varies it,
  reproducibly. The receipt always names the seed. The representative
  counterpart to `--max-calls`' head-truncating belt.
- **`smartpipe summarize` — deterministic aggregation, free (D38/07).**
  KQL's grammar verbatim: `'count(), avg(total), p95(total) by region'` →
  one record per group, largest first, KQL output naming (`avg_total`).
  Missing group fields group under null visibly; non-numeric values skip
  with a counted stderr note; empty numeric groups report null, not zero.
  count/sum/avg/min/max/p50-p99/dcount; the error screen prints the menu.
- **`smartpipe diff` — what distinguishes two sets (D38/06).** KQL's
  diffpatterns for meaning: embed both sides, cluster the union (adaptive
  threshold), keep the lopsided themes with BOTH shares as evidence and
  examples from the dominant side; balanced themes fold into a counted
  note (`--all` shows them). Left is stdin, right is `--right FILE`
  (join's shape). The post-incident "what changed", the eval regression
  story, and dataset drift before the GPU bill.
- **`smartpipe cluster` — themes with sizes and quotes (D38/05).** KQL's
  autocluster done semantically: leader clustering over embeddings, one
  temperature-0 label call per cluster (N embeddings + K labels, never N
  chat calls — the preview line prints before spend). One row per cluster,
  largest first, with share and nearest-centroid example quotes; `--top`
  folds the tail into `(other)`; `--k` forces a count; `--explode members`
  labels every input row for spreadsheets and training files. Degrades to
  numbered clusters without a chat model.
- **`smartpipe outliers` — novelty, surfaced (D38/04).** top_k's mirror:
  the N items farthest from everything, scored by mean cosine distance to
  their nearest neighbors (honest on multi-cluster corpora), anchored
  against the corpus median on stderr so the number means something.
  Embeddings only. Record shape mirrors top_k (`_distance`, original
  fields survive). For novel failure shapes, label noise, and the alert
  that isn't like the others.
- **`smartpipe distinct` — near-duplicate folding (D38/03).** Exact duplicates
  fold free before any embedding; the rest embed once and leader-cluster
  (hand-rolled, deterministic, order-stable — no sklearn). First occurrence
  wins, output keeps input order and bytes, the receipt names the split
  (`kept 412 of 1,208 — 573 exact + 223 near folded`), `--show-groups` is
  the audit trail, and items that fail to embed are KEPT and disclosed.
  The training-data decontamination move and the alert-storm collapser.
- **`smartpipe extend` — your record, plus columns (D38/02).** map's machinery
  with a merge at the emit edge: every input field survives, extracted fields
  land beside them (collisions overwrite idempotently, disclosed once per
  field). Plain lines promote to `{"text": …}` records; `--explode` copies
  the merged fields onto every row; media-transport b64 never re-emits.
  The verb dataset owners needed to drop smartpipe into the MIDDLE of a
  pipeline instead of the end.
- **`smartpipe where` — the free filter (D38/01, KQL-inspired).** Deterministic
  predicates (`has`, `contains`, `matches /re/`, `==/!=/>/>=/</<=`, and/or/not)
  cut the corpus BEFORE any paid stage: `where 'text has "ERROR"' | filter …`.
  Streams, passthrough-verbatim, missing fields evaluate false but are
  disclosed at the end. Grammar errors print the whole operator menu at
  exit 64 before reading stdin. The welcome screen now groups verbs into
  "call a model" vs "free utilities".
- **Inline types join inline descriptions (D37, amends D22).**
  `map "Extract {vendor string: the supplier, total number, status
  enum(paid, unpaid): payment state}"` — the type vocabulary is shared with
  `--schema-from` (one grammar, two homes), commas inside `enum(…)` are safe
  (brace splitting is paren-aware), and a fully-typed group regains
  server-side strict mode. Constraints stay in the DSL/file — the unknown-type
  error says exactly that. filter/reduce/join braces stay bare input
  references.
- **`smartpipe chart`: bars in the terminal, SVG on disk, zero dependencies.**
  `… | map "Extract {label}" | chart label` draws ranked unicode bars from
  NDJSON (or tallies plain lines); `--save labels.svg --title "…"` writes a
  standalone SVG via svgwrite behind the `[charts]` extra (a library, not
  bespoke markup); `--top N` widens.
  Free, no model calls. And `map --help` got humanized: examples first, and
  the promise stated plainly — you usually need NO flags, media is
  first-class and converts (disclosed) when a model can't take it natively.
- **Cloud profiles are multimodal by default (D35).** The `openai` and
  `gemini` presets set `allow-captions = true` (now also a config/profile
  key; the flag still wins): picking the profile is the consent, the wizard
  says so, and per-row disclosures continue. The converter gained **video
  rung 0**: the whole video goes to a model that watches (gemini native), so
  embedded video vectors carry the visuals, not just the soundtrack —
  live-proven ("watched by gemini/gemini-2.5-flash", pivot text describing
  the picture). Local models finally get **bounded generation**: the Ollama
  adapter now passes `num_predict` (it sent nothing before — tiny models
  could ramble forever) and honors new per-request presence/frequency
  penalties, which the converter sets on its prose calls (never on schema
  calls — penalties corrupt JSON; and never on gemini, which rejects them:
  live-caught). `doctor` now closes with a bold yellow pointer at `--probe`.
  Also fixed live: streaming `embed` emitted the pre-conversion item text
  (empty for media), so records now carry the text the vector actually means.
- **Gemini watches video natively (D34).** Gemini chat moved to Google's
  native `:generateContent` wire — the only wired endpoint with video input.
  `map "what happens?" --in demo.mp4 --model gemini-2.5-flash` sends the
  actual video; live-proven: the model described the visuals AND quoted the
  synthesized narration verbatim, zero conversions. The map video ladder
  gained rung 0 (attempt the raw video; every other adapter refuses pre-send
  at zero cost, so capability stays by-attempt). Structured output translates
  to Gemini's response-schema dialect; embeddings stay on the compat wire;
  the same retry/Retry-After/D18 taxonomy applies.
- **One embedding space: everything converts to text (D33).** Images and
  audio now enter `embed`/`top_k`/`filter`/`reduce`/`join` through an LLM
  conversion ladder — a hearing model transcribes speech verbatim or
  *describes non-speech sound* (the gap whisper could never cover); a vision
  model describes images including their visible text; whisper remains audio's
  free fallback. The cost fence: a **local** model converts automatically and
  free; a **cloud** model converts only behind `--allow-captions` (one flag,
  both modalities). Every conversion is a per-row `⚠ degraded:` line. Swapping
  embedding models changes nothing — the embedder only ever sees words. The
  `local` profile anchors the space with `embeddinggemma` (multilingual,
  308 M). Live-proven: a PDF-embedded figure captioned by a vision model, then
  ranked first (0.90) for "a noisy gradient from red to blue"; without the
  flag, the pinned skip line and zero paid conversions. CLIP-class and
  unified-space models (jina-clip-v2, OmniEmbed, mm-embed-small) are recorded,
  evidence-gated candidates — none ship.
- **`doctor --probe`: the modality matrix (D31).** Four tiny paid calls
  (announced first; plain `doctor` stays free) chart which modalities actually
  reach your configured chat and embed models — text, image, audio, video
  (local ffmpeg check), documents — with per-cell verdicts and reasons. The
  probe assets (an 8x8 PNG, a 0.25 s beep, one sentence) ship in the wheel.
  Its first live run immediately earned its keep: Gemini's OpenAI-compat
  endpoint DOES hear audio ('heard it — Tone'), correcting our own docs.
- **Config profiles (D30).** Named bundles of the existing config keys, three
  shipped presets (`openai`, `gemini`, `local` — the local one on
  `ollama/gemma-4-e2b`, the multimodal 2.3B-effective model), user tables via
  `[profiles.NAME]`, `smartpipe config profile [NAME]` to list/switch,
  `SMARTPIPE_PROFILE` for one-offs, and the interactive `smartpipe config` now
  opens with a pick-a-profile question on fresh setups. Direct keys beat the
  profile; profiles never hold secrets (a key inside one is rejected loudly).
- **`join` handles oversized sides (W3).** No more skipping: an oversized left
  or right item is chunk-embedded once, mean-pooled for blocking, and the
  judge reads only the most-relevant chunk of that side (argmax similarity
  against the other side), disclosed per row. Test-pinned: a ~65k-token right
  row matches while the judge sees under 20k characters.
- **Documents are multimodal items (D32).** `Item.media` grew from one value
  to a tuple of parts, and with it: `map --in report.pdf` now sends the text
  AND the embedded figures (up to 8, icon floor, counted on stderr);
  `split --by pages --media` emits one item per page carrying that page's
  text and figures together (live-proven: a 3-page PDF summarized per page,
  each summary naming its own figure's content); text verbs use the text and
  drop figure parts with a per-row note; token-mode `split` passes figures
  through as standalone figure records. Multi-part items ride NDJSON as a
  `parts` list and rebuild downstream.
- **Video input, the poor man's way (D27).** `.mp4/.mov/.mkv/.webm` files (and
  video on stdin) become items carrying their bytes; `map` converts each to
  N evenly-sampled frames + the audio track (ffmpeg via the `[video]` extra's
  bundled binary, or PATH), tries the native wire (frames seen, track heard),
  and on a deaf model falls to frames + a whisper transcript. Text verbs
  transcribe the track (frames dropped, said so). `split --by seconds` slices
  video losslessly at keyframes, slices staying video through the pipe.
- **Every conversion is disclosed on its row (D27).** `⚠ degraded: <source>
  audio → text (whisper tiny)` lines (capped per kind) plus one closing rollup
  (`degraded: audio→text ×1,203`), across map, filter, embed, top_k, reduce,
  join, and split. The old once-per-run transcription note is retired.
- **`map --explode FIELD`.** One row per element of a list-valued field,
  sibling fields copied (`{"vendor":"Acme","risks":[…]}` becomes one row per
  risk). Empty list = zero rows; non-lists pass through. Composes with
  `--tally` (counts per exploded row) and `--fields`.
- **`split --media`: the images inside your documents (D29).** Figures
  embedded in PDFs/DOCX/PPTX/XLSX become image items with page provenance
  (`report.pdf p.7 img.2`), byte-identical (office zips via the stdlib; PDF
  JPEG streams passed through, never re-encoded), 4 KB icon floor with a
  counted note, riding the pipe as base64 NDJSON that the next verb *sees*.
  Live-proven end to end: a photo embedded in a PDF, extracted and correctly
  described by a vision model. Explicit by design — decks full of decorative
  logos become items only when you ask (D29: extract as items, never fuse).
- **`split` learned real units (D26/D27).** `--by pages[:N]` groups PDF pages
  with true page provenance (`report.pdf p.6-10`); `--by minutes:N` /
  `--by seconds:N` slices audio into segments that **stay audio** through the
  pipe (base64 NDJSON, rebuilt on read), so
  `split --by minutes:10 --in call.wav | map "what was agreed?"` sends each
  slice to a model that hears it natively, with clock provenance
  (`call.wav §00:10-00:20`) that survives into every downstream warning.
  Live-proven: a spoken recording sliced at 4s boundaries, each slice heard by
  Voxtral. wav slices natively; other formats want ffmpeg on PATH.
  `--max-tokens N` stays as shorthand for `--by tokens:N`. Also disclosed in
  the docs: images embedded in PDFs/DOCX are currently dropped by text
  extraction (extraction-as-items is the planned fix, D29).
- **See your data: `--tally`, `join --unmatched`, and the visualization
  cookbook.** `map … --tally label` counts any extracted field live on the
  status line and prints one final stderr line (`tally: bug 14 · feature 7 ·
  question 3`) — stdout untouched. `join --unmatched leftovers.txt` writes
  every zero-match left item verbatim (the worklist for a second pass) and
  reports the split. And a new cookbook page wires the rest: `uniq -c`,
  youplot bars, visidata tables, and the join threshold picker.
- **The oversize stack (D26): probe, bisect, split.** Context windows are now
  *probed* when an input looks too big (Ollama, Mistral, Gemini, and OpenRouter
  publish theirs — live, Gemini's probe widens the budget 8x past the table
  floor; `SMARTPIPE_CONTEXT_TOKENS` overrides everything). `reduce` self-corrects
  when every estimate lies: a chunk the wire rejects splits in half and
  retries, so the token estimator is a hint, not a correctness dependency. And
  the new **`split` verb** turns one oversized item into provenance-carrying
  chunk items (`{"text", "source": "report.pdf §3/12"}`) for free — chunks
  reassemble to the exact original text, property-tested. Per-verb ladders on
  top: `map` refuses an over-window item with the split|map|reduce recipe
  (before spending), `filter` judges chunks (any match keeps the whole item),
  `embed`/`top_k` mean-pool chunk vectors into one document vector.

### Fixed
- **CI on Python 3.11 and macOS runners.** The signals-test harness closed the
  child's stdin and later called `communicate()`, which 3.11 rejects
  (`ValueError`, fixed in 3.12+); the OAuth callback test depended on the
  host's `localhost` resolution order. Both the harness and the callback
  server now use the explicit v4 loopback.

### Changed
- **Audio transcription is now local (faster-whisper).** The `[audio]` extra
  swaps Google's Web Speech API for a local Whisper model (`tiny` by default,
  `SMARTPIPE_WHISPER_MODEL=small|medium|large-v3` to trade speed for accuracy;
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
- **Prompts from files (D23).** `smartpipe map @prompt.md` reads the prompt from
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
  time; and `smartpipe schema "an invoice with …" > invoice.json` — a drafted
  schema file (one call + one repair, meta-validated; a failed draft exits 3
  with **empty stdout**, so a broken schema can never slip into a pipe).
  Braces never grow type syntax — descriptions in braces, types in the DSL.
- **Native audio Q&A (D20).** `smartpipe map "what does the caller want?" --in
  'calls/*.wav'` sends the *sound itself* to models that can hear
  (`gpt-4o-audio-preview`-family, Voxtral — wav/mp3 as `input_audio` parts,
  byte-verified); models that can't trigger a transcription fallback when the
  `[audio]` extra is installed (with a one-time note), else a skip naming both
  fixes. Text verbs (`filter`, `embed`, `top_k`, `reduce`, `join`) transcribe
  on demand instead of eagerly at read time. All of it lands on the one media
  union (`Item.media`), so vision behavior is byte-identical — and the whole
  audio diff came in *smaller* than vision's, as the constitution demands.
- **`smartpipe join` — the sixth semantic verb (D21).** Match stdin against a
  second input wherever a plain-English predicate holds:
  `join "{left.text} concerns {right.name}" --right products.jsonl`. Embed →
  block → judge keeps cost at lines×k (default k=5) instead of lines×catalog;
  the right side is indexed up front so a bad file costs zero chat calls; a
  TTY cost preview appears before big runs; the left side streams flag-free
  (live enrichment). Output nests `{"left", "right", "_score"}` per matched
  pair; `--fields` grows dotted paths. `make join-eval` publishes the recall@k
  table that justifies the default, and the docs teach the `--k 20` spot-check.
- **`smartpipe doctor`** — every no-cost setup check on one screen: config parses,
  Ollama reachable, configured chat/embed models installed, API keys present
  (never printed, never validated — validation costs a call), ChatGPT login,
  optional extras, shell completions. Each ✗ carries its fix; exit 0 all-green,
  1 otherwise. A broken config is a reported line, not a crash.
- **`--max-calls N` — a hard cost ceiling (D18).** Counts model calls (a repair
  re-ask counts; wire retries don't). Per-item verbs stop intake at the cap and
  drain gracefully (`note: stopped by --max-calls (N calls made)`); whole-set
  `top_k`/`reduce` treat mid-collection exhaustion as fatal with a fix screen —
  a partial collection is nothing usable. A capped run never exits 0. Also
  `SMARTPIPE_MAX_CALLS` and the `.sem` key `max-calls`.
- **Mistral, first-class.** `--model mistral-large-latest` (or any of the
  family's bare prefixes — `ministral-*`, `codestral-*`, `magistral-*`,
  `devstral-*`, `pixtral-*`, `open-mistral-*`, `open-mixtral-*`) just works:
  routing, `MISTRAL_API_KEY`, `mistral-embed` embeddings, `pixtral-*` vision,
  structured output, and the same retry/`Retry-After` resilience — all on the
  built-in OpenAI-wire adapter, no extra install. `SMARTPIPE_MISTRAL_BASE_URL`
  redirects it; `hf.co/mistralai/...` still routes to Ollama, untouched.
- **`.sem` stage files + `smartpipe run`.** Save one verb invocation as TOML
  (`verb = "map"`, `prompt = …`, the verb's flags as keys) and execute it with
  `smartpipe run stage.sem` — or add `#!/usr/bin/env -S smartpipe run`, `chmod +x`,
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
- **Shell completions** for bash, zsh, and fish (click's `_SMARTPIPE_COMPLETE`
  machinery; setup one-liners in the install docs). `--model`, `--embed-model`,
  and `config model`/`config embed-model` complete with your configured model
  plus the locally installed Ollama models — probed with a 150 ms cap so `<TAB>`
  never hangs, and any probe failure just means no suggestions.
- **Log in with ChatGPT.** `smartpipe auth login` (browser PKCE, or `--headless`
  device codes) lets ChatGPT Plus/Pro subscribers use OpenAI's Codex-family models
  without an API key. Tokens live in `~/.config/smartpipe/auth.json` (0600,
  self-refreshing, `smartpipe auth logout` removes them); an exported
  `OPENAI_API_KEY` always takes precedence. Embeddings still need a key or a
  local model — smartpipe says so instead of failing cryptically.
- **Vision.** Images (`--in 'photos/*.jpg'`, or one redirected to stdin) flow to
  the chat model as images — describe them, or extract `{fields}` from them —
  across all three providers. Non-vision models skip the item with a hint; the
  other verbs point you to `map`.
- **Binary stdin.** `smartpipe map "Summarize" < report.pdf` works: the bytes are
  sniffed, spooled, and parsed as one document item. Unrecognizable binary input
  stops with a clear screen instead of garbling.
- **Mixed input.** `--in` now composes with a pipe: files first (glob-sorted),
  then stdin lines, one run.
- **Streaming, flag-free.** `map`, `filter`, and `embed` now read stdin
  incrementally — `tail -f app.log | smartpipe filter "…"` just works, with results
  flowing out as lines arrive and a count+rate status line (`· N matched` for
  filter). Finite-input behavior is byte-identical to 1.0.
- **`reduce --window N [--every M]`** — rolling synthesis over a stream: one
  reduce per window (tumbling, or sliding with `--every`), each emitted as
  `{"window_end": …, "result": …}`; the trailing partial window is flushed on
  Ctrl-C/EOF with `"partial": true`.
- **`top_k K --stream --near "…"`** — the live leaderboard: repaints the top-K
  block in place at a terminal; in a pipe, emits an NDJSON snapshot (a
  `{"_snapshot": n}` marker + K ranked records) whenever membership changes.
- **`smartpipe cite`** — print a copy-paste BibTeX entry; `CITATION.cff` ships in the
  repo (GitHub's "Cite this repository" reads it) and in the sdist.
- **Unix death done right.** Downstream closing the pipe (`… | head`) now kills
  smartpipe instantly and silently with the conventional exit 141 — never the BUG
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
- **Optional-field schemas no longer 400 on OpenAI/Mistral.** smartpipe claimed
  `strict: true` structured output unconditionally; strict mode rejects any
  schema whose fields aren't all required (with `additionalProperties: false`),
  so a `--schema` with optional fields skipped every item for the wrong reason.
  Strictness is now claimed only when the schema qualifies; either way, replies
  are validated client-side with one repair retry.
- **Config edits are atomic and lossless (except comments).** `smartpipe config
  model …` now rewrites `config.toml` via a same-directory temp file +
  `os.replace` (a concurrent reader can never see a torn file), and keys it
  doesn't know survive the rewrite — an older smartpipe no longer strips a newer
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
- **Startup stays fast, now enforced.** `smartpipe --help` no longer imports
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
  DOCX, PPTX, XLSX, HTML, and EPUB extract via the optional `smartpipe[files]` extra;
  detection is by extension with a magic-byte fallback. Unreadable, unparseable, or
  unsupported files are skipped with a warning — a bad file never crashes the run.
  Missing an extra shows one-time install guidance, then skips those files.
- **File-mode output for `filter`/`top_k`** — they emit the matching/ranked
  **filenames** (with the score, for `top_k`), so filtering or ranking a folder of
  documents returns paths you can pipe onward.
- Docs: `docs/inputs/files.md`; extras table and pipes-and-items updated.

### Not yet
- Describing images with a vision model, and reading a single binary file from
  stdin (`smartpipe map … < report.pdf`), are planned for a following release.

## [0.4.0] — 2026-07-05

The last of the five verbs — smartpipe's full vocabulary now works end to end.

### Added
- **`smartpipe reduce`** — synthesize all input items into one result. When the input
  exceeds the model's context, smartpipe **automatically** chunks it, summarizes each
  chunk into dense notes, and recurses on the notes — no flags, no strategy to pick.
  `--group-by FIELD` produces one result per group (with `{field}` naming the
  group's value in the prompt); `--schema` validates the final result (same one-shot
  repair as `map`); `--verbose` prints the chunking tree. A chunk that fails is
  skipped with a warning (exit 1) rather than aborting the whole reduction.
- Docs: `reduce`.

## [0.3.0] — 2026-07-05

### Added
- **`smartpipe embed`** — convert each item to a vector embedding, emitted as NDJSON
  (`{text, vector, source}`). Uses the embedding model (never a chat model);
  redirect to a file to reuse. `--embed-model`, `--concurrency`.
- **`smartpipe top_k`** — rank items by cosine similarity to `--near`, keeping the
  top `K` and/or everything above `--threshold` (each result gains a `_score`).
  Reuses a precomputed `vector` from an `embed` record instead of re-embedding, so
  a corpus can be embedded once and queried many times. A query/corpus embedding
  dimension mismatch stops with a clear message. Spellings `top-k`/`topk` also work.
- Docs: `embed`, `top_k`.

## [0.2.0] — 2026-07-05

### Added
- **`smartpipe filter`** — semantic grep. Keeps the items that match a plain-English
  condition, byte-for-byte unchanged and in input order (a strict subset of the
  input). `--not` inverts, like `grep -v`. `{field}` references pull values out of
  JSON Lines input (`"{priority} is wrong given {description}"`); comma-groups are
  rejected as map-only, and a field reference on non-JSON input fails fast with a
  clear message. An unparseable verdict is repaired once before the item is skipped.
  Zero matches is a successful (exit 0) empty result.
- Docs: `filter`, and a new "pipes & items" concept page.

## [0.1.0] — 2026-07-05

The first release: `smartpipe map` works end to end, local-first.

### Added
- **`smartpipe map`** — transform each input item with a prompt. Plain-text mode
  (one line in, one line out) and structured mode: put `{field}` names in the
  prompt (or pass `--schema file.json`) to get validated JSON back. A reply that
  fails schema validation is repaired once (re-asking the model with the error)
  before the item is skipped. `--model`, `--output`, `--concurrency` flags.
- **`smartpipe config`** — interactive first-run setup, plus `config show` (effective
  settings with their origin), `config model`, and `config embed-model`.
- **Local-first models** — talks to a running Ollama by default (with model
  autodetection); any OpenAI-compatible endpoint via `--model`/`SMARTPIPE_OPENAI_BASE_URL`;
  Claude via the optional `smartpipe[anthropic]` extra. API keys are read from the
  environment, never stored.
- **Unix-native behavior** — results to stdout, diagnostics to stderr; TTY-adaptive
  output (human-readable at a terminal, NDJSON when piped); order-preserving
  bounded-concurrency execution; per-item failures skip with a warning instead of
  crashing; documented exit codes; a batch progress spinner (suppressed off-TTY).
- Docs: quickstart, install, `map`, models-and-providers, structured-output.

### Not yet
- `filter`, `embed`, `top_k`, `reduce`, file inputs, and streaming land in the
  following releases. The architecture for all of them is already in place.
