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

## Options

| Option | Meaning |
|---|---|
| `--group-by FIELD` | Reduce once per distinct value of a JSON field |
| `--schema FILE` | Validate the final result against a JSON Schema |
| `--verbose` | Print the chunking tree on stderr |
| `--model TEXT` | Model for this run |
| `--concurrency N` | Max parallel model calls |

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
