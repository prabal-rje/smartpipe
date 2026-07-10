"""``smartpipe demo`` - the playground fetcher: zero model calls, zero config.

Contract under test: refuses politely when ./smartpipe-playground is in the
way, recognizes a complete prior download (already here, exit 0), asks before
downloading at a TTY (Enter continues) while non-TTY runs proceed, verifies
the release sha256 before unpacking, and prints the next-steps block - the
command's one stdout result. No test touches the network: the transport is
injected (run_demo) or monkeypatched at the module boundary (the CLI shell),
and the wire itself is pinned with respx.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from smartpipe.cli import screens
from smartpipe.cli.demo_cmd import run_demo
from smartpipe.core.errors import SetupFault
from smartpipe.io import playground
from tests.conftest import RunCli

if TYPE_CHECKING:
    from collections.abc import Callable

# --- the fake corpus ---------------------------------------------------------------


def _fake_corpus_tar(*, entries: tuple[str, ...] | None = None) -> bytes:
    """A tiny tarball with the real corpus's shape (six content dirs + a file)."""
    names = entries if entries is not None else tuple(sorted(playground.EXPECTED_ENTRIES))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for entry in names:
            info = tarfile.TarInfo(f"{playground.PLAYGROUND_DIR}/{entry}")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        payload = b'{"region": "EU", "total": 284.0}\n'
        member = tarfile.TarInfo(f"{playground.PLAYGROUND_DIR}/data/tickets.jsonl")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plant_complete_corpus(target: Path) -> None:
    for entry in playground.EXPECTED_ENTRIES:
        (target / entry).mkdir(parents=True)


class _Recorder:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.told: list[str] = []
        self.asked: list[str] = []
        self.fetched = 0

    def say(self, message: str) -> None:
        self.said.append(message)

    def tell(self, message: str) -> None:
        self.told.append(message)

    def confirm(self, question: str, *, answer: bool) -> bool:
        self.asked.append(question)
        return answer

    def fetch(self, data: bytes) -> Callable[[], bytes]:
        def _fetch() -> bytes:
            self.fetched += 1
            return data

        return _fetch

    def never_fetch(self) -> bytes:
        raise AssertionError("this path must not download anything")


# --- the pure facts ----------------------------------------------------------------


def test_the_pinned_url_is_the_one_the_docs_advertise() -> None:
    docs = Path(__file__).parents[1] / "docs" / "index.md"
    assert playground.PLAYGROUND_URL in docs.read_text(encoding="utf-8")


def test_looks_complete_wants_all_six_content_dirs() -> None:
    assert playground.looks_complete(playground.EXPECTED_ENTRIES)
    assert playground.looks_complete([*playground.EXPECTED_ENTRIES, "README.md", "LICENSES.md"])
    assert not playground.looks_complete(["data", "invoices"])
    assert not playground.looks_complete([])


def test_next_steps_block_is_copy_pasteable_cookbook_commands() -> None:
    for line in (
        "cd smartpipe-playground",
        "smartpipe 'reports/*.pdf'",
        "smartpipe summarize 'count(), avg(total) by region' < data/tickets.jsonl",
        "smartpipe graph --fast 'reports/*.pdf' 'recordings/*.mp3' data/feedback.txt",
    ):
        assert line in screens.DEMO_READY
        assert line in screens.DEMO_ALREADY_HERE
    assert "already here" in screens.DEMO_ALREADY_HERE
    assert "nothing downloaded" in screens.DEMO_ALREADY_HERE


def test_verify_accepts_the_matching_digest_and_refuses_the_wrong_one() -> None:
    data = b"playground bytes"
    playground.verify(data, expected_sha256=_sha256(data))
    with pytest.raises(SetupFault) as caught:
        playground.verify(data, expected_sha256=_sha256(b"other bytes"))
    message = str(caught.value)
    assert _sha256(data) in message
    assert _sha256(b"other bytes") in message


# --- unpack (real tarfile, no network) ----------------------------------------------


def test_unpack_publishes_the_corpus_and_cleans_its_staging(tmp_path: Path) -> None:
    target = tmp_path / playground.PLAYGROUND_DIR
    playground.unpack(_fake_corpus_tar(), target)
    assert (target / "data" / "tickets.jsonl").read_bytes().startswith(b'{"region"')
    assert {entry.name for entry in target.iterdir()} >= set(playground.EXPECTED_ENTRIES)
    assert [entry.name for entry in tmp_path.iterdir()] == [playground.PLAYGROUND_DIR]


def test_unpack_replaces_an_existing_empty_target_dir(tmp_path: Path) -> None:
    target = tmp_path / playground.PLAYGROUND_DIR
    target.mkdir()
    playground.unpack(_fake_corpus_tar(), target)
    assert (target / "data" / "tickets.jsonl").exists()


def test_unpack_refuses_a_tarball_with_the_wrong_layout(tmp_path: Path) -> None:
    target = tmp_path / playground.PLAYGROUND_DIR
    with pytest.raises(SetupFault, match="layout"):
        playground.unpack(_fake_corpus_tar(entries=("data",)), target)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []  # staging never leaks


# --- run_demo (unit, injected effects) ----------------------------------------------


def test_complete_prior_download_is_already_here_and_never_fetches(tmp_path: Path) -> None:
    target = tmp_path / playground.PLAYGROUND_DIR
    _plant_complete_corpus(target)
    rec = _Recorder()
    run_demo(
        target=target,
        is_tty=True,
        confirm=lambda q: rec.confirm(q, answer=True),
        fetch=rec.never_fetch,
        expected_sha256="unused",
        say=rec.say,
        tell=rec.tell,
    )
    assert rec.asked == []  # nothing to confirm - nothing will download
    assert rec.said == [screens.DEMO_ALREADY_HERE]


def test_a_nonempty_stranger_directory_refuses_politely(tmp_path: Path) -> None:
    target = tmp_path / playground.PLAYGROUND_DIR
    target.mkdir()
    (target / "my-thesis.txt").write_text("mine", encoding="utf-8")
    rec = _Recorder()
    with pytest.raises(SetupFault, match="won't overwrite"):
        run_demo(
            target=target,
            is_tty=False,
            confirm=lambda q: rec.confirm(q, answer=True),
            fetch=rec.never_fetch,
            expected_sha256="unused",
            say=rec.say,
            tell=rec.tell,
        )
    assert (target / "my-thesis.txt").read_text(encoding="utf-8") == "mine"


def test_a_file_squatting_on_the_target_name_refuses(tmp_path: Path) -> None:
    target = tmp_path / playground.PLAYGROUND_DIR
    target.write_text("not a directory", encoding="utf-8")
    rec = _Recorder()
    with pytest.raises(SetupFault, match="won't overwrite"):
        run_demo(
            target=target,
            is_tty=False,
            confirm=lambda q: rec.confirm(q, answer=True),
            fetch=rec.never_fetch,
            expected_sha256="unused",
            say=rec.say,
            tell=rec.tell,
        )


def test_declining_at_the_tty_downloads_nothing(tmp_path: Path) -> None:
    target = tmp_path / playground.PLAYGROUND_DIR
    rec = _Recorder()
    run_demo(
        target=target,
        is_tty=True,
        confirm=lambda q: rec.confirm(q, answer=False),
        fetch=rec.never_fetch,
        expected_sha256="unused",
        say=rec.say,
        tell=rec.tell,
    )
    assert rec.asked == [screens.DEMO_CONFIRM]
    assert not target.exists()
    assert rec.said == []  # no result produced - stdout stays silent
    assert any("nothing downloaded" in line for line in rec.told)


def test_non_tty_proceeds_without_a_prompt(tmp_path: Path) -> None:
    target = tmp_path / playground.PLAYGROUND_DIR
    data = _fake_corpus_tar()
    rec = _Recorder()
    run_demo(
        target=target,
        is_tty=False,
        confirm=lambda q: rec.confirm(q, answer=False),  # would decline if asked
        fetch=rec.fetch(data),
        expected_sha256=_sha256(data),
        say=rec.say,
        tell=rec.tell,
    )
    assert rec.asked == []
    assert rec.fetched == 1
    assert (target / "data" / "tickets.jsonl").exists()
    assert rec.said == [screens.DEMO_READY]


def test_a_corrupt_download_unpacks_nothing(tmp_path: Path) -> None:
    target = tmp_path / playground.PLAYGROUND_DIR
    data = _fake_corpus_tar()
    rec = _Recorder()
    with pytest.raises(SetupFault, match="checksum"):
        run_demo(
            target=target,
            is_tty=False,
            confirm=lambda q: rec.confirm(q, answer=True),
            fetch=rec.fetch(data + b"tampered"),
            expected_sha256=_sha256(data),
            say=rec.say,
            tell=rec.tell,
        )
    assert not target.exists()


# --- the CLI shell -------------------------------------------------------------------


@pytest.fixture
def demo_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "cwd"
    home.mkdir()
    monkeypatch.chdir(home)
    return home


def _wire_fake_corpus(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    monkeypatch.setattr("smartpipe.cli.demo_cmd.fetch_playground", lambda: data)
    monkeypatch.setattr("smartpipe.io.playground.PLAYGROUND_SHA256", _sha256(data))


def test_cli_demo_downloads_unpacks_and_prints_next_steps(
    run_cli: RunCli, demo_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_fake_corpus(monkeypatch, _fake_corpus_tar())
    monkeypatch.setattr("smartpipe.cli.demo_cmd.stdin_is_tty", lambda: False)
    code, out, err = run_cli(["demo"])
    assert code == 0
    assert out == screens.DEMO_READY + "\n"  # stdout is the result - nothing else
    assert "downloading" in err
    assert "sha256 verified" in err
    assert (demo_cwd / playground.PLAYGROUND_DIR / "data" / "tickets.jsonl").exists()


def test_cli_demo_is_idempotent_the_second_run_says_already_here(
    run_cli: RunCli, demo_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _plant_complete_corpus(demo_cwd / playground.PLAYGROUND_DIR)
    monkeypatch.setattr(
        "smartpipe.cli.demo_cmd.fetch_playground",
        lambda: (_ for _ in ()).throw(AssertionError("must not download")),
    )
    monkeypatch.setattr("smartpipe.cli.demo_cmd.stdin_is_tty", lambda: True)
    code, out, _err = run_cli(["demo"])
    assert code == 0
    assert out == screens.DEMO_ALREADY_HERE + "\n"


def test_cli_demo_refuses_a_stranger_directory_with_exit_two(
    run_cli: RunCli, demo_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = demo_cwd / playground.PLAYGROUND_DIR
    target.mkdir()
    (target / "keep.txt").write_text("mine", encoding="utf-8")
    monkeypatch.setattr("smartpipe.cli.demo_cmd.stdin_is_tty", lambda: False)
    code, out, err = run_cli(["demo"])
    assert code == 2
    assert out == ""
    assert "won't overwrite" in err


def test_cli_demo_tty_prompt_default_is_yes(
    run_cli: RunCli, demo_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_fake_corpus(monkeypatch, _fake_corpus_tar())
    monkeypatch.setattr("smartpipe.cli.demo_cmd.stdin_is_tty", lambda: True)
    code, out, err = run_cli(["demo"], stdin="\n")  # bare Enter accepts
    assert code == 0
    assert out == screens.DEMO_READY + "\n"
    assert "continue?" in err  # the prompt rides stderr - stdout stays sacred


def test_cli_demo_tty_decline_exits_zero_and_leaves_nothing(
    run_cli: RunCli, demo_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "smartpipe.cli.demo_cmd.fetch_playground",
        lambda: (_ for _ in ()).throw(AssertionError("declined - must not download")),
    )
    monkeypatch.setattr("smartpipe.cli.demo_cmd.stdin_is_tty", lambda: True)
    code, out, err = run_cli(["demo"], stdin="n\n")
    assert code == 0
    assert out == ""
    assert "nothing downloaded" in err
    assert not (demo_cwd / playground.PLAYGROUND_DIR).exists()


def test_cli_demo_help_shows_the_examples(run_cli: RunCli) -> None:
    code, out, _err = run_cli(["demo", "--help"])
    assert code == 0
    assert "Examples:" in out
    assert "smartpipe-playground" in out


# --- the real-process boundaries (no network, no TTY assumed) ------------------------


def test_stdin_is_tty_reads_this_process() -> None:
    from smartpipe.cli.demo_cmd import stdin_is_tty

    assert isinstance(stdin_is_tty(), bool)


@respx.mock
def test_fetch_playground_streams_the_pinned_url() -> None:
    from smartpipe.cli.demo_cmd import fetch_playground

    respx.get(playground.PLAYGROUND_URL).mock(
        return_value=httpx.Response(200, content=b"the asset bytes")
    )
    assert fetch_playground() == b"the asset bytes"


def test_cli_demo_eof_at_the_prompt_declines(
    run_cli: RunCli, demo_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("smartpipe.cli.demo_cmd.stdin_is_tty", lambda: True)
    code, out, err = run_cli(["demo"], stdin="")  # EOF -> click.Abort -> decline
    assert code == 0
    assert out == ""
    assert "nothing downloaded" in err
    assert not (demo_cwd / playground.PLAYGROUND_DIR).exists()


# --- the wire (respx pins the transport behavior, still no live network) -------------


@respx.mock
def test_fetch_corpus_streams_and_follows_redirects() -> None:
    data = _fake_corpus_tar()
    respx.get("https://example.test/asset.tar.gz").mock(
        return_value=httpx.Response(302, headers={"location": "https://cdn.test/asset.tar.gz"})
    )
    respx.get("https://cdn.test/asset.tar.gz").mock(return_value=httpx.Response(200, content=data))
    assert playground.fetch_corpus("https://example.test/asset.tar.gz") == data


@respx.mock
def test_fetch_corpus_wire_failure_is_a_setup_fault_naming_the_url() -> None:
    respx.get("https://example.test/asset.tar.gz").mock(
        return_value=httpx.Response(404, content=b"gone")
    )
    with pytest.raises(SetupFault) as caught:
        playground.fetch_corpus("https://example.test/asset.tar.gz")
    message = str(caught.value)
    assert "https://example.test/asset.tar.gz" in message
    assert "download failed" in message


@respx.mock
def test_fetch_corpus_connect_error_is_a_setup_fault() -> None:
    respx.get("https://example.test/asset.tar.gz").mock(
        side_effect=httpx.ConnectError("no route to host")
    )
    with pytest.raises(SetupFault, match="no route to host"):
        playground.fetch_corpus("https://example.test/asset.tar.gz")
