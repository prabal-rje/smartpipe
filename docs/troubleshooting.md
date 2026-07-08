# Troubleshooting

> **First move: `smartpipe doctor`.** It checks config, Ollama, models, keys,
> login, extras, and completions in one screen - with the fix on each line -
> and never spends a model call. `doctor --probe` adds the modality matrix:
> which of text/image/audio/video actually reach your configured models, via
> four tiny paid calls, announced first.

Symptom-indexed. Find your error message; each entry says what it means and the fix.
Each of these also prints as a screen on `stderr` when the error occurs; this page collects them for reference.

## "no model configured" (exit 2)

smartpipe has nothing to talk to. Either:

- **Run a local model (free):** install [Ollama](https://ollama.com), then
  `ollama pull qwen3:8b`. smartpipe finds it automatically.
- **Use a cloud model:** `export OPENAI_API_KEY=…` (or `ANTHROPIC_API_KEY`) and pass
  `--model gpt-5.4-mini` (or set a default with `smartpipe config model …`).

See the [quickstart](quickstart.md) for the one-minute version.

## "can't reach Ollama at localhost:11434" (exit 2)

Ollama isn't running. Start it (`ollama serve`, or just open the app), or point
smartpipe elsewhere with `OLLAMA_HOST`. Confirm it's up with `ollama list`.

## "model 'X' isn't available" (exit 2)

The model name isn't pulled locally. `ollama pull X`, or pick one you have with
`ollama list` / `smartpipe config model …`.

## "needs an OpenAI API key or a ChatGPT login" (exit 2)

Two ways forward, pick one:

```bash
export OPENAI_API_KEY=sk-…      # platform billing
smartpipe auth login              # or use your ChatGPT Plus/Pro plan
```

## "the ChatGPT login has expired" (exit 2)

The refresh token was revoked (password change, session cleanup). Run
`smartpipe auth login` again.

## "ANTHROPIC_API_KEY isn't set" (exit 2)

A cloud model needs a key, and smartpipe reads it from the environment (never a file):

```bash
export ANTHROPIC_API_KEY=sk-ant-…
```

## "Claude models need an extra" (exit 2)

The SDK ships with smartpipe; seeing this means a broken environment - reinstall smartpipe.

## "parsing documents needs an optional dependency"

You pointed smartpipe at PDFs/DOCX in an environment missing the parser.
Document parsing ships in the box, so this means a broken install - reinstall
smartpipe. Files that need the parser are skipped until you do.

## "reading from a terminal - pipe some input in" (exit 64)

You ran a verb with nothing to read. Pipe something in, redirect a file, or
name the files:

```bash
cat notes.txt \
| smartpipe map "summarize"
smartpipe map "summarize" 'notes/*.txt'
```

## "no files matched: …" (exit 64)

Your file glob matched nothing. Check the pattern, and **quote it** so your shell
doesn't expand it first: `'*.pdf'`.

## "--output csv needs structured output" (exit 64)

CSV/TSV need named columns. Ask for fields with braces or a schema:

```bash
… \
| smartpipe map "Extract {name, email}" --output csv
```

## "the endpoint doesn't know the model 'X'" (exit 2)

The cloud endpoint answered 404 for that model name - every item would fail the
same way, so smartpipe stopped at the **first** occurrence instead of burning
through your input. Model names drift (e.g. `gemini-2.0-flash-lite` retired in
favor of `gemini-2.5-flash-lite`); check the provider's current list, or switch:
`smartpipe config model <name>`.

## "the endpoint rejected the --schema" (exit 2)

A 400 mentioning `response_format`/`json_schema` means the provider's strict
mode won't accept your schema shape (a common one: every property needs a
`type`). This too stops the run at first sight - nothing else would have
succeeded. Simplify the schema, build one with
[`--schema-from` or `smartpipe schema`](concepts/structured-output.md), or drop
`--schema` and validate downstream.

## "stopping - the call budget (N) is spent" (exit 1 or 2)

You set `--max-calls N` and the run reached it. Per-item verbs finish what's
in flight and report partial results (exit 1); whole-set verbs (`top_k`,
`reduce`) stop up front (exit 2) because a partial answer would be silently
wrong. Raise the ceiling or narrow the input.

## "this model can't hear audio - …" (exit 3 on the skip path)

You sent audio items to a model with no audio input. Two fixes, straight from
the message: use a model that hears (`voxtral-*`, `gemini` models) - otherwise
smartpipe transcribes locally, so text verbs (and `map`, as a fallback)
transcribe it with Whisper (`SMARTPIPE_WHISPER_MODEL` picks the size; tiny is
the default). Details:
[File inputs → audio](inputs/files.md#audio-heard-natively-or-transcribed).

## "~N tokens is past MODEL's ~W-token budget" (per-item skip)

The item is bigger than the model's context window. The message carries the
fix: `smartpipe split FILE | smartpipe map "..." | smartpipe reduce "..."` -
[split](verbs/split.md) chunks it for free, map transforms the chunks, reduce
recombines. If you know your deployment's window is actually bigger, assert it:
`SMARTPIPE_CONTEXT_TOKENS=200000`.

## "prompt file not found: X" (exit 64)

A prompt starting with `@` names a file (`smartpipe map @prompt.md`). If the
prompt itself begins with a literal `@`, escape it as `@@`; the explicit form
is `--prompt-file FILE`.

## "--schema-from: unexpected 'X' for field 'Y'" (exit 64)

The [schema DSL](concepts/structured-output.md) parses before any model call,
so typos cost nothing. The message lists the whole grammar - types
(`string`, `number`, `integer`, `boolean`, `enum(a, b)`, `string[]`,
`number[]`) and constraints (`>= N`, `<= N`, `minLength=N`, `maxLength=N`,
`optional`).

## "⚠ skipped: line N (…)" - but the run continued

That's by design. A single item that fails (a malformed record, a model refusal, a
file that won't parse) is skipped with a warning; the rest of the batch runs. The
exit code is `1` (partial) so a script can notice. If *every* item failed, you get
exit `3`.

## The output looks different when I pipe it

Also by design. At a terminal, structured results show a readable view; piped, they
become `jsonl` (one JSON object per line). Force one with `--output json` (or `text`, `csv`, `tsv`). See
[output formats](concepts/output-formats.md).

## Why is there no ETA / percentage when I pipe input in?

Piped `stdin` is a stream - smartpipe processes lines as they arrive and can't know how
many are coming, so the progress line shows a count and rate instead. Named-file
mode knows its total and keeps the ETA.

## My `tail -f` pipeline never ends

That's `tail -f` - it follows forever. Your smartpipe results stream out as lines
arrive. End the pipeline with `| head -N` (smartpipe exits cleanly when downstream
closes) or Ctrl-C (drains and summarizes).

## I piped into `head` and smartpipe "died" (exit 141)

That's correct behavior, not a crash. When downstream closes the pipe (`head` got what
it needed), smartpipe dies instantly and silently - exactly like `grep` or `cat` - with
exit code 141 (SIGPIPE). Scripts using `set -o pipefail` can treat 141 from the left
side of a `| head` as expected.

## What does Ctrl-C do mid-run?

For `map`/`filter`/`embed`: the first Ctrl-C stops new work, finishes what's in flight
(≤10 s), writes those results, prints a `done: interrupted - …` summary to `stderr`, and
exits with the normal outcome code. Press Ctrl-C twice to bail immediately (exit 130).
`reduce`/`top_k` exit immediately - they have no partial result to save.

## "stdin looks like binary data smartpipe can't parse" (exit 2)

You redirected something smartpipe doesn't recognize (a zip? an executable?). On
`stdin` it accepts text lines or a single PDF/DOCX/PPTX/XLSX/audio/image document.
For files on disk, naming the file (`smartpipe map "…" report.pdf`) is the
general route.

## "model can't read images" - my image was skipped

The chat model you're using has no vision. Pick one that does:
`--model ollama/qwen3-vl` locally, or a cloud vision model.

## An internal error / "BUG" screen (exit 70)

That's a smartpipe bug, not your fault. The screen tells you how to report it; re-run
with `SMARTPIPE_DEBUG=1` for a traceback to include.

## Installing tab completion by hand

The `smartpipe config` wizard offers to install completions for `zsh` and
`bash`; if you declined, use a different shell, or manage your rc files
yourself, it's one line per shell. Completions cover verbs and flags - and
`--model` / `--embed-model` suggest your configured model plus whatever
Ollama has installed (if Ollama doesn't answer within 150 ms, you just get
no suggestions).

**zsh** - add to `~/.zshrc`:

```bash
eval "$(_SMARTPIPE_COMPLETE=zsh_source smartpipe)"
```

**bash** (4.4+) - add to `~/.bashrc`:

```bash
eval "$(_SMARTPIPE_COMPLETE=bash_source smartpipe)"
```

**fish** - write it once to your completions directory:

```bash
_SMARTPIPE_COMPLETE=fish_source smartpipe > ~/.config/fish/completions/smartpipe.fish
```

For a faster shell startup, redirect the script to a file and `source` it
instead of `eval`-ing on every new shell.

## See also

- [CLI reference](reference/cli.md) - every flag and exit code
- [Quickstart](quickstart.md) - get set up correctly the first time
