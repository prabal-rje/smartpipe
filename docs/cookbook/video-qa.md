# Video Q&A and scene digests

Ask questions of video the way you'd ask them of text. On Gemini the model processes the whole file; everywhere else smartpipe samples frames + the
soundtrack and sends what the model can take - disclosed per row either way.

## Ask one question of one clip

```console
$ smartpipe map "What is the speaker's main claim?" --in talk.mp4
```

## A scene-change digest (density matters)

The default sampling (1 fps up to 24 frames) is right for "what is this?".
For "what CHANGES?", guarantee the density:

```console
$ smartpipe map "List each distinct scene with a one-line description {scenes string[]}" --in demo.mp4 --frame-every 1 --explode scenes
```

## A long lecture on a budget

```console
$ smartpipe map "Outline the sections with timestamps if visible" --in lecture.mp4 --frame-every 10 --max-frames 90
```

One frame per 10 seconds, never more than 90 - the per-row note prints the
frame count and the run receipt totals the megabytes, so the cost of a
density choice is visible up front.

## Segment first when clips are long

```console
$ smartpipe split --by seconds:60 --in webinar.mp4 \
    | smartpipe map "summarize this minute"
```

`split` slices losslessly (keyframe-aligned); each segment stays real video,
so the pieces are sent as native video on Gemini or sampled independently
elsewhere.

## Search a video library by meaning

```console
$ smartpipe embed --in 'clips/*.mp4' > library.embeddings
$ smartpipe top_k 5 --near "the demo where checkout fails" < library.embeddings
```

Embedding blends both modalities: each video's vector is the 50/50 mean
of its visual description and its speech transcript, so both what it shows
and what it says are searchable.
