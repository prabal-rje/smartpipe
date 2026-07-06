# File inputs

Every verb can read files instead of stdin lines. Point it at documents and each
*file* becomes one item — sempipe figures out how to read it.

## `--in` — a glob of files

```console
# Each PDF is one item; summarize each:
$ sempipe map "Summarize this document" --in 'reports/*.pdf'

# Rank résumés by relevance (the classic):
$ sempipe top_k 5 --near "distributed systems engineer" --in 'resumes/*.pdf'

# Keep the documents that mention a topic:
$ sempipe filter "discusses budget cuts" --in 'board-notes/**/*.md'
```

> **Quote the pattern.** Write `--in '*.pdf'` with quotes so your shell passes the
> pattern to sempipe instead of expanding it first. `**` recurses into
> subdirectories.

## `--from-files` — filenames on stdin

Compose with `find`, `ls`, `git`, or anything that lists paths:

```console
$ find . -name '*.md' -mtime -7 | sempipe map "Summarize" --from-files
$ git ls-files '*.py' | sempipe filter "has a TODO comment" --from-files
```

Each stdin line is treated as a filename; each file becomes one item.

## The user never names a parser

This is the point: you don't tell sempipe *how* to read a file. It detects the kind
and extracts the text automatically.

| You point at… | sempipe does… |
|---|---|
| `.txt` `.md` `.csv` `.json` | reads it as text |
| `.pdf` `.docx` `.pptx` `.xlsx` `.html` `.epub` | extracts the text (needs `sempipe[files]`) |
| `.mp3` `.wav` `.flac` … | transcribes it (needs `sempipe[audio]`) |
| anything unreadable | skips it with a warning — never crashes |

Detection is by extension first, with a magic-byte fallback for files whose name
doesn't say what they are.

## Optional dependencies

Parsing documents and audio needs extra packages, kept optional so a plain install
stays tiny:

```console
$ pip install 'sempipe[files]'    # PDF, Word, PowerPoint, Excel, HTML, EPUB
$ pip install 'sempipe[audio]'    # audio transcription
$ pip install 'sempipe[all]'      # everything
```

If you point `--in` at PDFs without `sempipe[files]` installed, sempipe tells you
exactly what to install (once), then skips those files.

## What file mode returns

- **`map` / `reduce`** work on the extracted text, exactly like any other item.
- **`embed`** embeds the extracted text; the record's `source` is the file's path.
- **`filter` / `top_k`** emit the **filename** (not the document's text) — so ranking
  or filtering a folder of documents gives you back a list of paths, the useful Unix
  result. `top_k` appends the score: `resumes/alice.pdf⇥0.87`.

## Skipped files never stop the run

A corrupt PDF, an unsupported binary, or a file you can't read is skipped with a
warning on stderr — the rest of the batch still runs. The exit code reflects it:
`1` if some files were skipped, `3` if every one failed.

## Images: described by your vision model

Point `map` at images and each one is sent — bytes and all — to the model, with the
prompt applied to what it *sees*:

```console
$ sempipe map "Describe the product shown" --in 'photos/*.jpg' --model ollama/qwen3-vl
$ sempipe map "Extract {brand, color}" --in shelf.png --model gpt-4o-mini
```

The chat model must be vision-capable; if it isn't, the item is skipped with a
message naming a model to try. The other verbs read text, so they skip image items
with a pointer to `map`.

## A binary document on stdin

Redirect a single document and it becomes one item — sempipe sniffs the bytes,
spools, and parses it exactly as `--in` would:

```console
$ sempipe map "Summarize this document" < report.pdf
```

One document per run (stdin is one stream); for many documents use `--in`.
Unrecognizable binary data stops with a clear message instead of garbling.

## Mixing files and a pipe

`--in` composes with piped stdin: the files come first (glob-sorted), then the
stdin lines, one run:

```console
$ cat extra-notes.txt | sempipe map "Summarize" --in 'reports/*.pdf'
```

## Audio: heard natively, or transcribed

An audio file (`.wav`, `.mp3`, `.m4a`, `.ogg`, `.flac`) becomes an item carrying
its **bytes**, not an eager transcript:

- `map` with an audio-capable model (`gpt-4o-audio-preview`, `voxtral-*`) sends
  the sound itself — tone and speaker changes included.
- With any other model, sempipe transcribes locally when the `[audio]` extra is
  installed (you'll see `note: transcribing audio locally` once), then retries
  as text.
- The text verbs (`filter`, `embed`, `top_k`, `reduce`, `join`) transcribe on
  demand with the extra, or skip with a line naming both fixes.

## See also

- [Pipes & items](../concepts/pipes-and-items.md) — the item model
- [Install](../install.md) — the optional extras in full
