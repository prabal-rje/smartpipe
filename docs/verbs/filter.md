# `filter` - keep or discard each item

Applies a semantic yes/no judgment to each item and keeps the ones that match.
Semantic grep: the output is a strict, byte-for-byte subset of the input, in order.

## Examples

```bash
# Semantic grep:
cat reviews.txt \
| smartpipe filter "the reviewer is sarcastic"

# Reference input fields with {braces} (JSON Lines input):
cat tickets.jsonl \
| smartpipe filter "{priority} is wrong given {description}"

# Invert, like grep -v:
cat emails.txt \
| smartpipe filter --not "this is spam" > ham.txt

# Chain with the tools you already use:
cat server.log \
| grep "POST /api" \
| smartpipe filter "the response indicates a bug" \
| wc -l
```

## How it works

`filter` asks the model a yes/no question about each item and keeps the "yes"
items. Two forms:

- **Plain condition** (`"reviewer is sarcastic"`) - the whole item is judged
  against the condition.
- **Field references** (`"{priority} is wrong given {description}"`) - each
  `{field}` is replaced with that item's value, so the condition can talk about
  specific parts of a JSON record. This needs **JSON Lines** input (one JSON object
  per line).

> In `filter`, `{field}` reads an *input* field. (In `map`, braces describe the
> *output*.) One field per brace group - comma-groups like `{a, b}` are a `map`-only
> shorthand and `filter` rejects them with a clear message.

## Streaming

`filter` streams by nature - pipe a live source in and matches flow out as lines
arrive, with a running `· N matched` tally on the `stderr` status line:

```bash
tail -f app.log \
| smartpipe filter "a user is hitting a real error"
```

Compose with `head` to wait for the first occurrence (smartpipe exits cleanly when
`head` closes the pipe):

```bash
tail -f app.log \
| smartpipe filter "signals an outage" \
| head -1 && page-oncall
```

## Options

| Option | Meaning |
|---|---|
| `--not` | Keep items that do NOT match (like `grep -v`) |
| `--model TEXT` | Model for this run |
| `--concurrency N` | Max parallel model calls (default 4) |

## Gotchas

- **Zero matches is success.** Unlike `grep`, `filter` exits `0` when nothing
  matches - an empty result is a valid result. (An item that *errors* during
  judging is a different thing: it's skipped with a warning, and the exit code is
  `1` if anything was skipped.)
- **Output is byte-identical to the input it kept.** `filter` never rewrites your
  data - a kept line comes out exactly as it went in, so `filter | diff` and
  `filter | jq` see the original bytes.
- **`{field}` on non-JSON input fails fast.** If your prompt references a field but
  the input isn't JSON, `filter` stops immediately with a clear message rather than
  warning on every line.

## See also

- [Structured output](../concepts/structured-output.md) - the `{field}` grammar in full
- [Pipes & items](../concepts/pipes-and-items.md) - what counts as "one item"
- [`map`](map.md) - transform items instead of filtering them

## Items bigger than the window

An oversized item is judged in chunks: if **any** chunk matches, the whole item
is kept (byte-verbatim, as always); `--not` inverts at the end. You pay one
judge call per chunk until the first match.
