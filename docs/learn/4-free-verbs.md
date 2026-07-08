# 4 · The free verbs

Every command in this chapter runs at zero model calls. The habit to build:
**cut the corpus with free verbs first**, then let the paid verbs judge only
what is left.

## Filter deterministically: where

```bash
cat logs.jsonl \
| smartpipe where 'level == "error" and not text contains "retry"' \
| smartpipe map "root cause? {cause}"
```

`where` is SQL-WHERE for your pipe: field predicates, comparisons, `and/or/not`.
A typo in the predicate fails before reading stdin, with the operator menu.

## Count and aggregate: summarize · chart · getschema

```bash
smartpipe getschema < tickets.jsonl                      # fields, types, coverage
smartpipe summarize 'count(), avg(total) by region' < tickets.jsonl
smartpipe where 'level == "error"' < logs.jsonl | smartpipe chart --by-time ts:1h
```

`getschema` first, always: it shows the dirt (mixed types, missing fields)
before the dirt costs you skipped rows.

## Trim and dedupe: sample · sort · distinct --exact · join --on

```bash
smartpipe sample 20 < corpus.jsonl        # seeded: same 20 every run
smartpipe distinct --exact < rows.jsonl   # fold byte-identical items, free
smartpipe join --on 'left.sku == right.sku' --right invoices.jsonl \
    --kind anti < orders.jsonl            # key-equality join, free
```

`distinct` without `--exact` and `join` with a prompt climb to embeddings and
judge calls - the free forms exist so you only pay when meaning is actually
needed.

## While you iterate

`smartpipe sample 20` on the front of a pipeline makes every experiment cost
twenty items instead of the whole corpus. Combined with the result cache
(chapter 5), iterating on a prompt costs almost nothing.

Next: [5 · Scale and cost](5-scale-and-cost.md) - budgets, caches, and what
happens when a provider goes down mid-run.
