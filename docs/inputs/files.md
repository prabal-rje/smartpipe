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

## Not yet

Images (describing a `.jpg` with a vision model) and reading a single binary file
straight from stdin (`sempipe map … < report.pdf`) are planned for a later release.
For now, use `--in` with image or document globs; images are skipped with a note.

## See also

- [Pipes & items](../concepts/pipes-and-items.md) — the item model
- [Install](../install.md) — the optional extras in full
