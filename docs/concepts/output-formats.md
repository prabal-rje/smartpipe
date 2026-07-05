# Output formats

sempipe adapts its output to where it's going, and lets you override that with
`--output`. The goal: readable at your terminal, machine-clean in a pipe.

## The default: `auto`

With no `--output` flag, sempipe looks at whether stdout is a terminal:

| Result kind | At a terminal | Piped / redirected |
|---|---|---|
| Plain text | the text | the text |
| Structured (JSON) | a readable key/value view | NDJSON (one object per line) |

So `sempipe map "Extract {name, role}"` shows you a tidy view on screen, but the
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
$ cat cards.txt | sempipe map "Extract {name, email, role}" --output csv > people.csv
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
$ echo hi | sempipe map "shout" --output csv
error: --output csv needs structured output — a table needs named columns
  add braces to the prompt (e.g. "Extract {name, email}") or pass --schema
```

## See also

- [Structured output](structured-output.md) — how to get named fields
- [`map`](../verbs/map.md) — the verb most often paired with `--output csv`
