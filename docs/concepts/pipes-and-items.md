# Pipes & items

smartpipe is built for the Unix pipe. Understanding what it treats as "one item" is
the whole mental model.

## What counts as one item?

- **Reading from `stdin` (a pipe or redirect):** each **line** is one item.

  ```bash
  cat server.log | smartpipe filter "database timeout"
  ```

  Each line of the log is judged on its own.

- **Reading files (named FILES / `--from-files`):** each **file** is one item
  by default; [the item](the-item.md) explains the granularity dial (`--as`).

  ```bash
  smartpipe map "Summarize this document" 'reports/*.pdf'
  ```

  See [File inputs](../inputs/files.md) for the details, including how documents
  are parsed automatically and what `filter`/`top_k` return in file mode.

## Plain text vs. JSON Lines

smartpipe looks at each line and notices whether it's a JSON object:

- A plain line (`disk full on /var`) is just text.
- A line that's a JSON object (`{"host": "web1", "level": "error"}`) is a
  **JSONL record** - smartpipe parses it so verbs can reference its fields with
  `{braces}`.

This is why `filter "{priority} is wrong"` needs JSON Lines input: it reads the
`priority` field out of each record. Plain text has no fields to read.

## JSONL, in five lines

**JSONL** (JSON Lines, also called newline-delimited JSON or NDJSON) is just one
JSON object per line:

```
{"vendor": "Acme", "total": 1250}
{"vendor": "Globex", "total": 990}
```

It's the common exchange format for Unix data pipelines because every line is independently
valid - you can `grep`, `head`, `split`, and stream it. smartpipe emits JSONL when
it produces structured output, so its results flow straight into `jq`:

```bash
cat receipts.txt \
| smartpipe map "Extract {vendor, total number}" \
| jq 'select(.total > 1000)'
```

## Streams are read incrementally

The per-item verbs (`map`, `filter`, `embed`) read stdin **incrementally**: each line
is processed as it arrives, and results flow out as they complete. That means
`tail -f app.log | smartpipe filter "…"` works with no flag - smartpipe never waits for
an end-of-file that isn't coming. Two practical notes:

- Piped stdin has no known total, so the progress line shows a count and rate
  (`⠋ Processing [847] 3.1/s`) instead of a bar. Named-file lists know their
  total, so they get the full progress bar with percentage and time left
  (`[██████░░░░░░░░░] 41% · 205/500 · 12/s · ~25s left`).
- `reduce` and `top_k` need the whole set by nature - over a live stream, use
  [`reduce --window`](../verbs/reduce.md) or [`top_k --stream`](../verbs/top-k.md),
  which redefine "the whole set" as a window or a running board. See the
  [live monitoring cookbook](../cookbook/live-monitoring.md).

## `stdout` is data, `stderr` is diagnostics

One rule makes smartpipe safe in any pipeline: **only results go to `stdout`.**
Progress spinners, warnings about skipped items, and diagnostics all go to
`stderr`. So this always sees clean data:

```bash
cat notes.txt \
| smartpipe map "summarize" > summaries.txt    # only results in the file
```

and you still see the progress and any warnings on your terminal.

## It dies like a filter

A good Unix tool ends cleanly, not just runs cleanly - and that includes *ending* well.
If downstream closes the pipe (`smartpipe … | head -1`), smartpipe dies instantly and
silently with the conventional code (141) - never an error screen. Ctrl-C drains
in-flight work for the per-item verbs and reports what it saved. One bad item is a
warning; only a majority-failure run halts early.

## Order is preserved

However many items smartpipe processes in parallel, **output order always matches
input order.** Line 1's result comes before line 2's, always - so `diff`, `paste`,
and line-numbered logs keep working.

## The one verb that makes new rows

Every verb above transforms, keeps, or combines *existing* items. `join` is the
exception: it emits **pairs** - `{"left": …, "right": …, "__score": …}` - built
from two inputs. The sides stay nested so their field names can never collide.

## See also

- [`filter`](../verbs/filter.md) and [`map`](../verbs/map.md) - the verbs that consume items
- [Structured output](structured-output.md) - reading and writing JSON fields


## The Unix toolbox, in five lines

smartpipe composes with tools you may not have met yet. The five you'll see
in these docs, each in one sentence:

- **`jq`** reads JSON: `jq -r .total` prints the `total` field of each line.
- **`grep`** keeps lines matching a pattern (smartpipe's `where` is its
  field-aware cousin; `filter` is its semantic one).
- **`sort` / `uniq`** order lines and drop exact repeats (`distinct` is the
  by-meaning version).
- **`head -5`** keeps the first five lines - handy after `sort --by`.
- **`wc -l`** counts lines, which after a filter means counting matches.

Every one of them connects with the pipe `|`: "send this command's output
into that command's input." That's the whole trick, and smartpipe is just
more verbs for the same sentence.
