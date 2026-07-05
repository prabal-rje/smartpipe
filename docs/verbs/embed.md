# `embed` — convert items to vectors

Turns each item into a vector embedding. This is a utility verb — it exists to feed
[`top_k`](top-k.md), and it's the only verb that never touches a chat model.

## Examples

```console
# Embed a corpus and save it for reuse:
$ cat docs/*.md | sempipe embed > corpus.embeddings

# Embed a single query (useful in scripts):
$ echo "senior Python backend engineer" | sempipe embed | jq '.vector | length'
768
```

## Output

One NDJSON object per item, always (a vector has no human-readable view):

```json
{"text": "the item text", "vector": [0.12, -0.03, ...], "source": "-"}
```

- `text` — the item's content.
- `vector` — the embedding, an array of floats.
- `source` — where it came from (`-` for stdin).

Because it's NDJSON, you redirect it to a file and feed that file to `top_k` later —
which skips re-embedding items that already carry a `vector`.

## Options

| Option | Meaning |
|---|---|
| `--embed-model TEXT` | The embedding model (default `nomic-embed-text`, configured separately from the chat model) |
| `--concurrency N` | Max parallel model calls (default 4) |
| `--fields A,B` | Select + order the `{text, vector, source}` record fields ([details](../concepts/output-formats.md#-fields--pick-and-order-your-columns)) |

## Notes

- **Embeddings are transient by design.** sempipe has no vector database — the
  embeddings live in the pipe. Redirect to a file if you want to keep them.
- **The embedding model is separate from the chat model.** Set it with
  `sempipe config embed-model …` or `--embed-model`. Whatever you embed a corpus
  with, use the *same* model when you query it with `top_k`.

## See also

- [`top_k`](top-k.md) — rank embedded items by similarity
- [Models & providers](../concepts/models-and-providers.md) — the separate embedding model
