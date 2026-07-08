# readable - render records for eyes

readable turns JSONL records into the same tidy blocks you see at the terminal, anywhere you point it: **Free - never calls a model.**

```bash
cat results.jsonl \
| smartpipe readable | less -R

… | smartpipe map "Extract {vendor, total}" \
| smartpipe readable > report.txt
```

Each record renders as an indented block - nested maps indent, lists render
as `- ` rows, multi-line strings as block scalars - with the `__` metadata
spine dimmed at the bottom. Plain text items pass through unchanged; blocks
separate with a blank line. Color appears only when readable's own stdout is
a terminal; piped or redirected output is plain text.

| Flag | Effect |
|---|---|
| `--full` | show whole values (no `… (+N chars)` / `… (+N items)` truncation) |
| `--bare` | drop the `__` metadata fields entirely |

## Media previews

At a color terminal, an item carrying media renders a preview under its
`__media` summary line - the same previews the default terminal view shows:

- **Images** - a small color thumbnail (aspect ratio preserved, about
  40x12 cells).
- **Video** - a 3-frame strip sampled at 10%/50%/90% of the duration
  (never the first frame - intros are black or logos).
- **Audio** - a waveform envelope of the clip (long files decode at most
  the first 10 minutes).
- **Playback** - audio and video whose source file still exists on disk get
  a `▶ play (0:42, 2.1 MB)` line: an OSC 8 hyperlink to the `file://` URL,
  so a click opens the clip in your system player. Media that exists only
  as pipe bytes shows its preview without the link.

Only the first media part of an item is previewed; the summary line still
names the rest. Previews never appear in pipes, under `NO_COLOR`, or with
`--bare` (there is no `__media` left to preview) - piped bytes stay exactly
as before. Turn them off everywhere with:

```bash
smartpipe config media-previews off
```

## See also

- [Output formats](../concepts/output-formats.md) - the terminal view vs pipes
- [The item](../concepts/the-item.md) - the `__media` and `__source` spine
- [`write`](../reference/cli.md) - the file door; readable is the eyes door
