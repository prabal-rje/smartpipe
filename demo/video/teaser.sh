#!/usr/bin/env bash
# teaser.sh - cut the README teaser GIF from the rendered demo (the teaser-v5 recipe).
#
# Checked in per ledger item 79(b): until now this recipe lived only in commit
# messages (45e7d07 "teaser v5", 51411ec "teaser v4", 311dc46, b0f0135, 3c99eb3).
# Reconstructed from that record - 12.5 fps, 960 px wide, two-pass palette
# (palettegen + paletteuse), three segments from the 86-second narrated cut:
#
#   1. ColdOpen logo still   0.00-3.04s   the pinned LOGO-FIRST open: a motionless
#                                         3-second brand card (v5 fixed v4, which
#                                         opened mid-action)
#   2. hook cascade          5.00-12.80s  ends on the settled caption, BEFORE the
#                                         subtitle fade at 12.87s
#   3. graph reveal          68.80-73.20s ends 0.3s after the last edge finishes
#                                         drawing
#
# Pinned teaser rules (owner rulings, teaser v5):
#   - the GIF opens ON the static logo card, held ~3s, no motion visible
#   - every segment ENDS SETTLED - never mid-animation, never mid-fade
#   - total 15.24s at 12.5 fps / 960w came out to 1.23 MB on the v5 render
#
# Release ritual: the published asset NAME must change on every re-render
# (smartpipe-teaser-v6.gif, ...) - GitHub's camo image cache serves stale bytes
# for a reused name. Upload to the demo-assets release, then point README.md at it.
#
# Note: palettegen/paletteuse options beyond the two-pass structure were never
# recorded in the commit record; ffmpeg defaults stand in for them here. This
# script is reconstruction-verified (parse + recipe cross-check), not
# render-verified - the narration wavs needed to re-render out/smartpipe-demo.mp4
# are a release asset (narration-wavs-rime-v1.zip), not in-tree.
#
# Usage: ./teaser.sh [INPUT.mp4] [OUTPUT.gif]
set -euo pipefail

INPUT="${1:-out/smartpipe-demo.mp4}"
OUTPUT="${2:-out/smartpipe-teaser.gif}"
FPS=12.5
WIDTH=960

if [ ! -f "$INPUT" ]; then
  echo "error: no rendered demo at $INPUT (npm run render, or pass a path)" >&2
  exit 64
fi

# The shared segment chain: three settled cuts, concatenated, then fps + scale.
SEGMENTS="\
[0:v]trim=start=0:end=3.04,setpts=PTS-STARTPTS[s0];\
[0:v]trim=start=5.0:end=12.8,setpts=PTS-STARTPTS[s1];\
[0:v]trim=start=68.8:end=73.2,setpts=PTS-STARTPTS[s2];\
[s0][s1][s2]concat=n=3:v=1:a=0,fps=${FPS},scale=${WIDTH}:-2:flags=lanczos"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
PALETTE="$WORKDIR/palette.png"

# Pass 1: build the palette over the exact frames the GIF will contain
# (-update 1: palettegen emits a single image, not a sequence).
ffmpeg -v warning -y -i "$INPUT" \
  -filter_complex "${SEGMENTS},palettegen" \
  -update 1 "$PALETTE"

# Pass 2: encode the GIF through that palette.
ffmpeg -v warning -y -i "$INPUT" -i "$PALETTE" \
  -filter_complex "${SEGMENTS}[v];[v][1:v]paletteuse" \
  "$OUTPUT"

echo "teaser written: $OUTPUT ($(du -h "$OUTPUT" | cut -f1)) - expect ~15.24s @ ${FPS}fps/${WIDTH}w" >&2
