# The item

Everything smartpipe does follows from five laws about what flows through a
pipe. Learn these once and every verb, flag, and output shape becomes
predictable.

## The five laws

1. **Everything in a pipe is an item (a record).** Verbs never see "a file"
   or "a line" - they see records with fields.
2. **A plain text line is shorthand for a text-only record.** `hello` and
   `{"text": "hello"}` are the same item; the short spelling exists for
   humans.
3. **A file is a crate of items** - one item or many, depending on the
   declared granularity (see the ladder below).
4. **Verbs transform records.** `map` on a record answers with a record
   (`{"result": …}` plus provenance); `extend` adds fields; `filter` passes
   records through byte-identically.
5. **At the edges, simple records leave as plain text (the human door),
   full records leave as JSONL (the machine door).** Which door you get is
   decided by what the record is, never by sniffing where the bytes go.

## The granularity ladder

How a crate becomes items is a dial, not a guess:

- **Paths decide the default.** A named `.jsonl`/`.ndjson` file cuts into
  strict records (a bad row is a loud error naming file and line); every
  other path is one whole-file item.
- **`--as` overrides.** `--as file` slurps (even stdin - the whole pipe
  becomes one document); `--as lines` reads every line as text, even lines
  that look like JSON; `--as jsonl` demands one record per line.
- **`split` cuts below the file.** Pages, tokens, minutes, seconds - each
  chunk is an item that remembers how it was cut.

Media crates (images, audio, video) support only `--as file`; the finer cuts
live in `split --by minutes/seconds` (clips) and nowhere (images have no
finer granularity).

An item bigger than the model's window is still ONE item - the verbs handle
the overflow themselves (chunk + combine in `map`/`extend`, any-chunk match
in `filter`/`join`, the recursive tree in `reduce`, mean-pooling in the
embedding verbs), disclose the plan on stderr before spending anything, and
`--whole` opts back into refusal. The full per-verb matrix lives in
[when it doesn't fit](feeding-smartpipe.md#when-it-doesnt-fit).

Worked example - translate a folder, four ways:

```bash
# each file is one item: one translation per document
smartpipe map "translate to French" 'notes/*.txt'

# each LINE of each file is one item: line-by-line translation
smartpipe map "translate to French" 'notes/*.txt' --as lines

# strict records: each row must be a {…} object
smartpipe map "translate {text}" 'rows/*.jsonl'

# the whole pipe as ONE document (a poem keeps its shape)
cat poem.txt \
| smartpipe map "translate to French, keep the line breaks" --as file
```

## The read/write mirror

`smartpipe PATH…` with no verb is the reader: it emits the crate's items as
JSONL records. `smartpipe write TEMPLATE` is the mirror image: items go back
to files the same way they were cut - whole-file items (and media) each get
their own file; line and segment items append into their target, reassembled
in spine order, so concurrency upstream can never scramble them.

```bash
# read a folder, translate, write the mirror back
smartpipe 'notes/*.txt' --as lines \
| smartpipe map "translate to French" \
| smartpipe write 'fr/{name}'
# {name} = the source file's name, carried by __source - notes/a.txt becomes fr/a.txt
```

Three doors out of a pipe: `write` routes items to files, `readable` renders
them for eyes, and plain stdout is for machines (`--bare` strips the metadata
for `>` redirections).

## The `__` spine

Double-underscore fields are smartpipe's reserved metadata namespace - the
spine an item travels on:

- `__source` - how the item was cut: `{"path": …, "as": "lines", "line": 12}`
  (plus a human label like `report.pdf §3/12` when a stage created it).
- `__media` - media transport: `{"kind": "image", "mime": …, "data_b64": …}`.
- `__score` - join's per-pair similarity.
- `__invalid` / `__error` / `__raw` - the `--keep-invalid` failure markers.

Known spine fields round-trip through any number of stages. Unknown `__`
fields warn once and carry through untouched; your own data may use at most
one leading underscore. The terminal preview shows the spine dimmed at the
bottom of each block; `--bare` (or `write` without `--keep-meta`) strips it.

## What the model sees

When an item reaches a model (`map`, `extend`, `filter`, `reduce`), its
payload rides in an `<input>` block. A record renders as a minimal
`key: value` block in its own field order - lists as `- ` rows, nesting
indented - and plain text rides unchanged:

```text
Summarize the ticket

<input>
id: 812
customer: acme
body: crashes on save
</input>
```

The `__` spine never appears there: provenance, scores, and the `__media`
transport are tool plumbing, not content (media itself rides the API's
native image/audio parts). `--dry-run` on `map`/`extend` prints exactly
this composed request.

## See also

- [Pipes & items](pipes-and-items.md) - the pipeline mental model
- [Structured output](structured-output.md) - braces, types, schemas
- [Output formats](output-formats.md) - the four output modes
