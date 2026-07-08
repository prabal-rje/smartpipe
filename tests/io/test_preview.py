"""Terminal media previews — the io shell, hermetically.

Images pin deterministic plotext output for a fixed tiny PNG as a golden;
video/audio ride the portable fake-ffmpeg pattern (a python script launched
through the real interpreter — see tests/io/test_metering.py); the play link
appears only when the ``__source`` spine names a file that still exists.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest

from smartpipe.io.preview import maybe_preview, preview_lines

GOLDEN = Path(__file__).parent.parent / "golden" / "preview"

# a 4x4 PNG: four solid color quadrants (94 bytes) — the fixed golden source
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAJUlEQVR4nGO8Y6TPwMCwMv0i"
    "AwMDEwMSYFTrvsTAwND/Tw9dBgDVKAZjFfrf/AAAAABJRU5ErkJggg=="
)
TINY_PNG = base64.b64decode(TINY_PNG_B64)


def _media_record(kind: str, mime: str, data: bytes, **extra: object) -> dict[str, object]:
    return {
        "text": "a clip",
        "__media": {"kind": kind, "mime": mime, "data_b64": base64.b64encode(data).decode()},
        **extra,
    }


def _match_golden(name: str, rendered: str) -> None:
    path = GOLDEN / f"{name}.txt"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    assert rendered == path.read_text(encoding="utf-8"), (
        f"preview '{name}' drifted from its golden; if intended, run: make golden"
    )


def _fake_ffmpeg(tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The portable fake-ffmpeg: a python script through the real interpreter
    (a #!/bin/sh file can't exec on the windows runners)."""
    fake_py = tmp_path / "fake_ffmpeg.py"
    fake_py.write_text(body, encoding="utf-8")
    if sys.platform == "win32":
        fake = tmp_path / "ffmpeg.bat"
        fake.write_text(f'@"{sys.executable}" "{fake_py}" %*\n', encoding="utf-8")
    else:
        fake = tmp_path / "ffmpeg"
        fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_py}" "$@"\n', encoding="utf-8")
        fake.chmod(0o755)
    from smartpipe.parsing import extract

    monkeypatch.setattr(extract, "ffmpeg_exe", lambda: str(fake))
    return fake


# a fake that answers all three shapes the previewer asks for:
#   ffmpeg -i SRC                      → Duration banner on stderr (exit 1, like real ffmpeg)
#   ffmpeg … -f s16le … -             → deterministic PCM on stdout (a rising sawtooth)
#   ffmpeg … -ss T -i SRC -frames:v 1 OUT → the tiny PNG written to OUT, T logged
_FULL_FAKE = f"""
import base64, os, sys
args = sys.argv[1:]
log = os.environ.get("FAKE_FFMPEG_LOG")
if "-frames:v" in args:
    if log:
        with open(log, "a", encoding="utf-8") as sink:
            sink.write(args[args.index("-ss") + 1] + "\\n")
    with open(args[-1], "wb") as sink:
        sink.write(base64.b64decode("{TINY_PNG_B64}"))
    sys.exit(0)
if "s16le" in args:
    pcm = b"".join(
        int(value).to_bytes(2, "little", signed=True)
        for value in [0, 1000, -2000, 4000, -8000, 16000, -32000, 8000]
    )
    sys.stdout.buffer.write(pcm)
    sys.exit(0)
print("Duration: 00:01:23.00", file=sys.stderr)
sys.exit(1)
"""


# --- images: the golden thumbnail ---------------------------------------------------


def test_image_thumbnail_matches_the_golden() -> None:
    lines = preview_lines(_media_record("image", "image/png", TINY_PNG), color=True, width=80)
    assert lines, "a readable PNG must render a thumbnail"
    assert all(line.startswith("  ") for line in lines)  # nested under the __media line
    _match_golden("image-thumb", "\n".join(lines))


def test_image_thumbnail_never_renders_a_play_link() -> None:
    record = _media_record("image", "image/png", TINY_PNG)
    lines = preview_lines(record, color=True, width=80)
    assert not any("play" in line for line in lines)


def test_unreadable_image_degrades_to_a_dim_note() -> None:
    record = _media_record("image", "image/png", b"not an image at all")
    lines = preview_lines(record, color=True, width=80)
    assert lines == ["  \x1b[2m(no preview: unrecognized image format)\x1b[0m"]


def test_only_the_first_media_part_is_previewed() -> None:
    single = _media_record("image", "image/png", TINY_PNG)
    encoded = single["__media"]
    both = dict(single)
    both["__media"] = [encoded, encoded]
    assert preview_lines(both, color=True, width=80) == preview_lines(single, color=True, width=80)


def test_no_media_means_no_lines() -> None:
    assert preview_lines({"text": "plain"}, color=True, width=80) == []


# --- audio: waveform + play link ----------------------------------------------------


def test_audio_waveform_matches_the_golden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_ffmpeg(tmp_path, _FULL_FAKE, monkeypatch)
    record = _media_record("audio", "audio/mpeg", b"mp3-ish bytes")
    lines = preview_lines(record, color=True, width=80)
    _match_golden("audio-wave", "\n".join(lines))


def test_audio_with_an_on_disk_source_gets_the_play_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_ffmpeg(tmp_path, _FULL_FAKE, monkeypatch)
    clip = tmp_path / "call.mp3"
    clip.write_bytes(b"mp3-ish bytes")
    record = _media_record(
        "audio", "audio/mpeg", b"mp3-ish bytes", __source={"path": str(clip), "as": "file"}
    )
    lines = preview_lines(record, color=True, width=80)
    last = lines[-1]
    assert "\x1b]8;;" in last and last.endswith("\x1b]8;;\x1b\\")  # an OSC 8 hyperlink
    assert clip.resolve().as_uri() in last
    assert "▶ play (1:23, " in last  # duration from the ffmpeg banner


def test_bytes_only_audio_omits_the_play_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_ffmpeg(tmp_path, _FULL_FAKE, monkeypatch)
    record = _media_record(
        "audio", "audio/mpeg", b"mp3-ish bytes", __source={"path": "-", "as": "jsonl", "line": 1}
    )
    lines = preview_lines(record, color=True, width=80)
    assert lines
    assert not any("play" in line for line in lines)


def test_a_vanished_source_path_omits_the_play_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_ffmpeg(tmp_path, _FULL_FAKE, monkeypatch)
    record = _media_record(
        "audio",
        "audio/mpeg",
        b"mp3-ish bytes",
        __source={"path": str(tmp_path / "deleted.mp3"), "as": "file"},
    )
    lines = preview_lines(record, color=True, width=80)
    assert not any("play" in line for line in lines)


def test_undecodable_audio_degrades_to_a_dim_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_ffmpeg(
        tmp_path,
        'import sys\nprint("Invalid data", file=sys.stderr)\nsys.exit(1)\n',
        monkeypatch,
    )
    record = _media_record("audio", "audio/mpeg", b"garbage")
    lines = preview_lines(record, color=True, width=80)
    assert len(lines) == 1
    assert "(no preview: ffmpeg couldn't decode this audio" in lines[0]


def test_missing_ffmpeg_degrades_to_a_dim_note(monkeypatch: pytest.MonkeyPatch) -> None:
    from smartpipe.core.errors import ItemError
    from smartpipe.parsing import extract

    def missing() -> str:
        raise ItemError("ffmpeg is unavailable — reinstall smartpipe")

    monkeypatch.setattr(extract, "ffmpeg_exe", missing)
    record = _media_record("audio", "audio/mpeg", b"whatever")
    lines = preview_lines(record, color=False, width=80)
    assert lines == ["  (no preview: ffmpeg is unavailable — reinstall smartpipe)"]


# --- video: the 3-frame strip -------------------------------------------------------


def test_video_strip_samples_10_50_90_and_matches_the_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_ffmpeg(tmp_path, _FULL_FAKE, monkeypatch)
    log = tmp_path / "calls.txt"
    monkeypatch.setenv("FAKE_FFMPEG_LOG", str(log))
    record = _media_record("video", "video/mp4", b"mp4-ish bytes")
    lines = preview_lines(record, color=True, width=80)
    offsets = [float(value) for value in log.read_text(encoding="utf-8").split()]
    assert offsets == [pytest.approx(8.3), pytest.approx(41.5), pytest.approx(74.7)]  # of 83 s
    assert 0.0 not in offsets  # NEVER the 0% frame — intros are black/logo frames
    _match_golden("video-strip", "\n".join(lines))


def test_video_with_an_on_disk_source_gets_the_play_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_ffmpeg(tmp_path, _FULL_FAKE, monkeypatch)
    clip = tmp_path / "demo.mp4"
    clip.write_bytes(b"mp4-ish bytes")
    record = _media_record(
        "video", "video/mp4", b"mp4-ish bytes", __source={"path": str(clip), "as": "file"}
    )
    lines = preview_lines(record, color=True, width=80)
    assert "▶ play (1:23, " in lines[-1]
    assert clip.resolve().as_uri() in lines[-1]


def test_video_with_no_extractable_frames_degrades_to_a_dim_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = (
        "import sys\n"
        'if "-frames:v" in sys.argv:\n'
        "    sys.exit(1)\n"
        'print("Duration: 00:01:23.00", file=sys.stderr)\n'
        "sys.exit(1)\n"
    )
    _fake_ffmpeg(tmp_path, body, monkeypatch)
    record = _media_record("video", "video/mp4", b"mp4-ish bytes")
    lines = preview_lines(record, color=False, width=80)
    assert lines == ["  (no preview: ffmpeg produced no frames from this video)"]


# --- the injectable hook ------------------------------------------------------------


def test_maybe_preview_is_none_when_off_or_colorless() -> None:
    assert maybe_preview(enabled=False, color=True, width=80) is None
    assert maybe_preview(enabled=True, color=False, width=80) is None  # NO_COLOR/pipes


def test_maybe_preview_binds_the_render_context() -> None:
    hook = maybe_preview(enabled=True, color=True, width=80)
    assert hook is not None
    assert hook({"text": "no media here"}) == []
