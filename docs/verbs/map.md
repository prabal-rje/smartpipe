# `map` - transform each item

> Need your input record's fields to survive alongside the extraction?
> That's [`extend`](extend.md) - map that merges.

Applies a prompt to every input item. One item in, one result out.

## Examples

```bash
# Plain transform - each line becomes a prompt, one result per line:
cat notes.txt \
| smartpipe map "Translate to French"

# Structured extraction - braces name the fields you want back (JSONL out):
cat receipts.txt \
| smartpipe map "Extract {vendor, date, total}"
# → {"vendor": "Acme Corp", "date": "2026-01-15", "total": 1250}

# Use a cloud model just for this run:
cat data.txt \
| smartpipe map "Classify the sentiment" --model gpt-5.4-mini

# Compose with the tools you already have:
cat receipts.txt \
| smartpipe map "Extract {vendor, total}" \
| jq -r '.total' \
| paste -sd+ \
| bc
```

## How it decides plain vs. structured

One rule: **braces mean you want structured output.** Everything outside the
braces is the instruction the model follows; the braces declare the fields to
return.

- No braces → **plain text**, one line out per input TEXT line. When the
  input is records (JSONL), the answer stays a record:
  `{"result": "…", "__source": …}` per row - records in, records out
  ([the item](../concepts/the-item.md)).
- Braces like `{vendor, total}` (or a `--schema` file) → **JSON**, one object per
  input, validated against the fields you asked for.

In `map`, braces describe the *output*. (In `filter` and `reduce`, `{field}` reads
an input field instead - see [structured output](../concepts/structured-output.md)
for the full grammar.)

## Images

`map` is the vision verb: an image item (from `'photos/*.jpg'` or a redirected
image on `stdin`) is sent to the model as an image, and your prompt describes what to
do with it - including structured extraction (`"Extract {brand, color}"`). Needs a
vision-capable model (`ollama/qwen3-vl`, `gpt-5.4-mini`, `claude-opus-4-8`, …);
without one, the item skips with a hint.

## Streaming

`map` processes `stdin` incrementally - results appear as input arrives, so live
sources work with no flag:

```bash
tail -f app.log \
| smartpipe map "Classify: {severity, category}" \
| tee incidents.jsonl
```

## Options

| Option | Meaning |
|---|---|
| `--schema FILE` | Enforce a JSON Schema file on the output (strict extraction - see below) |
| `--model TEXT` | Model for this run (e.g. `ollama/qwen3:8b`, `gpt-5.4-mini`, `claude-opus-4-8`) |
| `--output FORMAT` | `auto` (default) · `text` · `json`. `auto` = human-readable at a terminal, JSONL when piped |
| `--concurrency N` | Max parallel model calls (default 4) |
| `--fields A,B` | Select + order output columns ([details](../concepts/output-formats.md)) |
| `@file` / `--prompt-file FILE` | Read the prompt from a file - for instructions that outgrow the command line |
| `--whole` | Never auto-chunk oversized items: process whole or skip with an error |
| `--verbose` / `--debug` | More detail on stderr / full tracebacks |

## Lists into rows: `--explode`

When a field is a list, `--explode FIELD` emits one row per element with the
sibling fields copied - `jq -c '.risks[]'`, but provenance-aware and schema-checked:

```bash
cat filings.txt \
| smartpipe map "Extract {vendor, risks}" --explode risks
# → {"vendor":"Acme","risks":"late delivery"}
# → {"vendor":"Acme","risks":"currency exposure"}
```

An empty list is zero rows; a non-list value passes through unchanged.
Composes with `--tally` (counted per exploded row) and `--fields`.

## Items bigger than the window

`map` handles an item the model can't hold - loudly, never silently. The
plan is disclosed before the first call:

```
note: report.pdf ~48,200 tokens over budget - 7 chunks + 1 combine call
```

The same prompt runs on each chunk, then one synthesis call combines the
partial answers into the single result you asked for; with braces or
`--schema`, the partial extractions merge into one record against the same
schema (`+ 1 merge call`). Every chunk call shows in the receipt and counts
against `--max-calls`. The estimate is media-aware (images priced from their
header dimensions, audio/video per second), and a chunk the provider still
rejects re-splits in half and retries, disclosed
(`chunk re-split: provider rejected the estimate`).

Prefer call-for-call reproducibility over handling? `--whole` restores the
refusal - the item is processed whole or skipped with the split recipe:

```
⚠ skipped: report.pdf (~87,886 tokens is past gpt-5.4-mini's ~76,300-token budget -
  split it first: smartpipe split FILE | smartpipe map "..." | smartpipe reduce "...")
```

With `--whole`, [split](split.md) makes the chunks visible, `map` transforms
each, `reduce` recombines. The per-verb matrix lives in
[when it doesn't fit](../concepts/feeding-smartpipe.md#when-it-doesnt-fit).

## Audio and images

`map` is the multimodal verb: image items reach vision models as images, audio
items reach audio models as sound (`'calls/*.wav'`). A model that can't
hear falls back to local transcription (built in - `whisper` ships with smartpipe) when it is
present; details in [File inputs](../inputs/files.md#audio-heard-natively-or-transcribed).

## Inline braces vs. `--schema`

- **Inline** `{vendor, total}` is quick and great for exploration - the model
  infers the types.
- **`--schema invoice.json`** points at a standard JSON Schema file for production:
  output strictly conforms, types are coerced, and fields you didn't ask for are
  dropped. See [structured output](../concepts/structured-output.md).

## Gotchas

- **One in, one out works best with per-line prompts.** If a prompt asks for a
  multi-paragraph essay, you get multiple lines out for that item - fine, but know
  it breaks the neat line-for-line mapping.
- **A bad item is a warning, not a crash.** If the model can't produce valid JSON
  for one line (even after a retry), that line is skipped with a `⚠ skipped:`
  note on stderr, and the rest keep going. The exit code is `1` when anything was
  skipped, `0` when all succeeded.
- **`stdout` is only results.** Warnings and the progress spinner go to `stderr`, so
  `| jq` and `> file` see clean data.
- **Empty input is success.** `cat empty | smartpipe map …` prints nothing and exits
  `0`, just like `grep`.

## See also

- [Structured output](../concepts/structured-output.md) - the brace grammar and `--schema`
- [Models & providers](../concepts/models-and-providers.md) - picking and switching models


## Video frame control

By default a video yields one frame per second up to 24, evenly spread past
that (a 10-minute clip becomes 24 frames, one per 25 seconds). Two flags
change the deal on `map`/`extend`:

```bash
smartpipe map "what changes in this scene?" demo.mp4 --frame-every 1
smartpipe map "summarize" long.mp4 --frame-every 2 --max-frames 120
```

- `--frame-every SECONDS` is a **density guarantee** - one frame per period,
  and the default 24-frame cap lifts (so the default 24-frame cap no longer
  applies).
- `--max-frames N` is a **budget** - when both are set, the smaller wins.
- The per-row note prints the frame count, and the run receipt shows the
  image megabytes actually sent, so a 600-frame decision is a visible one.
- The text-verb caption pivot keeps its small fixed sample; these flags
  govern frames sent natively to a vision model. On `gemini` the video is
  watched natively and no frames are extracted at all.
