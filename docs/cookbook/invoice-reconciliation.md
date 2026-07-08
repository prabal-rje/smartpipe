# Invoice reconciliation

**Goal:** turn a folder of scanned vendor invoices into structured rows, then find
the ones that never made it into the ledger. No OCR toolchain, no CSV wrangling.

## The month-end close, one pipe

```bash
# Scanned invoices become rows (the vision model IS the OCR), then anti-join
# the ledger export: unmatched invoices ARE the deliverable
smartpipe map "Extract {vendor string, invoice_number string, invoice_date string, total number, currency string}" 'invoices/2026-06/*.pdf' \
| tee june-invoices.ndjson \
| smartpipe join "invoice from {left.vendor} for {left.total} and ledger entry {right.memo} for {right.amount} record the same payment" --right ledger.jsonl --kind anti > missing-from-ledger.jsonl
```

Three things happen:

1. **`map` reads the scans directly.** A scanned page routes itself to a vision
   model and says so per row ([file inputs](../inputs/files.md)) - so a shoebox
   of PDFs becomes typed NDJSON, with `total` a real number rather than a string.
2. **`tee` keeps the middle artifact.** `june-invoices.ndjson` is the month's
   structured invoice table: greppable, `jq`-able, and next month's audit trail.
3. **`join --kind anti` emits exactly the worklist.** The
   [anti-join](../verbs/join.md#join-kinds) is reconciliation's native shape -
   every invoice that matched no ledger entry comes out verbatim, ready to chase.

Note the predicate reads both sides and claims only what the text can support
("record the same payment", not "is the same"); see
[writing predicates](../verbs/join.md#writing-predicates-a-judge-can-satisfy).

## When late invoices arrive

Re-run the same pipe. With the result cache on (`smartpipe config cache on`),
already-extracted invoices and already-judged pairs are free - the re-run pays
only for the new files.

## If matches look too strict

Blocking drops candidates the judge never sees. Spot-check by re-running a
sample with `--k 20 --threshold 0` and comparing match counts - the recall
knob is explained on the [`join`](../verbs/join.md) page.

## See also

- [`join`](../verbs/join.md) · [`map`](../verbs/map.md) ·
  [File inputs](../inputs/files.md) ·
  [Contract & document extraction](contract-extraction.md)
