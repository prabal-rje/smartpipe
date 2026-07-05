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

## An internal error / "BUG" screen (exit 70)

That's a sempipe bug, not your fault. The screen tells you how to report it; re-run
with `SEMPIPE_DEBUG=1` for a traceback to include.

## See also

- [CLI reference](reference/cli.md) — every flag and exit code
- [Quickstart](quickstart.md) — get set up correctly the first time
