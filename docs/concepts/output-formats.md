# Output formats

smartpipe adapts its output to where it's going, and lets you override that with
`--output`. The goal: readable at your terminal, machine-clean in a pipe.

## The default: `auto`

With no `--output` flag, smartpipe looks at whether `stdout` is a terminal:

| Result kind | At a terminal | Piped / redirected |
|---|---|---|
| Plain text | the text | the text |
| Structured (JSON) | a readable key/value view | JSONL (one object per line) |

So `smartpipe map "Extract {name, role}"` shows you a tidy view on screen, but the
moment you pipe it into `jq` or a file, it becomes clean JSONL - no flags needed.

### Media previews at the terminal

In the terminal view (and in [`readable`](../verbs/readable.md)), an item
carrying media gets more than its `image/png (48 KB)` summary line - the first
media part renders right under it:

- **Images** show a small color thumbnail (aspect preserved, about 40x12
  cells).
- **Video** shows a 3-frame strip sampled at 10%/50%/90% of the duration -
  never the first frame, which is usually a black or logo intro.
- **Audio** shows a waveform envelope (long files decode at most the first
  10 minutes).
- **Audio and video with a real file behind them** add a
  `▶ play (0:42, 2.1 MB)` line - an OSC 8 hyperlink to the `file://` URL, so
  a click opens your system player. Media that exists only as pipe bytes
  still gets its picture, just no link.

Previews exist only where the human view does: pipes, `--output json`,
`NO_COLOR`, and `TERM=dumb` output stay byte-for-byte what they were. The
persisted kill switch:

```bash
smartpipe config media-previews off   # back to summary lines only
```

## Explicit formats

| `--output` | What you get |
|---|---|
| `text` | plain text, one line per item |
| `json` | JSONL, even at a terminal |
| `csv` | comma-separated, with a header row |
| `tsv` | tab-separated, with a header row |

You can also set a default with the `SMARTPIPE_OUTPUT` environment variable.

## CSV & TSV - for spreadsheet people

Point structured output straight into a spreadsheet:

```bash
cat cards.txt \
| smartpipe map "Extract {name, email, role}" --output csv > people.csv
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
  dropped, with a one-time warning on `stderr` - so the shape stays stable.
- **Nested values** (objects, arrays) become compact JSON inside one cell.
- **CSV is RFC 4180** (proper quoting, CRLF line endings); **TSV** uses tabs, and
  replaces any tab or newline inside a value with a space (a tab inside a TSV cell
  would corrupt the columns).
- **`top_k` scores land last.** A `__score` column sorts to the right of your data,
  where a spreadsheet reader expects it.

> **Migrating from pre-1.4:** `top_k` and `outliers` used to write `_score`
> (and `_rank`/`_snapshot`/`_distance`) with a single underscore. Those stamps
> now live in the reserved `__` namespace - update your `jq`/`sort --by`
> references to `__score` etc. The old spellings are still read for one
> release (they keep sorting last in CSV/TSV), but are no longer written.

CSV and TSV need **structured** output - there have to be named columns. Ask for
fields with braces or `--schema`; a plain-text prompt with `--output csv` is a
usage error that tells you so.

```bash
echo hi \
| smartpipe map "shout" --output csv
# → error: --output csv needs structured output - a table needs named columns
# →   add braces to the prompt (e.g. "Extract {name, email}") or pass --schema
```

## `--fields` - pick and order your columns

`--fields a,b` projects structured output down to just those fields, in exactly
that order. It works the same in every format - JSONL, the terminal view, CSV,
and TSV - on `map`, `embed`, `top_k`, and `reduce`:

```bash
cat cards.txt \
| smartpipe map "Extract {name, email, role}" --fields name,email --output tsv
# → name	email
# → Ada Lovelace	ada@example.com
# → Grace Hopper	grace@example.com
```

The projection rules:

- **Order is yours.** Columns appear in the order you list them, not the order the
  model produced.
- **The shape never wobbles.** A field a result doesn't carry stays in place -
  `null` in JSONL, an empty cell in CSV/TSV, a blank value at the terminal - and
  you get a one-time warning on `stderr` naming it.
- **Dropping the rest is the point.** Fields you didn't ask for are omitted
  silently.
- **Structured output only.** On a plain-text run there are no named fields to
  pick from, so `--fields` is a usage error that says exactly that. (`filter`
  never has the flag - its output is a byte-faithful subset of its input.)

```bash
cat notes.txt \
| smartpipe map "shout" --fields name
# → error: --fields selects columns from structured output
# →   This run produces plain text - there are no named fields to pick from.
# →   Add braces to the prompt (e.g. "Extract {name, email}") or pass --schema.
```

## See also

- [Structured output](structured-output.md) - how to get named fields
- [`map`](../verbs/map.md) - the verb most often paired with `--output csv`
