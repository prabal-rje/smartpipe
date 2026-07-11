# Changelog

All notable changes to smartpipe are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Fixed
- **`graph` proves the model can hold the schema before spending on OCR.**
  Both paid modes now fire one synthetic extraction ("Alice pays Bob for the
  shipment.") through the compiled schema before any ingestion — full mode
  before the reader touches a paid page, hybrid before the naming loop. A model
  that cannot produce the typed shape (even after the one shape-repair rung)
  refuses at SETUP (2) naming the model and a fix, instead of burning the whole
  run — a pilot run spent 943 OCR pages and 7 extractions before a wholesale
  schema collapse. The probe charges one belt unit and is cached, so a rerun's
  check is free; the partial-run plan counts that unit, so a belt sized exactly
  to the corpus warns "partial" up front instead of silently dropping the last
  chunk, and the probe is skipped entirely when the belt can't afford it plus
  real work (a `--max-calls 1` run does its one real call unprobed). An
  availability fault at canary time (belt/429/breaker) propagates as itself and
  is never mistaken for a capability verdict.
- **A model that keeps breaking the reply schema now says so, once.** When a
  schema-attached request comes back violating its schema even after the one
  paid repair rung — so the item skips — `map` (and `graph`, whose chunks flow
  through it) prints one loud stderr line per run naming the model. A loose wire
  that only advises the schema (ollama's `format`, which many cloud models
  ignore) is told it "likely ignores constrained decoding"; a strict-enforcing
  wire (openai, mistral) doing the same is flagged as a possible provider-side
  regression. A run of skips now reads as "wrong model" instead of a mystery.
- **A fail-fast halt no longer throws away everything already extracted.** When
  the failure policy trips mid-extraction (too many chunks failed the schema),
  `graph` full mode now folds and writes the edges it already has to stdout,
  then exits ALL_FAILED (3) carrying that salvaged graph, instead of
  propagating past the writer and leaving a 0-byte file. A pilot run lost 7
  good extractions and 943 paid OCR pages to this exact gap. Hybrid mode
  salvages too: the free co-occurrence graph is already whole, so a naming-model
  collapse stops only the naming pass and exits PARTIAL (1) with the strongest
  edges kept as co-occurs.
- **A failed OCR upload no longer eats a later document's page belt.** The
  dedicated OCR wire reserves a document's full page count against
  `--max-calls` before uploading, so an over-belt PDF never uploads partially.
  When that upload then fails — a 429 ladder exhausted, the breaker open — the
  reservation is now refunded, because those pages were never converted; a dead
  document can no longer starve the documents that come after it.
- **The huggingface_hub "set a HF_TOKEN" warning stays off stderr.** The first
  `graph --fast` run downloads the local NER weights through huggingface_hub,
  which printed an unauthenticated-request warning straight to stderr. smartpipe
  now sets that warning's own documented toggle
  (`HF_HUB_DISABLE_IMPLICIT_TOKEN`) the same per-library way onnxruntime's log
  level is pinned, so diagnostics own stderr — never the hub's chatter.

## [1.5.1] — 2026-07-10

### Fixed
- **The audit boundaries now fail closed.** `--local-only` accepts only
  canonical loopback endpoints and ignores ambient proxy settings, so model
  execution and user data remain on-device without pretending the machine is
  air-gapped. Input spools are owned through consumption; fragmented binary
  sniffing, OCR page billing/fallback, binary `--right` inputs, split stops,
  output/manifest aliases, and fatal reduce-task cleanup are pinned.
- **Batching is bounded, injection-safe, and counts real API calls.** Packed
  chat requests cap at 12 items, escape record text at the XML framing layer,
  validate recursive schemas, and tear down cleanly on cancellation.
  `--concurrency` limits simultaneous API calls (not item workers); exhausted
  rate-limit/transport failures fan out once instead of launching one retry
  ladder per packed item, while item-specific failures still isolate through
  a single solo recovery.
- **Receipts and exits count input sources rather than internal work units.**
  Multi-page OCR, prefetched-but-unsent rows, whole-set exclusions, research
  agreement/sampling, reader mode, joins, and graphs now preserve the
  succeeded/skipped/failed subset laws without inflating one document into
  many sources. Empty OCR, empty valid inputs, all-skipped runs, and partial
  whole-set results have explicit tested outcomes.
- **Concurrent cache misses are single-flight per request.** Repeated identical
  items in one run now share one model result instead of racing multiple
  packed answers into the same cache key; an all-hit rerun is byte-identical,
  and cancelling one waiter cannot cancel the shared fill.
- **Strict time-bin summaries validate the source field.** A `.sem` stage using
  `summarize 'count() by bin(ts, 1h)'` no longer emits correct rows and then
  faults because the generated `ts_bin` output alias was absent from its
  inputs.

### Changed
- **One run-scoped policy now owns outbound behavior.** Chat, embeddings,
  dedicated OCR, and remote transcription share composition-root admission,
  call budgeting, concurrency, and availability state. Environment/config
  choices are resolved once, request construction stays deferred until a call
  is admitted, and run disclosures reset between invocations.

## [1.5.0] — 2026-07-10

### Fixed
- **The published 1.4.0 introduced itself as 1.4.0rc1.** `__version__` was
  never bumped past rc1, so `--version`, `cite`, and the update-check
  comparison all carried the rc tag - and the cite golden had pinned the
  stale value, so tests stayed green. All four version sites fixed; the
  release checklist now names every one of them. The README Python badge
  also caught up with reality (3.11-3.14 - the matrix has proven 3.14 for
  a while).
- **A `.env` on disk no longer leaks into smartpipe's environment.** The
  file-type sniffer inside the document parser (magika, via markitdown)
  calls `load_dotenv()` at import time - so parsing a document while
  sitting in a repo with a `.env` silently injected credentials the user
  never exported. The import is now fenced: anything it adds or changes
  is reverted. Exporting a variable is consent; a file on disk is not.

### Added
- **The research kit: manifest, agree, stratified sampling, and a hard
  privacy fence.** `--manifest run.json` on any model verb writes the
  citable methods-section artifact at run end - models, prompt and its
  sha256, compiled schema, counts, token receipt, timestamps, exit
  status - atomically, even on partial and interrupted runs.
  `smartpipe agree gold.jsonl model.jsonl --on id` scores inter-rater
  agreement (observed, Cohen's kappa, Krippendorff's alpha, confusion
  matrix) with zero model calls - the math reproduces the published
  worked examples to the printed digit. `sample 50 --by label` allocates
  proportionally per stratum, integer-exact, still seeded and
  reproducible. And `--local-only` is the IRB sentence made enforceable:
  every cloud wire refused before any spend, remote OLLAMA_HOST
  included, so model execution and user inputs stay on this machine.
  Supporting downloads without user payload are allowed; this is not an
  air-gap mode. One cookbook page tells the whole story with real runs.
- **The OCR role works everywhere now.** cluster, diff, distinct,
  outliers, split, and join (both sides, `--right` included) honor a
  configured ocr-model with the same per-row disclosure, local fallback,
  and `--max-calls` belt as the rest - and split gains the belt flags,
  since OCR is the one way it ever calls a model. Unset stays exactly as
  free as before, pinned per verb. Embedding runs over OCR-parsed
  documents batch again (a two-pass count replaced the per-item
  fallback), and `using`/`config show` finally list every role and
  posture - stt-model, ocr-model, media-embed-model, cache, batching,
  update-check, media-previews - each with its value and where it came
  from. The missing-key screens now offer `smartpipe auth login
  PROVIDER` alongside the export line.
- **Request batching: N small items, one model call.** map, extend, and
  filter now coalesce eligible items into packed requests - ten
  one-line classifications cost one HTTP call instead of ten, and
  `--max-calls` counts what actually flies. Each item keeps its own
  labeled slot in the reply, so a model that skips one names exactly
  which item re-runs solo; valid answers are never thrown away with the
  batch. Caching stays per item, media and oversized items never batch,
  and one stderr note tells you what happened:
  `note: batched 500 items into 42 calls`. On by default;
  `smartpipe config batching off` (or `SMARTPIPE_BATCH=off`) restores
  strict one-call-per-item behavior.
- **Field paths reach everywhere.** The nested-path grammar (`a.b.c`,
  `items[0]`, `a['weird key']`) now also works in `--fields`, `write`
  templates (`smartpipe write 'out/{user.plan}/{stem}.txt'`),
  `--explode`, `--tally`, and `join --on` - the same
  exact-column-name-wins rule as everywhere else, and malformed paths
  fault at the flag, before anything is spent. En route, `write`
  templates with dotted vars or bare `{}` used to crash with a raw
  traceback; they're clean usage errors now.
- **`smartpipe demo` - a practice corpus in one command.** Downloads the
  26 MB playground (invoices, reports, photos, recordings, sessions,
  JSONL) into `./smartpipe-playground` - sha256-verified before unpack,
  staged so an interrupted download leaves nothing, idempotent on
  re-runs, and it refuses to touch a directory it didn't create. Ends
  with three copy-pasteable free commands; every cookbook recipe runs
  against this corpus. Zero model calls, zero config.

## [1.4.0] — 2026-07-10

The identity release.

### Knowledge graphs (the highlight)
- **The showcase**: a full verb page with the modality matrix and a
  live-rendering Mermaid sample, a cookbook chapter written from real
  runs against the downloadable playground corpus, the README highlight
  with a screenshot of the case graph, agent SKILL recipes, and `graph`
  on the welcome screen. Found en route: the playground download in the
  docs pointed at the source tarball (no media inside) - all three
  sites now fetch the actual 26 MB release asset.

- **`smartpipe graph --fast` turns a corpus into a knowledge graph for
  exactly $0.** A local zero-shot NER model (GLiNER-small over ONNX -
  no new dependencies, no torch, a one-time ~190 MB download like the
  local embedder) finds YOUR entity types (`--entities "person, vessel,
  account"`), entities co-occurring in a window become weighted edges,
  near-duplicate names fold into one node (disclosed), and every edge
  carries its `sources` - the files, pages, and minutes where it was
  seen. Zero model calls, pinned by test. Free transcripts (local
  whisper) mean audio and video speech join the graph; scans and
  images wait for the paid mode, counted out loud.
- **Six ways out, all deterministic**: `.graphml` (Gephi), `.dot`
  (Graphviz), `.mmd` (Mermaid), `nodes.csv`+`edges.csv` (Neo4j/Kuzu),
  a self-contained interactive `.html` (search, weight slider, hover
  cards with clickable `file://` evidence links), or a trailing `/`
  for an Obsidian vault of wikilinked entity notes.
- **A focus prompt buys named relations.** `smartpipe graph "people,
  orgs, and money flows" case/**/*` extracts typed triples per chunk
  through the full multimodal ladders - vision, OCR, native video -
  with `--entities`/`--relations` compiling to enums when you want a
  strict ontology. The cost plan prints BEFORE any spend (`~810
  extraction calls; belt is 500 - the graph will be partial`), a
  terminal decline exits clean at zero calls, and a belted run drains
  to a valid partial graph at exit 1, never a fake success.
- **The hybrid ladder: `--name-top 200`.** The free pass finds the
  candidates; the model names only the strongest links - one call per
  edge, and if the belt runs out the remainder keeps its honest
  `co-occurs` label, disclosed. Free graph in seconds; name the top
  edges for cents.
- **Bring your own edges.** Records with `{source, target}` or
  `{subject, relation, object}` on stdin skip extraction entirely -
  canonicalize, fold, serialize. Your own `extend` pipeline is the
  power path.

### The item — one law for everything in a pipe
- **Nested field paths, everywhere you read.** `where 'user.plan ==
  "pro"'`, `sort --by metrics.score`, `chart items[0].category`,
  `summarize 'avg(metrics.score) by user.plan'`, and `"Is {user.plan}
  worth it?"` in prompts - one grammar (`a.b.c`, `items[0]`,
  `a.b['weird key']`) across all five surfaces. A column literally
  named `user.plan` always wins over the traversal, so CSV headers
  with dots keep working. Extraction names stay flat - paths are for
  reading, and the error says so.

- **Dates are a type, and they come back ISO.** `{due date}` and
  `{ts datetime}` join the brace grammar. However the model phrases it
  ("Jan 15, 2026", "15/01/2026"), the field canonicalizes to
  `YYYY-MM-DD` / full ISO-8601 - offsets preserved, never invented;
  genuinely ambiguous slash-dates read month-first and say so on
  stderr. Downstream just works: `where 'due >= "2026-01-01"'`
  compares temporally, `sort --by due` orders by instant, and
  `summarize 'count() by bin(due, 1d)'` buckets date-only values.
- **The model sees your data framed, not dumped.** Every item payload
  now arrives inside an `<input>` fence - records rendered as clean
  YAML-ish (the `__` plumbing excluded), text as itself. The
  instruction can no longer blur into the data, and the fence is the
  foundation the planned request-batching builds on.
- **CSV is a first-class row format.** `--as csv` (and `.csv`/`.tsv`
  extension defaults - tab dialect for `.tsv`) reads the header row as
  field names and streams every data row as a record, with per-cell
  int → float → string coercion and multi-line quoted cells handled. A
  ragged row is a loud error naming the file and physical line; rows
  carry `__source {"as": "csv", "line": N}`. Works on stdin too, and
  `.sem` files gain the `as` key.
- **Malformed model replies repair for free before they cost money.**
  A structured reply that fails to parse now passes through a
  deterministic fixer first - code fences, JSON-island extraction,
  Python-repr literals, trailing commas, quote fixes, all validated by
  an actual parse - and only escalates to the paid model-repair round
  trip when that fails. Disclosed once per run:
  `note: N replies repaired deterministically (fences/commas/quotes)`.
- **The `__` metadata spine.** Tool metadata lives in a reserved
  double-underscore namespace that rides every record: `__source` (path plus
  HOW the item was cut - file, lines, jsonl, pages, minutes, segment index),
  `__media` (one transport object for bytes crossing a pipe), `__score`
  (top_k and join similarity), `__rank` (top_k), `__snapshot` (top_k
  --stream markers), `__distance` (outliers), `__invalid`/`__error`/`__raw`
  (kept failures). Known fields round-trip through saved files; unknown `__`
  fields warn once and carry. User data owns everything up to one leading
  underscore - which is why the last single-underscore stragglers moved:
  pre-1.4 `top_k`/`outliers` wrote `_score`/`_rank`/`_snapshot`/`_distance`,
  and those spellings stay readable for one release (CSV/TSV still sort them
  into the trailing columns) but are never written again. In the same sweep,
  `cluster --explode` overwriting an existing `cluster` field became a
  disclosed overwrite (one stderr warning) instead of a silent one.
- **top_k ranks can no longer collide.** Ranking keyed on each item's
  source index, so two page-1 records from different PDFs fought over one
  slot and a result silently vanished. Ranks are now run-scoped ordinals in
  arrival order, in both the embedded and precomputed-vector paths.
- **Records in, records out.** A plain-prompt `map` over records now returns
  a record (`{"result": …}` plus the spine) instead of bare prose - structure
  and provenance survive transformation. Text lines still leave as text.
- **The `--as` ingestion dial** (`file` | `lines` | `jsonl`) on every input
  verb. Auto defaults: `.jsonl` means rows (loud per-line errors on
  non-JSON), everything else means whole-document; stdin keeps per-line
  sniffing; `--as file` slurps stdin whole (new). Media files refuse text
  granularities with signposts to `split`.
- **The binary is the reader.** `smartpipe report.pdf | …` - a first
  argument that exists on disk streams items; verbs always win names,
  `./name` disambiguates. Every input verb also takes positional FILE
  arguments; `--in` survives as a hidden alias.
- **`write` — the egress mirror.** Items route to files by template
  (`{name}`, `{stem}`, `{index}`, any record field for content fan-out);
  egress mirrors ingress via the spine - whole-file items become files,
  line/row items reassemble into their sources in original order. Emits
  written paths so pipes continue. Text-only records leave as plain text.
- **`readable` — the human door.** The same YAML-ish renderer as the TTY
  preview, as an explicit pipe stage: nesting indented, lists bulleted,
  multi-line strings as block scalars, spine dimmed, long values truncated
  with counts (`--full` to disable), media summarized (`image/png (48 KB)`)
  - never base64. Piped machine output is untouched JSONL; `--bare` strips
  the spine from it.
- **Mixed streams are visible.** A kind census notes mixed inputs;
  `where`/`summarize` report rows that had no fields; `--strict-rows` turns
  the notes into errors. Multi-line plain output into a pipe warns that
  line framing is ambiguous (use `--output json`).
- **`.sem` files are strict about their rows now.** A saved pipeline is a
  program, and a stray plain line in a record stream is a bug, not a
  shrug: `.sem` runs default to `--strict-rows` and fail at ingestion
  naming the exact line, before any spend. Opt out with
  `strict-rows = false` (top-level or per-stage); ad-hoc terminal runs
  keep the permissive census. Eleven verbs that accepted `--strict-rows`
  without actually enforcing it now do, and a text-only `where` predicate
  over a log no longer trips strict mode over "missing fields". A new
  docs page explains the granularity ladder (file → split → lines/rows)
  and when mixing happens.
- **Synthesized records tell you what they summarize.** join pairs carry
  `__sources` (both parents' refs); reduce windows stamp
  `{"as": "window", "span": [1, 100], "count": 100}`, groups
  `{"as": "group", "group": …, "count": N}`; cluster and diff summary
  rows carry `{"as": "cluster"|"diff", "count": N}`. An audit trail for
  every row that speaks for other rows - and `--bare` strips all of it.

### Judgment you can gate, retry, and keep
- **`--keep-invalid`** (map/extend): after the repair retry fails, keep the
  failure as one JSON row (`__invalid`/`__error`/`__raw`) instead of a skip
  - dim one-liner at a TTY, full row when piped.
- **`smartpipe schema`** - the free rungs: compile braces/DSL to JSON Schema,
  `--check FILE` validates rows (exit 0/1), `--example` prints a validating
  instance, bare stdin mode is a REPL. **`--dry-run`** (map/extend) prints
  the composed first request and exits before any model resolution.
- **`schema --check` stopped failing your own pipelines.** The check is
  open-world now: rows from the documented extend workflow (original
  fields kept, spine aboard) pass instead of tripping "additional
  properties are not allowed" on every row, and `?`-marked fields may be
  absent, not just null. Extras earn one dim hint; `--strict` restores
  the closed world for contract enforcement. The schema sent to models
  at extraction time is unchanged.
- **Nullability is declared.** `?` on any type (`string?`, `number[]?`, bare
  `field?`) compiles to a null union; bare fields mean scalar-or-scalar-list
  and never admit null (D48, shipped earlier in this cycle).
- **Circuit breaker + `fallback-model`.** Five consecutive transport
  failures stop the run with a provider-down screen (`SMARTPIPE_BREAKER`
  tunes; 0 disables) - or, with a configured fallback, the run switches
  chat models wholesale, re-runs the failed window on the successor, and
  the receipt splits counts per model. Embedding fallback is refused
  (one vector space per run). One fallback, no chains.
- **Deterministic rungs.** `join --on 'left.K == right.K'` alone is a free
  key-equality join (all kinds, `--unmatched`); with a prompt it becomes
  blocking - equality narrows pairs, the judge reasons within blocks, the
  receipt reports pairs avoided. `distinct --exact` folds byte/value
  duplicates only (records canonicalized, media hashed by bytes) - zero
  model calls. Verb tables mark the free rungs.

### The terminal behaves
- **A real progress bar.** When the total is knowable (file lists), the
  status line becomes `[██████░░░░░░░░░] 41% · 205/500 · 12/s · ~25s
  left` - fill and percent truncate so 100% is earned, the ETA appears
  once the first item completes, ASCII terminals get `[=====>....]`,
  and multi-stage runs prefix the stage name. Streams keep the
  count-and-rate line: a `tail -f` has no percent.
- **Braces extract lists of objects.** `{events {name string, when
  date, severity enum(low, high)}[]}` pulls every event out of an item
  in one call, typed and validated - inner dates still canonicalize to
  ISO, `--explode events` turns the list into one row each. One level
  deep by design; deeper nesting faults free at parse time with the
  two-pass hint.
- Result writes pause/erase/redraw the status line under an arbiter - the
  spinner can never interleave with output again. Only the final pipe stage
  (stdout a TTY) animates; mid-pipe stages stay line-atomic.
- Records render at the TTY as YAML-ish blocks. The welcome screen links
  docs, cookbook, and issues. The wizard offers to install shell
  completions (consented, idempotent).
- **`chart` re-platformed: plotext draws the terminal, matplotlib renders
  `--save`.** TTY bars get color (cyan bars, green time-series); piped or
  `NO_COLOR` output keeps the plain-ASCII contract byte-for-byte. `--save`
  now writes SVG or PNG by extension (identity-styled, deterministic
  output); svgwrite - unmaintained since 2022 - leaves the tree. Both
  libraries ship in core per the no-extras rule; imports stay
  function-local so startup holds. The run receipt now reads
  `run: ↑40 ↓25 tok` (arrows for in/out). The wizard validates model names
  before saving (two strikes, then a typed fault), prints completions
  before the paste-bait "Try it" line, prefers a sensible local default
  over alphabetical luck (cloud tags compete equally), and speaks in the
  same color voice as the other commands with a menu of detected local
  tags and provider-prefixed cloud examples.
- **Exit 141 is now deliberate, and the -13 flake is dead.** smartpipe used
  to arm the default SIGPIPE handler, so a downstream `| head` killed it by
  raw signal - and, one run in a few hundred, a stray EPIPE on asyncio's
  own self-pipe killed a healthy run mid-teardown, skipping the receipt
  (the CI flake that delayed rc3). SIGPIPE now stays ignored; a closed
  stdout surfaces as BrokenPipeError and exits a silent, deliberate 141
  with every finally honored. (click 8.4's own EPIPE-to-exit-1 trap is
  defused on the way.) One visible delta: `kill -PIPE` no longer
  terminates smartpipe; pipeline behavior is unchanged.
- **A files-then-stdin wait has a name.** Chaining positional files into a
  stdin read used to end in a silent hang when nothing was piped. All three
  chain paths now say once, on stderr:
  `note: files done - now reading stdin (pipe data or close it; files-only: add < /dev/null)`.
- **Docs bug, live-caught: `cluster | chart` drew flat bars.** The summary
  rows are one-per-cluster, so charting them counts every theme once. The
  README quick-tour step, the `cluster` help epilog, and the `.sem`
  pipeline example now route `cluster --explode members` into
  `chart cluster --top 8`, which draws real sizes.

### Every example, run for real
- **`map` and `extend` records finally carry their provenance.** The
  `__source` spine - the law said it rides every record - was being
  dropped by braces extraction: a reader-stamped row went in with
  `{"path", "as", "line"}` and came out with only the extracted fields.
  Every record leaving `map`/`extend` now carries `__source` (incoming
  spines adopted verbatim, fresh ones stamped from the item's
  provenance), which also means the terminal preview now shows the dim
  file-and-line origin under each block. `--bare` still strips it.
- **Docs truth sweep** (owner-reported): four `echo "... $1250"`
  examples silently shell-expanded to "250" - single-quoted; text files
  are declared one-item-per-line at first use; "everything outside the
  braces is the instruction" stated wherever braces are taught; typed
  braces are the norm across the examples; the playground corpus is
  linked from README, docs home, and the cookbook; the demo video plays
  on the docs front page.
- **Row cuts keep their rows through `filter` and `top_k`.** The grep-l
  contract (match whole files, get paths back) leaked onto `--as jsonl` /
  `--as lines` rows read from a positional file: every matching ROW came
  out as the filename. Both verbs now key path-back on the whole-file cut;
  rows pass through as records. Found by a zero-context agent driving
  smartpipe purely from SKILL.md; pinned in tests both ways.
- **The docs were audited by executing them**: all 236 example blocks
  (README, docs, SKILL files, CLI help) inventoried, statically checked,
  and ~105 run verbatim against a generated corpus of real-format files.
  Three code bugs surfaced and are fixed with pinning tests:
  - **Quoted globs reach the reader.** `smartpipe 'logs/*.jsonl'` - a
    documented form - exited 64 ("no verb"); unexpanded glob tokens now
    route to reader mode, and an unmatched glob errs loudly.
  - **Video is refused pre-send on wires that can't watch.** The
    ChatGPT-login (codex) and anthropic adapters silently dropped video
    parts and sent prompt-only requests - the model answered "no video
    provided", no degraded note fired, and video-RAG examples embedded
    that refusal prose as vectors. Both wires now raise before sending,
    so the documented ladder (`degraded: … video → frames+audio`)
    actually engages.
  - 7 broken and 12 misleading example sites corrected against observed
    behavior (SKILL files now run verbatim; the `.sem` key table is
    re-derived from the runtime's own validation errors; `__score`
    spelling; stale "oversized items are refused" claims rewritten for
    auto-chunking).

### The terminal shows your media
- **Every block at the terminal gets a number.** The human view opens
  each record block with a cyan `#N` - so "look at object 5" is a thing
  you can say. The ordinal is the one handle every output is guaranteed
  to have (provenance can repeat across rows - fifty pages of one PDF
  share a path - or be absent on plain text). Display only: piped
  output is byte-identical.
- **Media previews at the human view.** At a terminal (and through
  `readable`), the first media part of an item now renders under its
  summary line: images as color thumbnails, video as a three-frame strip
  sampled at 10/50/90% of its duration (never frame zero - intros are
  black), audio as a peak-envelope waveform. Audio and video that still
  live on disk get a clickable `▶ play (0:42, 2.1 MB)` link (OSC 8 -
  opens your system player in iTerm2/kitty/WezTerm/Ghostty/Windows
  Terminal). Piped output, JSONL, NO_COLOR, and `--bare` are
  byte-identical to before - previews exist only where a human is
  looking. `smartpipe config media-previews off` turns them off.

### Documents parse where they enter, vectors stay honest
- **The `ocr-model` role: a document parser at ingestion.** Set
  `ocr-model = "mistral/mistral-ocr-latest"` in config.toml (the setup
  flow's OCR stage stamps it; `SMARTPIPE_OCR_MODEL` and `--ocr-model`
  override per run) and ingested PDFs and images parse through it - one
  item per page on the `pages` cut, markdown as the text, disclosed per
  row and metered as a paid conversion. Mistral refs ride the dedicated
  `/v1/ocr` wire (live-verified, whole PDFs in one call); any other
  model ref works as chat-vision with extract-the-text framing, so
  `ollama/llava` is a free local OCR. Parse failures fall back to the
  local extraction ladder, disclosed, never fatal. Unset = exactly the
  old behavior.
- **Reader mode does what you configured.** `smartpipe scan.pdf` used to
  be pinned "zero model calls" even when you had set an ocr-model - the
  reader now honors the role with the same per-row disclosure, local
  fallback, and `--max-calls` belt as the verbs (plus a preflight note
  when more than 20 files will parse). No ocr-model configured = the
  same free path as always, pinned by a zero-HTTP-calls test. Found en
  route: the mistral OCR wire was never counted by `--max-calls`
  anywhere - it is belted now.
- **The `media-embed-model` role: one joint space for pixels and prose.**
  Text keeps `embed-model`; media routes to the joint-space model
  (jina-clip-v2 and friends). If a run would mix two vector spaces, the
  geometry fence stops it with exit 64 BEFORE any spend, naming both
  models and both fixes. A text query now ranks an image corpus in the
  joint space - that is the point.
- **Embed rows carry their provenance.** Output rows are now
  `{"text", "vector", "__embedder", "__source"}` - the spine like every
  other verb, plus the model that made the vector. `top_k` refuses a
  corpus whose stamp disagrees with the query's embedder (the
  same-dimensions-different-model trap), and reads old-style rows for
  one release. Two bugs died en route: records piped from the reader
  embedded their serialized JSON instead of their content text, and
  `--max-calls` silently demoted pixels to captions by stripping the
  media-embedding capability.

### Setup that reads the room
- **Rich now backs `config show` and `doctor`.** It is a deliberate core
  dependency under the no-extras policy, imported only while those human-facing
  screens render so `--help`, pipes, and `NO_COLOR` keep their existing contracts.
- **The interactive config wizard can go backward.** Model, embedding, OCR, and
  final review screens expose explicit Back rows; returning to a stage restores
  its pre-stage checkpoint, and typed model prompts accept `back`, `b`, `no`, or
  `n` instead of accidentally saving `ollama/n`.


- **Discarding config is terminal and interruption is ordinary.** Choosing
  discard no longer opens the shell-completion prompt after saying "Not saved";
  Ctrl-C or EOF at any Click prompt exits 130 instead of showing the internal-bug
  screen.

- **`auth login` now connects every provider, not just ChatGPT.** Pick
  from the full list - OpenAI appears twice because it really is two
  wires (API key vs ChatGPT login, and the login wire has no
  embeddings) - paste your key at a masked prompt after the create-a-key
  URL, and it validates live before storing (with an honest
  "store anyway - the provider may be having a bad minute" escape).
  Keys live at `~/.local/share/smartpipe/auth.json`, owner-only
  permissions from the first byte, masked everywhere they surface, and
  the environment variable always wins. `auth list` shows what's
  connected and which source is live; `auth logout` removes.
- **Setup walks three questions in order: text model, embeddings,
  document OCR.** Every stage shows every provider - connected ones
  badged, unconnected ones connectable inline without leaving the flow.
  The embedding stage only lists wires that embed; OCR is optional and
  skippable in one keypress. At the end, one consented verification
  pass (~5 tiny requests) probes what your models can actually do -
  and if the basic text check fails, smartpipe reports a setup fault
  instead of pretending your model lacks modalities. Results feed the
  capability chips you see in every picker from then on, alongside a
  public model registry and your own declarations for self-hosted
  models.

- **The schema workshop.** `smartpipe schema` with no arguments at a
  terminal opens a small interactive draft loop - your schema pinned up
  top as colored braces, commands scrolling below: `/add name type`,
  `/drop`, `/test data.jsonl` (per-field coverage bars against your real
  rows), `/example` (a synthetic instance), `/save` (writes schema.json
  and prints the paste-ready braces and `--schema` lines). Zero model
  calls by construction - iterate freely, commit when the bars are
  green. Paste any `{braces}` string to replace the draft. Piped and
  argument invocations are unchanged.
- **`smartpipe config` is now a provider-first picker.** Detect what's
  already connected (API keys in the environment, a ChatGPT login, local
  Ollama), pick a provider, pick from its LIVE model catalog - arrow keys
  on a real terminal, a numbered prompt everywhere else. Undetected
  providers show the exact `export ..._API_KEY=` line (smartpipe never
  prompts for or stores a key). Catalogs cache for a day; a failed fetch
  degrades to typed input with provider-prefixed examples. Picking a
  provider auto-pairs a coherent embedding model (announced, and never
  overwrites one you chose yourself), and one y/N question adds a backup
  `fallback-model` for provider outages. After a `doctor --probe`,
  catalog entries carry capability chips (`sees, hears - probed 2d ago`)
  - probed facts, never claims.
- **`smartpipe use` - setup is one word now.** `use gemini` (or
  `use claude-opus-4-8`, `use ollama/qwen3:8b`) stamps a coherent bundle
  in one shot: the model, its paired embedder, and the captions posture,
  each line disclosed with a ✓ and a reason. No credential for the named
  provider = a clean refusal that names the exact `auth login` command,
  nothing stamped. Bare `use` opens the interactive flow. `smartpipe
  using` answers "what am I running?" with every setting, its value, and
  WHERE it came from (flag, env, config file, default) in one aligned
  grid. The config.toml you get is signed by its door -
  `# stamped by: smartpipe use (2026-07-10T06:38Z)` - so a mystery
  config explains itself. The old setter subcommands (`config model`,
  `config embed-model`, …) and profiles are gone; an old config with
  profile keys loads with one warning and cleans itself on the next
  save.
- **The no-model dead end offers a hand.** At a real terminal, the
  no-model error now ends with `run setup now? [Y/n]` - accept, walk the
  same setup flow, and it closes with "saved - rerun your command".
  Scripts and CI see byte-identical output to before (the offer is
  TTY-gated), and the exit code stays honest.

### Getting it and keeping it current
- **One-line install on every platform.**
  `curl -LsSf https://prabal-rje.github.io/smartpipe/install.sh | sh`
  (macOS/Linux) and the `install.ps1` analog for Windows PowerShell, both
  published at the docs-site root. The script uses Homebrew when present,
  otherwise bootstraps uv and runs `uv tool install smartpipe-cli`;
  `SMARTPIPE_VERSION` pins a version; it ends with a loud
  `smartpipe --version` verify. brew/uv/pipx/pip remain as explicit
  alternatives.
- **The installers survive the real world.** Rerunning the one-liner
  upgrades in place instead of erroring (brew-, uv-, and
  otherwise-managed installs each get the right move); the uv bootstrap
  falls back to `wget` when `curl` is missing; Alpine/musl systems get
  a wheels warning before anything installs; Rosetta-translated macOS
  shells get an arch hint; GitHub Actions runners get `~/.local/bin`
  appended to `GITHUB_PATH`. Footguns adopted from studying opencode's
  installer. `install.sh` stays readable POSIX sh at 96 lines.
- **`smartpipe update`.** Detects how smartpipe was installed (homebrew,
  uv tool, pipx, or pip - fingerprinted from the executable path), shows
  the exact upgrade command, asks consent (`--yes` skips), runs it, and
  reports honestly. Unrecognized installs get the per-channel commands
  instead of a guess.
- **A calm update notice.** At most once a day smartpipe checks PyPI in a
  background thread (2 s cap, silent on any failure) and - on a later
  run, at a terminal, never in CI, never on stdout - prints one dim line:
  `note: smartpipe X.Y.Z is available (you have A.B.C) - run: smartpipe
  update`. Rc users aren't nagged about older stables. Off switches:
  `SMARTPIPE_NO_UPDATE_CHECK=1` or `smartpipe config update-check off`.
  The welcome screen's utility list now includes `update`.

### Docs: a shape, not a pile
- New Learn track (`docs/learn/1…6`), `concepts/the-item.md` (the five
  laws), `concepts/feeding-smartpipe.md` (the ingestion chapter), nav
  regrouped Learn/Verbs/Concepts/Cookbook/Reference, index as mode-router,
  every example reconciled to the new syntax with outputs from tests, qa/
  flows extended (a live walk of which caught and fixed a real write
  round-trip bug).

## [1.3.1] — 2026-07-08

### Fixed
- **PyPI no longer advertises phantom extras.** 1.3.0 kept the old extra
  names (`[files]`, `[video]`, …) as empty compat aliases - but nothing was
  ever published under the old name, so they protected nobody while the
  PyPI sidebar listed six extras that contradict the everything-in-the-box
  install story. The optional-dependencies table is gone entirely; pip
  warns on an unknown extra and installs the complete package regardless.
  Keywords now carry the multimodal story (pdf, audio, video, embeddings).

## [1.3.0] — 2026-07-07

Installs from PyPI as **`smartpipe-cli`** (the plain name was too close to
an existing project) - the command, import, and everything else you type
remain `smartpipe`.

The release that ships the identity and the polish: the **total rename to
smartpipe** (distribution, repo, command, internals; `sempipe` survives as a
command alias), the **usage ledger** (`smartpipe usage` — hour/day/week/
month/lifetime, resettable), the **multimodality-first pitch** with the
loved tagline kept, **the color voice** (ASCII wordmark, styled help,
colored doctor/probe with honest fallback marks), **video frame control**
(`--frame-every` guarantee vs `--max-frames` budget), and scoped-key 401s
that degrade per-item with the provider's real reason quoted.


### Changed
- **No optional extras, ever (D46).** Claude models, document parsing
  (PDF/DOCX/PPTX/XLSX/HTML/EPUB), video (bundled static ffmpeg), and chart
  `--save` all ship in the core install now, joining D44's whisper and local
  embeddings. One `pip install smartpipe-cli` is the entire multimodal surface.
  The old extra names briefly remained as empty aliases (removed for real
  in 1.3.1 - nothing was ever published under the old name, so they only
  advertised a false install story). `doctor`'s extras row now verifies the bundled components and
  FAILS on a broken install instead of suggesting installs.
- **The gemini profile's default chat model is `gemini-3.1-flash-lite` (D45).**
  Verified against the live catalog (GA, not a preview) and through the
  ability probe: sees, hears, and watches video natively. Wizard tips and
  doc examples updated; family-wide claims ("gemini-2.5-*" hears/watches)
  generalized to gemini models, which was already how the wire treats them.
- **Seamless beats slim (D44).** Local transcription (faster-whisper) and
  local embeddings (fastembed) moved from optional extras into the core
  install: `smartpipe embed` now works on a fresh machine with NOTHING
  running - the default embedder is on-device nomic-embed-text v1.5 (768-dim,
  one ~130 MB download on first use, disclosed; one engine per run, reused
  across batches). Ollama is no longer required for embeddings; the `[audio]`
  extra is gone and every "install smartpipe[audio]" message with it. The
  frozen-dependency snapshot was refreshed deliberately - the wheel now pulls
  onnxruntime and ctranslate2, and that trade is the point.
- **The privacy story is provider-honest now.** "Local-first" overclaimed:
  the docs, README, and descriptions now lead with "your data goes to
  whichever endpoint you configure" - local pieces (embeddings, whisper)
  on-device regardless, cloud models named as cloud, consent gates spelled
  out.
- **Docs readability pass (owner review).** Every multi-stage pipe example
  is now Kusto-style - one stage per line with backslash continuations; em
  dashes removed from the docs entirely; the verbs nav nested into four
  groups (Transform / Find & match / Group & compare / Free utilities); a
  five-line "Unix toolbox" primer in pipes-and-items with kindness links
  where jq first appears.

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
  Install: `pip install smartpipe-cli`.

### Fixed
- **A restricted key refusing MEDIA no longer kills the run (D43c).** The
  owner's key proved the case: text chat 200, image-bearing chat 401
  "Missing scopes: model.request". A scope-401 on a media request is a
  capability statement about the key, not a dead key — it now degrades
  per-item (the ladders and skip machinery take over, the skip line quotes
  the scope and the fix), while a scope-401 on plain text stays properly
  fatal.
- **401s now quote the provider's actual reason.** A scope-restricted key
  (text chat and whisper fine, media chat forbidden) used to read as "check
  the key" — a live goose chase. The screen now includes the server's
  message ("Missing scopes: model.request") and, when the reason is a
  scope/permission, says the key is RESTRICTED and where to fix it.
- **files.md's video section was two designs stale** (six frames,
  track-only text verbs) — rewritten for the 1 fps/24 default, the D43
  flags, and the D36 halves pivot.

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
  And the streaming path (piped stdin) skipped the native media route
  entirely - image-only items caption-pivoted even on jina-clip while the
  finite-corpus branch embedded pixels. Both branches now share one router
  (live-verified: `dims: 1024` from jina-clip-v2 on a piped image).
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
