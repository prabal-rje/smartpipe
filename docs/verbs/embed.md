# `embed` - convert items to vectors

Turns each item into a vector embedding. This is a utility verb - it exists to feed
[`top_k`](top-k.md), and it's the only verb that produces vectors instead of text.

## Examples

```bash
# Embed a corpus and save it for reuse:
cat docs/*.md \
| smartpipe embed > corpus.embeddings

# Embed a single query (useful in scripts):
echo "senior Python backend engineer" \
| smartpipe embed \
| jq '.vector | length'
# → 768
```

## Output

One JSONL object per item, always (a vector has no human-readable view):

```json
{"text": "the item text", "vector": [0.12, -0.03, ...], "__embedder": "ollama/nomic-embed-text", "__source": {"path": "-", "as": "lines", "line": 1}}
```

- `text` - the item's meaningful content (a record embeds its content, never
  its serialized wrapper).
- `vector` - the embedding, an array of floats.
- `__embedder` - the resolved model that produced the vector. `top_k` checks
  this stamp against its own resolved model and refuses a mismatched corpus -
  vectors from two models live in different spaces.
- `__source` - the provenance spine every verb carries
  ([the item](../concepts/the-item.md)).

Rows written by older releases (`"source": "-"` and no stamp) still feed
`top_k` for one release - unstamped rows get one calm note instead of a
refusal.

Because it's JSONL, you redirect it to a file and feed that file to `top_k` later -
which skips re-embedding items that already carry a `vector`.

## Options

| Option | Meaning |
|---|---|
| `--embed-model TEXT` | The embedding model (default `local/nomic-embed-text-v1.5` when `fastembed` is available; configured separately from the chat model) |
| `--media-embed-model TEXT` | A JOINT text+image embedder for media items (e.g. `jina/jina-clip-v2`); text items keep `--embed-model` ([the role](../concepts/models-and-providers.md#the-media-embed-model-role)) |
| `--ocr-model TEXT` | Parse ingested PDFs/images with a document parsing model ([the role](../concepts/models-and-providers.md#the-ocr-model-role)) |
| `--concurrency N` | Max parallel model calls (default 4) |
| `--fields A,B` | Select + order the output record fields ([details](../concepts/output-formats.md)) |

## Performance

Batching is automatic. A file corpus (`'docs/*'`) is embedded in chunks of
up to 64 texts per call - 64× fewer round-trips, and if a chunk fails it is
retried one item at a time so a single bad item skips alone. Piped input stays
one item per call: on a live stream, latency beats throughput.

## Media items: one text space, everything converts in

`embed` and `top_k` rank **text** - and every other modality converts into that
space through a ladder, per item, disclosed per row:

- **audio** → a chat model that hears ("transcribe verbatim; if it isn't
  speech, describe the sound" - this covers non-speech audio) → the configured
  transcription ladder → skip. Local conversion is automatic; cloud conversion
  requires `--allow-captions` or an equivalent cloud profile consent.

- **images** → a vision chat model describes them (including visible text) -
  same fence: local free and automatic, cloud behind `--allow-captions`; no
  free non-LLM rung exists, so without one of them the item is skipped;
  supplying either flag fixes it.

- **video** → first the WHOLE video to a model that watches (`gemini` native:
  the description covers the visuals too); otherwise the audio track through
  the audio row (frames dropped, said so).

Swapping embedding models changes none of this: the converter runs before
embedding and belongs to the *chat* model's capabilities, so the embedder only
ever sees words. The `local` profile anchors the space with `embeddinggemma`
(multilingual, 2k context, ~20 ms/item).

Two exceptions skip the ladder entirely:

- a media-capable `--embed-model` (e.g. `jina/jina-clip-v2`) embeds
  image-only items as pixels, natively;
- a configured `--media-embed-model` routes image-only items to that joint
  space while text keeps `--embed-model`. Mixing text and media in one run
  with two DIFFERENT models is refused loudly - one run, one vector space.

## Items bigger than the embedding window

An oversized text is embedded in chunks and the vectors are **mean-pooled**
into one whole-document vector (the standard practice). `top_k` inherits this.
The budget is conservative per provider (Gemini's embedding model caps input
much lower than the others).

## Notes

- **Embeddings are transient by design.** smartpipe doesn't persist embeddings;
  they stream through the pipe. Redirect to a file to keep them.
- **The embedding model is separate from the chat model.** Set it with
  `smartpipe config embed-model …` or `--embed-model`. Whatever you embed a corpus
  with, use the *same* model when you query it with `top_k`.

## See also

- [`top_k`](top-k.md) - rank embedded items by similarity
- [Models & providers](../concepts/models-and-providers.md) - the separate embedding model
