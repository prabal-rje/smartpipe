# Video Q&A and scene digests

Ask questions of video the way you'd ask them of text. On Gemini the model processes the whole file; everywhere else smartpipe samples frames + the
soundtrack and sends what the model can take - disclosed per row either way.

## Ask one question of one clip

```bash
smartpipe map "What is the speaker's main claim?" talk.mp4
```

## A scene-change digest (density matters)

The default sampling (1 fps up to 24 frames) is right for "what is this?".
For "what CHANGES?", guarantee the density:

```bash
smartpipe map "List each distinct scene with a one-line description {scenes string[]}" demo.mp4 --frame-every 1 --explode scenes
```

## A long lecture on a budget

```bash
smartpipe map "Outline the sections with timestamps if visible" lecture.mp4 --frame-every 10 --max-frames 90
```

One frame per 10 seconds, never more than 90 - the per-row note prints the
frame count and the run receipt totals the megabytes, so the cost of a
density choice is visible up front.

## Segment first when clips are long

```bash
smartpipe split --by seconds:60 webinar.mp4 \
| smartpipe map "summarize this minute"
```

`split` slices losslessly (keyframe-aligned); each segment stays real video,
so the pieces are sent as native video on Gemini or sampled independently
elsewhere.

## Video RAG: search a recording library, watch only the hits

Hundreds of screen recordings become searchable by meaning, with no vector
database - the index is a flat file of NDJSON vectors. Build it once:

```bash
# Index the library once: each clip's vector is half what it SHOWS, half what it SAYS
smartpipe embed 'sessions/**/*.mp4' > sessions.embeddings
```

Embedding blends both modalities: each video's vector is the 50/50 mean
of its visual description and its speech transcript, so both what it shows
and what it says are searchable.

From then on, any question, any day - ranking against the saved vectors is
free, and the vision pass pays only for the sessions that actually matter:

```bash
# Rank against the saved vectors (no re-embedding), then a vision pass over only the top hits
smartpipe top_k 3 --near "user gives up after the coupon code fails at checkout" < sessions.embeddings \
| jq -r .source \
| smartpipe map "Describe the failure {user_goal string, failure_point string, on_screen_error string}" --from-files --frame-every 2 --max-frames 60
```

`jq -r .source` turns the ranked records back into file paths, `--from-files`
hands them to `map`, and `--frame-every 2 --max-frames 60` pins the evidence
density (and the bill) for each session watched.
