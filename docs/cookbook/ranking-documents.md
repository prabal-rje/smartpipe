# Ranking documents

**Goal:** given a folder of files and a query, find the most relevant ones - by
meaning, not keyword.

## The résumé screen

The classic: rank a stack of résumés against a role.

```console
$ smartpipe top_k 5 --near "senior distributed systems engineer, Rust, Kubernetes" --in 'resumes/*.pdf'
```

Output is the five closest files, best first, each with a similarity score:

```
resumes/chen.pdf	0.91
resumes/okafor.pdf	0.88
resumes/silva.pdf	0.85
resumes/patel.pdf	0.83
resumes/nguyen.pdf	0.81
```

`top_k` parses each PDF, embeds it, and ranks by cosine similarity to your query.

## Embed once, query many times

If you'll run several queries against the same corpus, embed it once and reuse the
vectors - much cheaper than re-embedding every time:

```console
$ smartpipe embed --in 'docs/**/*.md' > corpus.embeddings

$ cat corpus.embeddings \
    | smartpipe top_k 10 --near "our data retention policy"
$ cat corpus.embeddings \
    | smartpipe top_k 10 --near "incident response runbook"
```

`top_k` sees the precomputed `vector` in each record and skips re-embedding.

## Rank, then extract

Feed the top files straight into `map` to pull structured data from just the winners:

```console
$ smartpipe top_k 20 --near "indemnification clause" --in 'legal/*.pdf' \
    | cut -f1 \
    | smartpipe map "Extract {clause_text, liability_cap}" --from-files --output csv
```

`cut -f1` drops the score column, leaving filenames; `--from-files` feeds them to
`map`.

## Threshold instead of a fixed count

Don't know how many are relevant? Keep everything above a similarity bar:

```console
$ smartpipe top_k --near "GDPR compliance" --threshold 0.8 --in 'policies/*.pdf'
```

## See also

- [`top_k`](../verbs/top-k.md) · [`embed`](../verbs/embed.md) ·
  [Models & providers](../concepts/models-and-providers.md)
