# `join` — match two inputs, semantically

Merge stdin against a second input wherever a plain-English predicate holds.
The SQL join's semantic cousin: no keys, no exact equality — meaning.

```console
$ cat tickets.jsonl | sempipe join "ticket {left.text} concerns product {right.name}" --right products.jsonl
{"left": {"text": "the laser printer keeps smoking"}, "right": {"name": "LaserJet 9"}, "_score": 0.91}
```

## How it works (and why it's affordable)

A naive semantic join would ask the model about every pair — 1,000 × 1,000 =
a million calls. sempipe does **embed → block → judge**:

1. **Embed** the `--right` file once (it's read whole and indexed in memory).
2. **Block**: each stdin item is embedded and matched to its `--k` nearest
   right-side candidates by similarity — pure math, no model calls.
3. **Judge**: only those candidate pairs go to the chat model, with a
   yes/no verdict prompt.

Cost is `lines × k`, never `lines × right-size`. Before the first judge call,
sempipe tells you the worst case on stderr (when it exceeds a couple hundred):

```
join: 1,204 left items · up to 5 candidates each = at most 6,020 model calls (cap with --max-calls)
```

## The predicate

Braces name a side's field — every brace must pick a side:

- `{left.text}` / `{right.text}` — the whole item (for JSON items without a
  `text` field, the raw line).
- `{left.body}`, `{right.name}`, … — a JSON field; a pair whose item lacks the
  field is skipped with a warning naming what it *does* have.
- A predicate must mention **both** sides — one that reads only one side would
  match everything or nothing, so it's refused up front.

## Options

| Option | Meaning |
|---|---|
| `--right FILE` | The finite side to index (JSONL or plain lines). Required; never stdin |
| `--k N` | Candidates judged per left item (default 5 — **the recall knob**, see below) |
| `--threshold FLOAT` | Similarity floor (0–1) a candidate must clear before judging |
| `--model TEXT` | Chat model for the judge calls |
| `--embed-model TEXT` | Embedding model for both sides |
| `--fields A,B` | Project output columns — dotted paths reach the sides: `--fields left.id,right.name,_score` |
| `--output FORMAT` | `auto` · `json` · `csv` · `tsv` |
| `--concurrency N` / `--max-calls N` | Parallel left items / hard cost ceiling |

## Output

One record per **matched pair**, in left-input order, a left item's matches
consecutive and ranked by similarity:

```json
{"left": {…}, "right": {…}, "_score": 0.87}
```

The sides stay nested (never flat-merged) so identical field names on both
sides can't corrupt each other. Exit codes are the usual contract: `0` all
judged (zero matches is still success, like `grep`), `1` some pairs or lines
skipped, `2`/`64` per the taxonomy.

## `--k` is a recall knob, not a performance knob

Candidates that blocking drops are matches the judge never sees. On a synthetic
40×40 corpus with known ground truth (`make join-eval`):

| k | recall of true matches |
|---|---|
| 1 | 0.20 |
| 3 | 0.56 |
| **5 (default)** | **0.85** |
| 10 | 1.00 |

Real numbers depend on your embedding model and data. **The spot-check:** rerun
a sample with `--k 20 --threshold 0` and compare match counts — a jump means
the default is dropping true matches; raise `--k` (and consider a stronger
embedding model).

## Streaming

The left side streams flag-free, like every per-item verb — so join is a live
enrichment operator:

```console
$ tail -f events.log | sempipe join "event {left.text} involves customer {right.name}" --right customers.jsonl
```

The right side can never stream (an index can't be built from a tail): `--right -`
is a usage error that says so.

## See also

- [Cookbook: live stream enrichment](../cookbook/stream-enrichment.md)
- [`top_k`](top-k.md) — ranking against one query instead of matching two sets
- [`.sem` files](../reference/sem-files.md) — save a join as an executable stage
