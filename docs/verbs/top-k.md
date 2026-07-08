# `top_k` - rank by similarity

Sorts items by semantic closeness to a query and returns the best matches. Like
`sort | head`, but by meaning instead of by string.

## Examples

```console
# Find the 5 most relevant résumés:
$ cat resumes/*.txt \
    | smartpipe top_k 5 --near "distributed systems engineer"

# Rank a precomputed corpus (embed once, query many times):
$ cat corpus.embeddings \
    | smartpipe top_k 10 --near "Q3 revenue strategy"

# Threshold mode: everything above a similarity of 0.8, no fixed count:
$ cat articles.jsonl \
    | smartpipe top_k --near "climate policy" --threshold 0.8

# The three-stage pipeline: embed, rank, extract:
$ cat legal/*.txt \
    | smartpipe embed \
    | smartpipe top_k 20 --near "indemnification" \
    | smartpipe map "Extract {clause_text, liability_cap}"
```

## How it works

`top_k` embeds your query and every item, then ranks the items by cosine
similarity. Give it a number (K), a `--threshold`, or both:

- `top_k 5 --near Q` - the 5 closest items.
- `top_k --near Q --threshold 0.8` - everything scoring ≥ 0.8.
- `top_k 5 --near Q --threshold 0.8` - up to 5 items that also score ≥ 0.8.

Each result gains a **`_score`** from 0 to 1 (higher is closer): a JSON field for
JSON items, or a trailing tab-separated column for plain text.

## Reusing embeddings

If an item already carries a `vector` (because it came from `smartpipe embed`),
`top_k` uses it directly instead of re-embedding - so you can embed a large corpus
once and run many queries against it cheaply:

```console
$ cat docs/*.md \
    | smartpipe embed > corpus.embeddings
$ cat corpus.embeddings \
    | smartpipe top_k 5 --near "first question"
$ cat corpus.embeddings \
    | smartpipe top_k 5 --near "second question"
```

The `vector` field is an internal field, so `top_k` drops it from the output and keeps the
rest of the record plus `_score`.

## Streaming: `--stream` (the live leaderboard)

Keep a running top-K over a live stream:

```console
$ tail -f tickets.jsonl \
    | smartpipe top_k 5 --stream --near "billing dispute"
```

At a terminal the K-line board repaints in place as better matches arrive. In a
pipe, every membership/order change emits a JSONL **snapshot**: a
`{"_snapshot": N}` marker line, then the K records in rank order, each with
`_score` and `_rank` - split on the markers to consume programmatically; no
change means no output. `--stream` needs `K`, reads `stdin` only, and skips (rather
than fails on) a record whose embedding dimensions don't match the query.

## Options

| Option | Meaning |
|---|---|
| `K` (positional) | Return at most this many items |
| `--near TEXT` | The query to rank against (required) |
| `--threshold FLOAT` | Keep everything at or above this similarity (0–1) |
| `--stream` | Live leaderboard over a stream (needs `K`) |
| `--embed-model TEXT` | The embedding model |
| `--concurrency N` | Max parallel model calls |
| `--fields A,B` | Select + order columns of JSON results (incl. `_score`) ([details](../concepts/output-formats.md)) |

*(Spellings `top-k` and `topk` also work.)*

## Performance

Items that need embedding are sent in chunks of up to 64 texts per call
(precomputed `vector` fields from `smartpipe embed` records are reused, never
re-embedded). A failed chunk is retried item by item, so one bad item skips
alone. `--stream` stays one item per call - streaming processes one item at a time to keep latency low.


## Gotchas

- **Use one embedding model for a corpus and its queries.** If the corpus was
  embedded with a different model than the query, the vector dimensions won't
  match and `top_k` stops with an error explaining the dimension mismatch. (Same-dimension but different
  models can't be detected - keep them consistent.)
- **`top_k` buffers everything.** Unlike `map`/`filter`, it must see every item to
  rank, so it isn't a streaming operation.

## See also

- [`embed`](embed.md) - produce the vectors `top_k` ranks
- [Structured output](../concepts/structured-output.md) - the `_score` field in JSON mode
