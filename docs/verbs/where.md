# where — the free filter

Keep rows matching a deterministic predicate. **Free — `where` never calls a
model.** Streams, preserves input order, and emits matching rows byte-for-byte.

```console
$ tail -f app.log | sempipe where 'text has "ERROR"'
$ cat orders.jsonl | sempipe where 'total > 1000'
```

## The filter-early idiom

`where` exists so the paid stages only see what matters — the same discipline
KQL enforces ("cheapest predicate first"):

```console
$ cat app.log | sempipe where 'text has "ERROR"' | sempipe filter "an actual outage, not a retry storm"
```

On a 100k-line log, judging every line costs real money; `where` first cuts
the corpus for free, often 50×. Rule of thumb: **`where` for facts, `filter`
for judgment.**

## The predicate language

| Form | Meaning |
|---|---|
| `FIELD has "word"` | word-bounded match, case-insensitive (KQL semantics) |
| `FIELD contains "text"` | substring, case-insensitive |
| `FIELD matches /re/` | Python regex search, case-sensitive |
| `FIELD == VALUE` / `!=` | numeric when both sides are numbers, else exact string |
| `FIELD > >= < <=` | numeric only — non-numbers never match (and are disclosed) |
| `and` · `or` · `not` · `( )` | `not` binds tightest, then `and`, then `or` |

`FIELD` is a record field name, or `text` for the whole line (on plain-text
input, `text` is the only field there is). Booleans compare by their JSON
spelling: `pass == true`.

## Honest silence

Missing fields evaluate false — streams keep flowing — but the run ends with
a disclosure so silence never lies:

```
where: 2,114 of 104,882 matched
note: field 'level' missing on 12,004 rows
```

Zero matches exits 0 (an empty result is a valid result, like `filter`).
A predicate the grammar can't parse exits 64 with the full operator menu,
before any input is read.
