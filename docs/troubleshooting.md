# Troubleshooting

Symptom-indexed. Find your error message; each entry says what it means and the fix.
Every one of these is also a friendly screen sempipe prints on stderr — you shouldn't
have to come here, but here's the reference.

## "no model configured" (exit 2)

sempipe has nothing to talk to. Either:

- **Run a local model (free):** install [Ollama](https://ollama.com), then
  `ollama pull qwen3:8b`. sempipe finds it automatically.
- **Use a cloud model:** `export OPENAI_API_KEY=…` (or `ANTHROPIC_API_KEY`) and pass
  `--model gpt-4o-mini` (or set a default with `sempipe config model …`).

See the [quickstart](quickstart.md) for the one-minute version.

## "can't reach Ollama at localhost:11434" (exit 2)

Ollama isn't running. Start it (`ollama serve`, or just open the app), or point
sempipe elsewhere with `OLLAMA_HOST`. Confirm it's up with `ollama list`.

## "model 'X' isn't available" (exit 2)

The model name isn't pulled locally. `ollama pull X`, or pick one you have with
`ollama list` / `sempipe config model …`.

## "ANTHROPIC_API_KEY isn't set" / "OPENAI_API_KEY isn't set" (exit 2)

A cloud model needs a key, and sempipe reads it from the environment (never a file):

```console
$ export ANTHROPIC_API_KEY=sk-ant-…
```

## "Claude models need an extra" (exit 2)

Install it: `pip install 'sempipe[anthropic]'`.

## "parsing documents needs an optional dependency"

You pointed `--in` at PDFs/DOCX without the parser installed:
`pip install 'sempipe[files]'` (or `sempipe[audio]` for audio, `sempipe[all]` for
everything). Files that need it are skipped until you do.

## "reading from a terminal — pipe some input in" (exit 64)

You ran a verb with nothing to read. Pipe something in, redirect a file, or use
`--in`:

```console
$ cat notes.txt | sempipe map "summarize"
$ sempipe map "summarize" --in 'notes/*.txt'
```

## "no files matched: …" (exit 64)

Your `--in` glob matched nothing. Check the pattern, and **quote it** so your shell
doesn't expand it first: `--in '*.pdf'`.

## "--output csv needs structured output" (exit 64)

CSV/TSV need named columns. Ask for fields with braces or a schema:

```console
$ … | sempipe map "Extract {name, email}" --output csv
```

## "⚠ skipped: line N (…)" — but the run continued

That's by design. A single item that fails (a malformed record, a model refusal, a
file that won't parse) is skipped with a warning; the rest of the batch runs. The
exit code is `1` (partial) so a script can notice. If *every* item failed, you get
exit `3`.

## The output looks different when I pipe it

Also by design. At a terminal, structured results show a readable view; piped, they
become NDJSON. Force one with `--output json` (or `text`, `csv`, `tsv`). See
[output formats](concepts/output-formats.md).

## Why is there no ETA / percentage when I pipe input in?

Piped stdin is a stream — sempipe processes lines as they arrive and can't know how
many are coming, so the progress line shows a count and rate instead. `--in` file
mode knows its total and keeps the ETA.

## My `tail -f` pipeline never ends

That's `tail -f` — it follows forever. Your sempipe results stream out as lines
arrive. End the pipeline with `| head -N` (sempipe exits cleanly when downstream
closes) or Ctrl-C (drains and summarizes).

## I piped into `head` and sempipe "died" (exit 141)

That's correct behavior, not a crash. When downstream closes the pipe (`head` got what
it needed), sempipe dies instantly and silently — exactly like `grep` or `cat` — with
exit code 141 (SIGPIPE). Scripts using `set -o pipefail` can treat 141 from the left
side of a `| head` as expected.

## What does Ctrl-C do mid-run?

For `map`/`filter`/`embed`: the first Ctrl-C stops new work, finishes what's in flight
(≤10 s), writes those results, prints a `done: interrupted — …` summary to stderr, and
exits with the normal outcome code. Press Ctrl-C twice to bail immediately (exit 130).
`reduce`/`top_k` exit immediately — they have no partial result to save.

## An internal error / "BUG" screen (exit 70)

That's a sempipe bug, not your fault. The screen tells you how to report it; re-run
with `SEMPIPE_DEBUG=1` for a traceback to include.

## See also

- [CLI reference](reference/cli.md) — every flag and exit code
- [Quickstart](quickstart.md) — get set up correctly the first time
