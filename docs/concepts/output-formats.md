# Output formats

smartpipe adapts its output to where it's going, and lets you override that with
`--output`. The goal: readable at your terminal, machine-clean in a pipe.

## The default: `auto`

With no `--output` flag, smartpipe looks at whether stdout is a terminal:

| Result kind | At a terminal | Piped / redirected |
|---|---|---|
| Plain text | the text | the text |
| Structured (JSON) | a readable key/value view | NDJSON (one object per line) |

So `smartpipe map "Extract {name, role}"` shows you a tidy view on screen, but the
moment you pipe it into `jq` or a file, it becomes clean NDJSON — no flags needed.

## Explicit formats

| `--output` | What you get |
|---|---|
| `text` | plain text, one line per item |
| `json` | NDJSON, even at a terminal |
| `csv` | comma-separated, with a header row |
| `tsv` | tab-separated, with a header row |

You can also set a default with the `SEMPIPE_OUTPUT` environment variable.

## CSV & TSV — for spreadsheet people

Point structured output straight into a spreadsheet:

```console
$ cat cards.txt | smartpipe map "Extract {name, email, role}" --output csv > people.csv
```

```
name,email,role
Ada Lovelace,ada@example.com,engineer
Grace Hopper,grace@example.com,admiral
```

The rules that make it a real table:

- **A rectangle is the contract.** The columns are fixed by the first record (or by
  the schema order). Every later row lines up with that header.
- **Missing values are empty cells;** a key that shows up *after* the header is
  dropped, with a one-time warning on stderr — so the shape stays stable.
- **Nested values** (objects, arrays) become compact JSON inside one cell.
- **CSV is RFC 4180** (proper quoting, CRLF line endings); **TSV** uses tabs, and
  replaces any tab or newline inside a value with a space (a tab inside a TSV cell
  would corrupt the columns).
- **`top_k` scores land last.** A `_score` column sorts to the right of your data,
  where a spreadsheet reader expects it.

CSV and TSV need **structured** output — there have to be named columns. Ask for
fields with braces or `--schema`; a plain-text prompt with `--output csv` is a
usage error that tells you so.

```console
$ echo hi | smartpipe map "shout" --output csv
error: --output csv needs structured output — a table needs named columns
  add braces to the prompt (e.g. "Extract {name, email}") or pass --schema
```

## `--fields` — pick and order your columns

`--fields a,b` projects structured output down to just those fields, in exactly
that order. It works the same in every format — NDJSON, the terminal view, CSV,
and TSV — on `map`, `embed`, `top_k`, and `reduce`:

```console
$ cat cards.txt | smartpipe map "Extract {name, email, role}" --fields name,email --output tsv
name	email
Ada Lovelace	ada@example.com
Grace Hopper	grace@example.com
```

The projection rules:

- **Order is yours.** Columns appear in the order you list them, not the order the
  model produced.
- **The shape never wobbles.** A field a result doesn't carry stays in place —
  `null` in NDJSON, an empty cell in CSV/TSV, a blank value at the terminal — and
  you get a one-time warning on stderr naming it.
- **Dropping the rest is the point.** Fields you didn't ask for are omitted
  silently.
- **Structured output only.** On a plain-text run there are no named fields to
  pick from, so `--fields` is a usage error that says exactly that. (`filter`
  never has the flag — its output is a byte-faithful subset of its input.)

```console
$ cat notes.txt | smartpipe map "shout" --fields name
error: --fields selects columns from structured output
  This run produces plain text — there are no named fields to pick from.
  Add braces to the prompt (e.g. "Extract {name, email}") or pass --schema.
```

## See also

- [Structured output](structured-output.md) — how to get named fields
- [`map`](../verbs/map.md) — the verb most often paired with `--output csv`
