# Manual release QA — the human pass

The automated gates (`make gates`, goldens, the live matrix) are the primary
defense; this checklist is the **redundant human layer** a maintainer walks
end to end before every release. It is deliberately minimal: ~20 minutes,
one flow per critical path, every command copy-pasteable from the repo root.

Fixtures live in `qa/fixtures/` (regenerate with
`uv run python qa/make_fixtures.py` — deterministic, the diff must be
empty). `tickets.jsonl` is ~115 rows with planted duplicates, an outlier,
missing fields, and mixed types; `logs.jsonl` has an incident spike at
15:00; every 7th order in `orders.jsonl` has no invoice.

**Conventions:** run with a configured cloud profile unless the step says
otherwise; add `--max-calls` belts exactly as written; a step passes only if
EVERY listed check holds. If anything surprises you, stop and file it — do
not rationalize.

---

## 0. Setup surface (5 checks)

```console
$ smartpipe                      # bare
$ smartpipe config show
$ smartpipe doctor
$ smartpipe doctor --probe
$ smartpipe --version
```
- [ ] Bare `smartpipe` prints the welcome with verbs grouped into "call a
      model" vs "free utilities"; exit 0.
- [ ] `config show` matches your real setup; no secrets shown.
- [ ] `doctor` ends with the bright-yellow "--probe" hint.
- [ ] `--probe` shows the modality matrix; rows agree with your provider.
- [ ] Version matches the release you're cutting.

## 1. The typed-extraction spine

```console
$ head -25 qa/fixtures/tickets.jsonl | smartpipe map \
    "Extract {label enum(bug, praise, request), product string: what part}" --tally label --max-calls 30
```
- [ ] 25 NDJSON rows, every `label` one of the three enum values.
- [ ] Live tally on the status line while running; final tally on stderr.
- [ ] The status line shows the token segment (`↑… ↓… tok`).
- [ ] The run ends with a `run: … tokens` receipt on stderr.

## 2. Enrichment keeps the record

```console
$ head -10 qa/fixtures/tickets.jsonl | smartpipe extend "Add {sentiment enum(pos, neg)}" --max-calls 12
```
- [ ] Output rows carry ALL original fields (`id`, `customer`, `ts`, …)
      plus `sentiment` — nothing dropped.

## 3. Free gates before paid judges

```console
$ smartpipe where 'total > 100 and region == "EU"' < qa/fixtures/tickets.jsonl | head -3
$ smartpipe where 'text has "ERROR"' < qa/fixtures/tickets.jsonl
$ smartpipe where 'total >>> 5' < qa/fixtures/tickets.jsonl; echo "exit=$?"
```
- [ ] First: rows pass through byte-identical (spacing intact).
- [ ] Second run's closing note discloses the missing-field counts honestly.
- [ ] Third: exit 64 BEFORE reading stdin, full operator menu printed.

## 4. The dedupe/cluster/outlier trio (embeddings)

```console
$ smartpipe distinct --show-groups < qa/fixtures/tickets.jsonl 2>&1 | tail -5
$ smartpipe cluster --top 5 < qa/fixtures/feedback.txt --max-calls 12
$ smartpipe outliers 3 < qa/fixtures/tickets.jsonl
```
- [ ] distinct's receipt shows BOTH exact and near folds (the fixture
      plants 4 exact dupes); groups read sensibly.
- [ ] cluster prints the cost preview BEFORE any call; rows are
      largest-first with sane labels; `(other)` row appears.
- [ ] outliers ranks the kernel soft-lockup row first, with the
      median-anchored stderr line.

## 5. diff and anti-join (the comparative pair)

```console
$ smartpipe where 'level == "error"' < qa/fixtures/logs.jsonl > /tmp/qa-errors.jsonl
$ smartpipe where 'level == "info"'  < qa/fixtures/logs.jsonl | smartpipe diff --right /dev/stdin < /tmp/qa-errors.jsonl --max-calls 20
$ smartpipe join "order {left.desc} and invoice {right.item} name the same product" \
    --right qa/fixtures/invoices.jsonl --kind anti --max-calls 60 < qa/fixtures/orders.jsonl
```
- [ ] diff reports payment/timeout themes lopsided to the LEFT with both
      shares; shared themes fold into a counted note.
- [ ] anti-join emits ~5 orders, verbatim JSON — the every-7th products
      with no invoice; the matched/unmatched summary prints.

## 6. The free reporting suite

```console
$ smartpipe getschema < qa/fixtures/tickets.jsonl
$ smartpipe summarize 'count(), avg(total), p95(total) by region' < qa/fixtures/tickets.jsonl
$ smartpipe chart --facet region,customer < qa/fixtures/tickets.jsonl
$ smartpipe where 'level == "error"' < qa/fixtures/logs.jsonl | smartpipe chart --by-time ts:1h
$ smartpipe sample 10 < qa/fixtures/tickets.jsonl | sha256sum
$ smartpipe sample 10 < qa/fixtures/tickets.jsonl | sha256sum
```
- [ ] getschema shows the `total` type UNION (number|string) and region
      coverage below 100% — the planted dirt.
- [ ] summarize groups under `null` visibly for missing regions; the
      non-numeric `total` skips are counted on stderr.
- [ ] The time chart shows the 15:00 spike, zero-filled gaps included.
- [ ] The two sample hashes are IDENTICAL (seeded by default).

## 7. Media in, meaning out

```console
$ smartpipe filter "mentions anything" --allow-captions --max-calls 6 < qa/fixtures/media.jsonl
$ smartpipe map "describe this" --max-calls 6 < qa/fixtures/media.jsonl
```
- [ ] Per-row conversion notes name the path taken (whisper-1 / heard by /
      described by), and the receipt shows audio duration + image MB.
- [ ] With an OpenAI key and no stt-model configured, audio says
      `transcribed by openai/whisper-1` (the auto-matrix).
- [ ] Grab ANY real PDF with pictures: `smartpipe map "summarize" --in that.pdf`
      → figures-attached note; a scanned PDF says "thin text layer … routed
      … to the vision path".

## 8. Pipelines and custom verbs

```console
$ smartpipe run qa/fixtures/triage.sem --dry-run
$ smartpipe run qa/fixtures/triage.sem < qa/fixtures/logs.jsonl
$ mkdir -p ~/.config/smartpipe/verbs && cp qa/fixtures/triage.sem ~/.config/smartpipe/verbs/qa-triage.sem
$ smartpipe qa-triage < qa/fixtures/logs.jsonl && rm ~/.config/smartpipe/verbs/qa-triage.sem
```
- [ ] dry-run prints both stages with cost postures, runs nothing.
- [ ] The run emits hourly error counts; stage receipts are `[hot]`-prefixed.
- [ ] The custom verb behaves identically to `run`.

## 9. Cache round-trip

```console
$ SMARTPIPE_CACHE=1 smartpipe map "Extract {label}" --max-calls 12 < qa/fixtures/feedback.txt > /tmp/qa-a.jsonl
$ SMARTPIPE_CACHE=1 smartpipe map "Extract {label}" --max-calls 2  < qa/fixtures/feedback.txt > /tmp/qa-b.jsonl
$ diff /tmp/qa-a.jsonl /tmp/qa-b.jsonl && echo IDENTICAL
$ smartpipe cache stats && smartpipe cache clear
```
- [ ] Run 2 says `cache: N hits · 0 calls` and SUCCEEDS under
      `--max-calls 2` (hits don't spend budget); outputs identical.
- [ ] stats shows entries/size/age; clear reports MB freed.

## 10. Unix citizenship (the contract checks)

```console
$ smartpipe map "translate to French" --max-calls 3 < qa/fixtures/feedback.txt 2>/dev/null | head -2
$ yes "hello" | smartpipe where 'text has "hello"' | head -1; echo "exit=$?"
$ head -40 qa/fixtures/tickets.jsonl | smartpipe map "Extract {label}" --max-calls 50   # press Ctrl-C mid-run
```
- [ ] With stderr discarded, stdout is PURE results (`| jq .` never chokes).
- [ ] The `| head` pipe exits promptly (SIGPIPE handled, exit 141 or 0).
- [ ] Ctrl-C drains gracefully: partial results flushed, interrupted
      summary printed, exit 130.

---

**Sign-off:** all boxes checked, or issues filed with the failing command
verbatim. Then, and only then, tag.
