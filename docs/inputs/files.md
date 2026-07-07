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
$ sempipe map "Extract {brand, color}" --in shelf.png --model gpt-5.4-mini
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

## Video: frames + soundtrack

A video file becomes an item carrying its bytes. On `gemini-2.5-*` models the
video rides the native wire whole — the model watches it, soundtrack included.
Everywhere else `map` converts it locally
(ffmpeg, via `pip install 'sempipe[video]'` or PATH): six evenly-sampled frames
plus the audio track, sent natively when the model can see/hear, with a whisper
transcript as the fallback rung. Every conversion is announced on its row
(`⚠ degraded: demo.mp4 video → frames+audio (6 frames + audio)`). Text verbs
transcribe the track and say the frames were dropped. `split --by seconds:N`
slices video losslessly (keyframe-aligned) into segments that stay video.

## Documents carry their figures (D32)

`map "summarize" --in report.pdf` sends the text **and** the embedded images —
up to 8 figures per document (a stderr note counts them:
`report.pdf: 5 figures attached (3 more capped)`), icons under 4 KB dropped.
Per page, fused:

```console
$ sempipe split --by pages --media --in report.pdf \
    | sempipe map "summarize this page, including what each figure shows"
```

One item per page with that page's text and figures together. Text verbs
(`filter`, `reduce`, …) use the text and drop figure parts with a per-row
`⚠ degraded:` note. DOCX has no fixed pages, so figures attach at document
level there.

## Standalone figure extraction: `split --media`

Document parsing extracts **text**; figures embedded in a PDF/DOCX/PPTX/XLSX
don't ride along implicitly (a 100-page deck can carry 300 decorative logos —
an item explosion you should choose, not inherit). When you want them:

```console
$ sempipe split --media --in report.pdf | sempipe map "describe this figure"
{"image_b64": "…", "mime": "image/jpeg", "source": "report.pdf p.7 img.2"}
```

Each embedded image becomes an item with page provenance, byte-identical
(never re-encoded), and the next verb *sees* it. Icons under 4 KB are dropped
and counted once on stderr. Office formats yield every embedded PNG/JPEG/GIF/WebP;
PDFs yield JPEG-compressed images (the overwhelming majority of real photos in
PDFs — other encodings would need re-encoding and are skipped for now).

## Audio: heard natively, or transcribed

An audio file (`.wav`, `.mp3`, `.m4a`, `.ogg`, `.flac`) becomes an item carrying
its **bytes**, not an eager transcript:

- `map` with an audio-capable model (`gemini-2.5-*`, `voxtral-*`) sends
  the sound itself — tone and speaker changes included.
- With any other model, sempipe transcribes **locally** when the `[audio]`
  extra is installed (a one-time stderr note says so), then retries as text.
  The transcriber is faster-whisper, `tiny` by default: fast, but rough on
  names and noisy audio. `SEMPIPE_WHISPER_MODEL=small` (or `medium`,
  `large-v3`) trades speed for accuracy; the first use of a size downloads its
  weights once. Audio never leaves your machine on this path.
- The text verbs (`filter`, `embed`, `top_k`, `reduce`, `join`) transcribe on
  demand with the extra, or skip with a line naming both fixes.

## See also

- [Pipes & items](../concepts/pipes-and-items.md) — the item model
- [Install](../install.md) — the optional extras in full


## Scanned documents

Scanned PDFs have no text layer. sempipe detects the thin layer, keeps the
page images on the item, and says so:

```
note: contract.pdf: thin text layer (11 chars) — scanned? routed 8 page image(s)
      to the vision path (22 more capped — split --by pages --media processes every page)
```

`map` then reads the pages with a vision model directly (the LLM **is** the
OCR); text verbs caption them through the conversion ladder (consent rules
apply). For long scans, `split --by pages --media` processes every page —
the whole-document item caps at 8 images for request-size sanity. Pick a
model that can see (`gpt-5.4-mini`, `gemini-2.5-flash`, `ollama/llava`);
`sempipe doctor --probe` verifies actual ability.
