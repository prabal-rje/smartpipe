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
| `--fields A,B` | Select + order the `{text, vector, source}` record fields ([details](../concepts/output-formats.md)) |

## Performance

Batching is automatic. A file corpus (`--in 'docs/*'`) is embedded in chunks of
up to 64 texts per call — 64× fewer round-trips, and if a chunk fails it is
retried one item at a time so a single bad item skips alone. Piped input stays
one item per call: on a live stream, latency beats throughput.

## Media items (images, audio)

`embed` and `top_k` rank **text**. Audio items transcribe on demand when the
`[audio]` extra is installed; image items skip with a pointer to `map`. True
multimodal embeddings wait on a provider wire that carries them (none of the
wired providers' embedding endpoints do today — this is a recorded gate, not an
oversight). To rank audio by content deliberately: transcribe first with
`map "transcribe this" --in 'calls/*.wav'`, then embed the transcripts.

## Notes

- **Embeddings are transient by design.** sempipe has no vector database — the
  embeddings live in the pipe. Redirect to a file if you want to keep them.
- **The embedding model is separate from the chat model.** Set it with
  `sempipe config embed-model …` or `--embed-model`. Whatever you embed a corpus
  with, use the *same* model when you query it with `top_k`.

## See also

- [`top_k`](top-k.md) — rank embedded items by similarity
- [Models & providers](../concepts/models-and-providers.md) — the separate embedding model
