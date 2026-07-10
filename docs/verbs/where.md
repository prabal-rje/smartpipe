# where - the free filter

Keep rows matching a deterministic predicate. **Free - `where` never calls a
model.** Streams, preserves input order, and emits matching rows byte-for-byte.

```bash
tail -f app.log \
| smartpipe where 'text has "ERROR"'
cat orders.jsonl \
| smartpipe where 'total > 1000'
```

Rule of thumb: **`where` for facts, `filter` for judgment** - the
filter-early habit is [Learn chapter 4](../learn/4-free-verbs.md).

## The predicate language

| Form | Meaning |
|---|---|
| `FIELD has "word"` | word-bounded match, case-insensitive |
| `FIELD contains "text"` | substring, case-insensitive |
| `FIELD matches /re/` | Python regex search, case-sensitive |
| `FIELD == VALUE` / `!=` | numeric when both sides are numbers, temporal when both are ISO dates/datetimes, else exact string |
| `FIELD > >= < <=` | numeric, or temporal when both sides are ISO dates/datetimes - anything else never matches (and is disclosed) |
| `and` · `or` · `not` · `( )` | `not` binds tightest, then `and`, then `or` |

`FIELD` is a record field name, or `text` for the whole line (on plain-text
input, `text` is the only field there is). It can also be a
[field path](../concepts/structured-output.md#field-paths-reading-nested-data)
into nested records - `where 'user.plan has "pro" and items[0].total >= 100'` -
where a literal flat column named `user.plan` wins first and a path miss
counts as an ordinary missing field. Booleans compare by their JSON
spelling: `pass == true`. Temporal comparison (`due >= "2026-01-01"`) kicks
in when both sides read as ISO dates/datetimes - a bare date counts as its
midnight, so a `{due date}` field extracted upstream compares as time, not
text.

## Missing fields

Missing fields evaluate false - streams keep flowing - but the run ends with
a disclosure noting which fields were missing:

```
where: 2,114 of 104,882 matched
note: field 'level' missing on 12,004 rows
```

Zero matches exits 0 (an empty result is a valid result, like `filter`).
A predicate the grammar can't parse exits 64 with the full operator menu,
before any input is read.
