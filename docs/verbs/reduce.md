# `reduce` — synthesize many items into one

Combines all your input into a single result. Like an `awk` END block that can
actually read — a summary, a synthesis, a report drawn from everything at once.

## Examples

```console
# Summarize a pile of notes:
$ cat meeting-notes/*.md | sempipe reduce "Write a one-page executive summary"

# Structured synthesis:
$ cat incidents.jsonl | sempipe reduce "Write a root-cause analysis" --schema rca.json

# One summary per group:
$ cat feedback.jsonl | sempipe reduce "Summarize the sentiment" --group-by product

# See how it chunks a large input:
$ cat book.txt | sempipe reduce "List the main themes" --verbose
```

## The headline feature: it just handles large inputs

Most "summarize with an LLM" tools break the moment your input exceeds the model's
context window. `reduce` doesn't. When the input is too big for one call, it:

1. splits the items into chunks that fit,
2. summarizes each chunk into dense notes,
3. and repeats on the notes — until everything fits in a final synthesis.

There are **no flags to configure this** and no strategy to choose. It's automatic.
Add `--verbose` to watch the tree on stderr:

```
reduce: 50,000 items → 41 chunks → 3 → 1
```

That line reads left to right: 50,000 items became 41 chunk-summaries, those became
3, and those became the 1 final result.

## `--group-by`: one result per group

With JSON Lines input, `--group-by FIELD` runs a separate reduction for each
distinct value of `FIELD`, emitting one record per group:

```console
$ cat reviews.jsonl | sempipe reduce "Summarize complaints" --group-by product
{"group": "Widget", "result": "Users report..."}
{"group": "Gadget", "result": "The main issue..."}
```

Inside the prompt, `{field}` refers to the group's value — so
`reduce "Summarize sentiment for {product}" --group-by product` names each product
in its own prompt. (Outside `--group-by`, a `{field}` reference is an error, because
there's no single item to read it from.)

## `--schema`: shape the final result

Point `--schema` at a JSON Schema to get a validated object instead of prose — the
same enforcement and one-shot repair as [`map`](map.md):

```console
$ cat reports.jsonl | sempipe reduce "Synthesize findings" --schema summary.json
```

## Streaming: `--window`

A stream never ends, so give `reduce` a boundary: `--window N` synthesizes every N
lines and emits each result immediately; `--every M` makes the windows slide (a
fresh take on the last N lines after every M new ones):

```console
$ tail -f server.log | sempipe reduce --window 100 --every 20 "current error trend?"
{"window_end": 100, "result": "…"}
```

Each window's record carries `window_end` (the stream position); the final,
incomplete window is flushed on Ctrl-C or EOF with `"partial": true` — buffered
lines are never silently discarded. `--window` reads stdin only (not `--in`) and
doesn't combine with `--group-by`.

## Options

| Option | Meaning |
|---|---|
| `--group-by FIELD` | Reduce once per distinct value of a JSON field |
| `--window N` | Stream mode: one reduce per N lines (tumbling) |
| `--every M` | With `--window`: slide, reducing after every M lines |
| `--schema FILE` | Validate the final result against a JSON Schema |
| `--verbose` | Print the chunking tree on stderr |
| `--model TEXT` | Model for this run |
| `--concurrency N` | Max parallel model calls |
| `--fields A,B` | Select + order output columns ([details](../concepts/output-formats.md#-fields--pick-and-order-your-columns)) |

## Gotchas

- **A chunk that fails is skipped, not fatal.** If one chunk can't be summarized,
  `reduce` warns (naming the item range), drops it, and continues with the rest —
  the exit code is `1` to signal the partial result. Only if *every* chunk fails do
  you get an empty result and exit `3`.
- **Token estimates are deliberately conservative.** sempipe errs toward smaller
  chunks, which means an extra level of summarization now and then — never a
  truncated call that silently loses your data.

## See also

- [Pipes & items](../concepts/pipes-and-items.md) — what counts as one item
- [`map`](map.md) — transform items instead of combining them
- [Structured output](../concepts/structured-output.md) — the `--schema` details
