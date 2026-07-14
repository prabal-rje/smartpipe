# Manual release QA — the human pass

The automated gates (`make gates`, goldens, the live matrix) are the primary
defense; this checklist is the **redundant human layer** a maintainer walks
end to end before every release. It is deliberately minimal: ~20 minutes,
one flow per critical path, every command copy-pasteable from the repo root.

Fixtures live in `qa/fixtures/` (regenerate with
`uv run python qa/make_fixtures.py` — deterministic, the diff must be
empty). `tickets.jsonl` is ~115 rows with planted duplicates, an outlier,
missing fields, and mixed types; `logs.jsonl` has an incident spike at
15:00; every 7th order in `orders.jsonl` has no invoice; `big_report.txt`
is ~0.5 MB of deliberately oversized filler with the headline finding
planted in its closing paragraph.

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
$ head -3 qa/fixtures/tickets.jsonl | smartpipe map "Extract {label}" --dry-run
```
- [ ] 25 JSONL rows, every `label` one of the three enum values.
- [ ] Live tally on the status line while running; final tally on stderr.
- [ ] The status line shows the token segment (`↑… ↓… tok`).
- [ ] The run ends with a `run: … tokens` receipt on stderr.
- [ ] `--dry-run` prints system/schema/user sections for the FIRST row only,
      exits 0, spends nothing.

## 2. Enrichment keeps the record

```console
$ head -10 qa/fixtures/tickets.jsonl | smartpipe extend "Add {sentiment enum(pos, neg)}" --max-calls 12
```
- [ ] Output rows carry ALL original fields (`id`, `customer`, `ts`, …)
      plus `sentiment` — nothing dropped.

## 3. Free gates before paid judges

```console
$ smartpipe where 'total > 100 and region == "EU"' < qa/fixtures/tickets.jsonl | head -3
$ smartpipe where 'level == "error"' < qa/fixtures/tickets.jsonl
$ smartpipe where 'total >>> 5' < qa/fixtures/tickets.jsonl; echo "exit=$?"
```
- [ ] First: rows pass through byte-identical (spacing intact).
- [ ] Second run's closing note discloses the missing-field counts honestly.
- [ ] Third: exit 64 BEFORE reading stdin, full operator menu printed.

## 4. The dedupe/cluster/outlier trio (embeddings)

```console
$ smartpipe distinct --show-groups < qa/fixtures/tickets.jsonl 2>&1 | tail -5
$ smartpipe distinct --exact < qa/fixtures/tickets.jsonl 2>&1 | tail -1
$ smartpipe cluster --k 8 --top 5 < qa/fixtures/feedback.txt --max-calls 12
$ smartpipe outliers 3 < qa/fixtures/tickets.jsonl
```
- [ ] distinct's receipt shows BOTH exact and near folds (the fixture
      plants 4 exact dupes); groups read sensibly.
- [ ] `--exact` folds the 4 planted dupes instantly, receipt says
      `… + 0 near duplicates folded`, and NO model is contacted.
- [ ] cluster prints the cost preview BEFORE any call; rows are
      largest-first with sane labels; `(other)` row appears.
- [ ] outliers ranks the kernel soft-lockup row first, with the
      median-anchored stderr line.

## 5. diff and anti-join (the comparative pair)

```console
$ smartpipe where 'level == "error"' < qa/fixtures/logs.jsonl > /tmp/qa-errors.jsonl
$ smartpipe where 'level == "info"' < qa/fixtures/logs.jsonl > /tmp/qa-info.jsonl
$ smartpipe diff --right /tmp/qa-info.jsonl < /tmp/qa-errors.jsonl --max-calls 20
$ sed -n '2,3p' qa/fixtures/orders.jsonl | smartpipe join \
    "order {left.desc} and invoice {right.item} name the same product" \
    --model openai/gpt-5.4-mini \
    --right qa/fixtures/invoices.jsonl --k 3 --max-calls 20
```
- [ ] diff reports payment/timeout themes lopsided to the LEFT and routine
      HTTP/cron themes to the RIGHT, with both shares on every row.
- [ ] The semantic join emits sensible matches for both left rows and finishes
      below its belt.

```console
$ smartpipe join --on 'left.sku == right.sku' --right qa/fixtures/invoices.jsonl \
    --kind anti < qa/fixtures/orders.jsonl
```
- [ ] The key join runs FREE (no model call, instant) and finds the same
      five every-7th-SKU orders with no invoice.

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
$ smartpipe filter "mentions anything" --allow-captions --max-calls 8 < qa/fixtures/media.jsonl
$ smartpipe map "describe this" --max-calls 6 < qa/fixtures/media.jsonl
```
- [ ] Per-row conversion notes name the path taken (whisper-1 / heard by /
      described by), and the receipt shows audio duration + image MB.
- [ ] With an OpenAI key and no stt-model configured, audio says
      `transcribed by openai/whisper-1` (the auto-matrix).
- [ ] Grab ANY real PDF with pictures: `smartpipe map "summarize" that.pdf`
      → figures-attached note; a scanned PDF says "thin text layer … routed
      … to the vision path".

## 8. The read/write mirror (free)

```console
$ smartpipe qa/fixtures/feedback.txt --as lines | head -3
$ smartpipe qa/fixtures/feedback.txt --as lines | smartpipe write '/tmp/qa-mirror/{name}'
$ diff qa/fixtures/feedback.txt /tmp/qa-mirror/feedback.txt && echo MIRRORED
$ head -3 qa/fixtures/tickets.jsonl | smartpipe readable | head -8
```
- [ ] Reader mode emits one record per line with a `__source` spine
      (`{"path": …, "as": "lines", "line": 1}`).
- [ ] write prints the path it wrote; the round-tripped file diffs EMPTY
      (reassembled in spine order).
- [ ] readable renders indented blocks — no JSON braces, spine dimmed last.

## 9. Pipelines and custom verbs

```console
$ smartpipe run qa/fixtures/triage.sem --dry-run
$ smartpipe run qa/fixtures/triage.sem < qa/fixtures/logs.jsonl
$ mkdir -p ~/.config/smartpipe/verbs && cp qa/fixtures/triage.sem ~/.config/smartpipe/verbs/qa-triage.sem
$ smartpipe qa-triage < qa/fixtures/logs.jsonl && rm ~/.config/smartpipe/verbs/qa-triage.sem
```
- [ ] dry-run prints both stages with cost postures, runs nothing.
- [ ] The run emits hourly error counts; stage receipts are `[hot]`-prefixed.
- [ ] The custom verb behaves identically to `run`.

## 10. Cache round-trip

```console
$ SMARTPIPE_CACHE=1 smartpipe map "Extract {label}" --max-calls 12 < qa/fixtures/feedback.txt > /tmp/qa-a.jsonl
$ SMARTPIPE_CACHE=1 smartpipe map "Extract {label}" --max-calls 2  < qa/fixtures/feedback.txt > /tmp/qa-b.jsonl
$ diff /tmp/qa-a.jsonl /tmp/qa-b.jsonl && echo IDENTICAL
$ smartpipe cache stats && smartpipe cache clear
```
- [ ] Run 2 says `cache: N hits · 0 calls` and SUCCEEDS under
      `--max-calls 2` (hits don't spend budget); outputs identical.
- [ ] stats shows entries/size/age; clear reports MB freed.

## 11. Unix citizenship (the contract checks)

```console
$ smartpipe map "translate to French" --max-calls 3 < qa/fixtures/feedback.txt 2>/dev/null | head -2
$ yes "hello" | smartpipe where 'text has "hello"' | head -1; echo "exit=$?"
$ head -40 qa/fixtures/tickets.jsonl | SMARTPIPE_BATCH=off smartpipe map "Extract {label}" --max-calls 50   # press Ctrl-C mid-run
```
- [ ] With stderr discarded, stdout is PURE results: two French text lines,
      with no receipt, progress frame, warning, or ANSI byte mixed in.
- [ ] The `| head` pipe exits promptly (SIGPIPE handled, exit 141 or 0).
- [ ] Ctrl-C drains gracefully: partial results flushed, interrupted
      summary printed, and the exit reflects drained work (0 when everything
      that ran succeeded; 1 when any item skipped).

## 12. Oversized items are handled, not skipped (D26 v2)

```console
$ smartpipe map "What is the headline finding? Include the exact percentage." qa/fixtures/big_report.txt --max-calls 12 < /dev/null
$ smartpipe map "What is the headline finding? Include the exact percentage." qa/fixtures/big_report.txt --whole < /dev/null; echo "exit=$?"
```
- [ ] BEFORE any spend, the chunk note prints on stderr:
      `note: qa/fixtures/big_report.txt ~125,… tokens over budget - N chunks
      + 1 combine call` (the exact counts vary by provider window).
- [ ] Exactly ONE summary line lands on stdout, and it mentions the
      Rotterdam automation pilot / 23 percent unit-cost drop — the planted
      headline lives in the LAST chunk, so only a real chunk+combine pass
      finds it.
- [ ] The `run: … tokens` receipt shows the large observed input total across
      every chunk call — nothing hidden.
- [ ] The `--whole` run spends NOTHING: one `⚠ skipped: …token budget —
      split it first…` line with the split recipe, empty stdout, exit=3.

---

**Sign-off:** all boxes checked, or issues filed with the failing command
verbatim. Then, and only then, tag.
