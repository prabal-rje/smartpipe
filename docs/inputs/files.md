# File inputs

Every verb can read files instead of stdin lines. Point it at documents and each
*file* becomes one item - smartpipe figures out how to read it.

## `--in` - a glob of files

```console
# Each PDF is one item; summarize each:
$ smartpipe map "Summarize this document" --in 'reports/*.pdf'

# Rank résumés by relevance (the classic):
$ smartpipe top_k 5 --near "distributed systems engineer" --in 'resumes/*.pdf'

# Keep the documents that mention a topic:
$ smartpipe filter "discusses budget cuts" --in 'board-notes/**/*.md'
```

> **Quote the pattern.** Write `--in '*.pdf'` with quotes so your shell passes the
> pattern to smartpipe instead of expanding it first. `**` recurses into
> subdirectories.

## `--from-files` - filenames on stdin

Compose with `find`, `ls`, `git`, or anything that lists paths:

```console
$ find . -name '*.md' -mtime -7 \
    | smartpipe map "Summarize" --from-files
$ git ls-files '*.py' \
    | smartpipe filter "has a TODO comment" --from-files
```

Each stdin line is treated as a filename; each file becomes one item.

## The user never names a parser

This is the point: you don't tell smartpipe *how* to read a file. It detects the kind
and extracts the text automatically.

| You point at… | smartpipe does… |
|---|---|
| `.txt` `.md` `.csv` `.json` | reads it as text |
| `.pdf` `.docx` `.pptx` `.xlsx` `.html` `.epub` | extracts the text (built in) |
| `.mp3` `.wav` `.flac` … | carries the audio bytes; the next verb either sends them to an audio-capable model or transcribes them through the configured ladder |
| anything unreadable | skips it with a warning - never crashes |

Detection is by extension first, with a magic-byte fallback for files whose name
doesn't say what they are.

## Parser availability

Parsing documents, audio, video, and charts ships with the normal install:

```console
$ pip install smartpipe    # everything ships in the box - documents, video, charts, all of it
```

If a parser is unavailable in a broken or unsupported environment, smartpipe tells
you exactly what is missing once, then skips those files.

## What file mode returns

- **`map` / `reduce`** work on the extracted text, exactly like any other item.
- **`embed`** embeds the extracted text; the record's `source` is the file's path.
- **`filter` / `top_k`** emit the **filename** (not the document's text) - so ranking
  or filtering a folder of documents gives you back a list of paths, the useful Unix
  result. `top_k` appends the score: `resumes/alice.pdf⇥0.87`.

## Skipped files never stop the run

A corrupt PDF, an unsupported binary, or a file you can't read is skipped with a
warning on stderr - the rest of the batch still runs. The exit code reflects it:
`1` if some files were skipped, `3` if every one failed.

## Images: described by your vision model

Point `map` at images and each one is sent - bytes and all - to the model, with the
prompt applied to what it *sees*:

```console
$ smartpipe map "Describe the product shown" --in 'photos/*.jpg' --model ollama/qwen3-vl
$ smartpipe map "Extract {brand, color}" --in shelf.png --model gpt-5.4-mini
```

The chat model must be vision-capable; if it isn't, the item is skipped with a
message naming a model to try. The other verbs read text, so they skip image items
with a pointer to `map`.

## A binary document on stdin

Redirect a single document and it becomes one item - smartpipe sniffs the bytes,
spools, and parses it exactly as `--in` would:

```console
$ smartpipe map "Summarize this document" < report.pdf
```

One document per run (stdin is one stream); for many documents use `--in`.
Unrecognizable binary data stops with a clear message instead of garbling.

## Mixing files and a pipe

`--in` composes with piped stdin: the files come first (glob-sorted), then the
stdin lines, one run:

```console
$ cat extra-notes.txt \
    | smartpipe map "Summarize" --in 'reports/*.pdf'
```

## Video: watched, or frames + soundtrack

A video file becomes an item carrying its bytes.

On gemini models, the video rides the native wire whole: visuals and soundtrack
together. Everywhere else, `map`/`extend` convert it locally with ffmpeg into frames
plus audio.

The default is **one frame per second up to 24**, evenly spread past that. Tune the
sampling when it matters:

```console
$ smartpipe map "what changes between scenes?" --in demo.mp4 --frame-every 1
$ smartpipe map "summarize this lecture" --in talk.mp4 --frame-every 5 --max-frames 120
```

`--frame-every SECONDS` guarantees the density (and lifts the 24-frame cap);
`--max-frames N` is the budget - the smaller wins. Every conversion is
announced on its row (`⚠ degraded: demo.mp4 video → frames+audio (24 frames +
audio)`), and the run receipt totals the megabytes sent.

Text and embedding verbs use the **halves pivot** (D36): the visual
description and the speech transcript, embedded as a 50/50 mean - so a video's
vector carries what it *shows* as well as what it *says*. `split --by
seconds:N` slices video losslessly (keyframe-aligned) into segments that stay
video.

## Documents carry their figures (D32)

`map "summarize" --in report.pdf` sends the text **and** the embedded images -
up to 8 figures per document (a stderr note counts them:
`report.pdf: 5 figures attached (3 more capped)`), icons under 4 KB dropped.
Per page, fused:

```console
$ smartpipe split --by pages --media --in report.pdf \
    | smartpipe map "summarize this page, including what each figure shows"
```

One item per page with that page's text and figures together. Text verbs
(`filter`, `reduce`, …) use the text and drop figure parts with a per-row
`⚠ degraded:` note. DOCX has no fixed pages, so figures attach at document
level there.

## Standalone figure extraction: `split --media`

Document parsing extracts **text**; figures embedded in a PDF/DOCX/PPTX/XLSX
don't ride along implicitly (a 100-page deck can carry 300 decorative logos -
an item explosion you should choose, not inherit). When you want them:

```console
$ smartpipe split --media --in report.pdf \
    | smartpipe map "describe this figure"
{"image_b64": "…", "mime": "image/jpeg", "source": "report.pdf p.7 img.2"}
```

Each embedded image becomes an item with page provenance, byte-identical
(never re-encoded), and the next verb *sees* it. Icons under 4 KB are dropped
and counted once on stderr. Office formats yield every embedded PNG/JPEG/GIF/WebP;
PDFs yield JPEG-compressed images (the overwhelming majority of real photos in
PDFs - other encodings would need re-encoding and are skipped for now).

## Audio: heard natively, or transcribed

An audio file (`.wav`, `.mp3`, `.m4a`, `.ogg`, `.flac`) becomes an item carrying
its **bytes**, not an eager transcript:

- `map` with an audio-capable model (gemini models, `voxtral-*`) sends the sound to
  that configured endpoint - tone and speaker changes included.

- With a text-only model, smartpipe needs a transcript. It uses a configured remote
  transcriber (`stt-model`, or OpenAI `whisper-1` on the OpenAI API-key path when
  consent allows it); otherwise it uses local faster-whisper (`tiny` by default),
  then retries as text. `SMARTPIPE_WHISPER_MODEL=small` (or `medium`, `large-v3`)
  trades speed for accuracy; the first use of a size downloads its weights once.
  Audio never leaves your machine on the local whisper path.

- The text verbs (`filter`, `embed`, `top_k`, `reduce`, `join`) transcribe on
  demand through the same ladder, or skip with a line naming the fixes.

## See also

- [Pipes & items](../concepts/pipes-and-items.md) - the item model
- [Install](../install.md) - package contents and environment notes


## Scanned documents

Scanned PDFs have no text layer. smartpipe detects the thin layer, keeps the
page images on the item, and says so:

```
note: contract.pdf: thin text layer (11 chars) - scanned? routed 8 page image(s)
      to the vision path (22 more capped - split --by pages --media processes every page)
```

`map` reads those pages with a vision model directly; the LLM is the OCR. Text verbs
caption them through the conversion ladder, with the usual consent rules.

For long scans, `split --by pages --media` processes every page. The whole-document
item caps at 8 images for request-size sanity. Pick a model that can see
(`gpt-5.4-mini`, `gemini-3.1-flash-lite`, `ollama/llava`); `smartpipe doctor --probe`
verifies actual ability.
