# Pipes & items

sempipe is built for the Unix pipe. Understanding what it treats as "one item" is
the whole mental model — everything else follows.

## What counts as one item?

- **Reading from stdin (a pipe or redirect):** each **line** is one item.

  ```console
  $ cat server.log | sempipe filter "database timeout"
  ```

  Each line of the log is judged on its own.

- **Reading files (`--in` / `--from-files`):** each **file** is one item.

  ```console
  $ sempipe map "Summarize this document" --in 'reports/*.pdf'
  ```

  See [File inputs](../inputs/files.md) for the details, including how documents
  are parsed automatically and what `filter`/`top_k` return in file mode.

## Plain text vs. JSON Lines

sempipe looks at each line and notices whether it's a JSON object:

- A plain line (`disk full on /var`) is just text.
- A line that's a JSON object (`{"host": "web1", "level": "error"}`) is an
  **NDJSON record** — sempipe parses it so verbs can reference its fields with
  `{braces}`.

This is why `filter "{priority} is wrong"` needs JSON Lines input: it reads the
`priority` field out of each record. Plain text has no fields to read.

## NDJSON, in five lines

**NDJSON** (newline-delimited JSON, also called JSON Lines) is just one JSON object
per line:

```
{"vendor": "Acme", "total": 1250}
{"vendor": "Globex", "total": 990}
```

It's the lingua franca of Unix data pipelines because every line is independently
valid — you can `grep`, `head`, `split`, and stream it. sempipe emits NDJSON when
it produces structured output, so its results flow straight into `jq`:

```console
$ cat receipts.txt | sempipe map "Extract {vendor, total}" | jq 'select(.total > 1000)'
```

## Streams just work

The per-item verbs (`map`, `filter`, `embed`) read stdin **incrementally**: each line
is processed as it arrives, and results flow out as they complete. That means
`tail -f app.log | sempipe filter "…"` works with no flag — sempipe never waits for
an end-of-file that isn't coming. Two practical notes:

- Piped stdin has no known total, so the progress line shows a count and rate
  (`⠋ Processing [847] 3.1/s`) instead of a percentage/ETA. `--in` file lists keep
  the full ETA (their total is known).
- `reduce` and `top_k` need the whole set by nature — over a live stream, use
  [`reduce --window`](../verbs/reduce.md) or [`top_k --stream`](../verbs/top-k.md),
  which redefine "the whole set" as a window or a running board. See the
  [live monitoring cookbook](../cookbook/live-monitoring.md).

## stdout is data, stderr is chatter

One rule makes sempipe safe in any pipeline: **only results go to stdout.**
Progress spinners, warnings about skipped items, and diagnostics all go to
**stderr**. So this always sees clean data:

```console
$ cat notes.txt | sempipe map "summarize" > summaries.txt    # only results in the file
```

and you still see the progress and any warnings on your terminal.

## It dies like a filter

Resilience is the name of the game for Unix tools, and that includes *ending* well.
If downstream closes the pipe (`sempipe … | head -1`), sempipe dies instantly and
silently with the conventional code (141) — never an error screen. Ctrl-C drains
in-flight work for the per-item verbs and reports what it saved. One bad item is a
warning; only a majority-failure run halts early.

## Order is preserved

However many items sempipe processes in parallel, **output order always matches
input order.** Line 1's result comes before line 2's, always — so `diff`, `paste`,
and line-numbered logs keep working.

## The one verb that makes new rows

Every verb above transforms, keeps, or combines *existing* items. `join` is the
exception: it emits **pairs** — `{"left": …, "right": …, "_score": …}` — built
from two inputs. The sides stay nested so their field names can never collide.

## See also

- [`filter`](../verbs/filter.md) and [`map`](../verbs/map.md) — the verbs that consume items
- [Structured output](structured-output.md) — reading and writing JSON fields
