# `map` — transform each item

Applies a prompt to every input item. One item in, one result out.

## Examples

```console
# Plain transform — each line becomes a prompt, one result per line:
$ cat notes.txt | sempipe map "Translate to French"

# Structured extraction — braces name the fields you want back (NDJSON out):
$ cat receipts.txt | sempipe map "Extract {vendor, date, total}"
{"vendor": "Acme Corp", "date": "2026-01-15", "total": 1250}

# Use a cloud model just for this run:
$ cat data.txt | sempipe map "Classify the sentiment" --model gpt-4o-mini

# Compose with the tools you already have:
$ cat receipts.txt | sempipe map "Extract {vendor, total}" | jq -r '.total' | paste -sd+ | bc
```

## How it decides plain vs. structured

One rule: **braces mean you want structured output.**

- No braces → **plain text**, one line out per input line.
- Braces like `{vendor, total}` (or a `--schema` file) → **JSON**, one object per
  input, validated against the fields you asked for.

In `map`, braces describe the *output*. (In `filter` and `reduce`, `{field}` reads
an input field instead — see [structured output](../concepts/structured-output.md)
for the full grammar.)

## Images

`map` is the vision verb: an image item (from `--in 'photos/*.jpg'` or a redirected
image on stdin) is sent to the model as an image, and your prompt describes what to
do with it — including structured extraction (`"Extract {brand, color}"`). Needs a
vision-capable model (`ollama/qwen3-vl`, `gpt-4o-mini`, `claude-opus-4-8`, …);
without one, the item skips with a hint.

## Streaming

`map` processes stdin incrementally — results appear as input arrives, so live
sources work with no flag:

```console
$ tail -f app.log | sempipe map "Classify: {severity, category}" | tee incidents.jsonl
```

## Options

| Option | Meaning |
|---|---|
| `--schema FILE` | Enforce a JSON Schema file on the output (production-grade extraction — see below) |
| `--model TEXT` | Model for this run (e.g. `ollama/qwen3:8b`, `gpt-4o-mini`, `claude-opus-4-8`) |
| `--output FORMAT` | `auto` (default) · `text` · `json`. `auto` = human-readable at a terminal, NDJSON when piped |
| `--concurrency N` | Max parallel model calls (default 4) |
| `--fields A,B` | Select + order output columns ([details](../concepts/output-formats.md#-fields--pick-and-order-your-columns)) |
| `--verbose` / `--debug` | More detail on stderr / full tracebacks |

## Inline braces vs. `--schema`

- **Inline** `{vendor, total}` is quick and great for exploration — the model
  infers the types.
- **`--schema invoice.json`** points at a standard JSON Schema file for production:
  output strictly conforms, types are coerced, and fields you didn't ask for are
  dropped. See [structured output](../concepts/structured-output.md).

## Gotchas

- **One in, one out works best with per-line prompts.** If a prompt asks for a
  multi-paragraph essay, you get multiple lines out for that item — fine, but know
  it breaks the neat line-for-line mapping.
- **A bad item is a warning, not a crash.** If the model can't produce valid JSON
  for one line (even after a retry), that line is skipped with a `⚠ skipped:`
  note on stderr, and the rest keep going. The exit code is `1` when anything was
  skipped, `0` when all succeeded.
- **stdout is only results.** Warnings and the progress spinner go to stderr, so
  `| jq` and `> file` always see clean data.
- **Empty input is success.** `cat empty | sempipe map …` prints nothing and exits
  `0`, just like `grep`.

## See also

- [Structured output](../concepts/structured-output.md) — the brace grammar and `--schema`
- [Models & providers](../concepts/models-and-providers.md) — picking and switching models
