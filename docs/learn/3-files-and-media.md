# 3 · Files and media

smartpipe is multimodal end to end: PDFs, images, audio, and video are items
like any other. This chapter teaches how files become items and how to
control the cut.

## Name the files

Files go after the prompt; each one becomes an item, whatever it is:

```bash
smartpipe map "Summarize this document" 'reports/*.pdf'
smartpipe map "What does the caller want?" 'calls/*.mp3'
smartpipe filter "shows a person" 'photos/*.jpg'
```

Quote the glob so your shell doesn't expand it. There is no parser to pick -
smartpipe detects the kind, extracts text from documents, and carries media
bytes to models that can see or hear them (converting and disclosing when
they can't).

## The granularity dial

A file is a crate of items; `--as` decides how it opens
([the item](../concepts/the-item.md) has the laws):

```bash
smartpipe map "translate to French" 'notes/*.txt'                # one item per file
smartpipe map "translate to French" 'notes/*.txt' --as lines    # one item per line
cat poem.txt | smartpipe map "translate, keep the shape" --as file   # whole pipe = one item
```

Named `.jsonl` files cut into strict records automatically; a bad row is a
loud error naming the file and line.

## Peek before you spend

With no verb at all, the binary is the reader - see exactly what items a
file yields, for free:

```bash
smartpipe report.pdf | head -2
smartpipe notes.txt --as lines | head -3
```

`--dry-run` on `map`/`extend` goes one step further: it prints the fully
composed first request (system prompt, schema, the first item's text) and
exits without any model call.

## Below the file: split

When one document is too big, or you want the pieces addressable:

```bash
smartpipe split --by pages:5 report.pdf | smartpipe map "summarize these pages"
smartpipe split --by minutes:10 call.wav | smartpipe map "what was agreed?"
smartpipe split --media 'decks/*.pptx' | smartpipe map "what does this chart claim?"
```

Audio slices stay audio (the next verb can *hear* them); every chunk carries
`__source` provenance so downstream tools - and `smartpipe write` - know
where it came from.

Next: [4 · The free verbs](4-free-verbs.md) - cut the corpus before anything
costs money.
