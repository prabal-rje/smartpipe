"""Full-stack ``filter`` tests: real CLI → real container → Ollama adapter, HTTP mocked."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    import respx

CHAT = "http://localhost:11434/api/chat"


@pytest.fixture(autouse=True)
def local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMPIPE_MODEL", "ollama/qwen3:8b")


def _verdict(match: bool) -> httpx.Response:
    body = '{"match": true}' if match else '{"match": false}'
    return httpx.Response(200, json={"message": {"content": body}})


def test_semantic_grep_pipes_to_wc(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    # keep the two lines that contain "error"; each judged by one model call
    respx_mock.post(CHAT).side_effect = [
        _verdict(True),
        _verdict(False),
        _verdict(True),
    ]
    code, out, _err = run_cli(
        ["filter", "indicates an error", "--concurrency", "1"],
        stdin="disk error\nall fine\nnetwork error\n",
    )
    assert code == 0
    assert out == "disk error\nnetwork error\n"


def test_not_inverts(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).side_effect = [_verdict(True), _verdict(False)]
    code, out, _err = run_cli(
        ["filter", "--not", "is spam", "--concurrency", "1"], stdin="buy now\nhi mom\n"
    )
    assert code == 0
    assert out == "hi mom\n"


def test_field_reference_on_plain_input_is_usage_error(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_verdict(True))
    code, _out, err = run_cli(["filter", "{priority} is wrong"], stdin="plain line\n")
    assert code == 64
    assert "isn't JSON" in err
    assert route.call_count == 0  # failed before any model call


def test_comma_braces_rejected(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(return_value=_verdict(True))
    code, _out, err = run_cli(["filter", "{a, b}"], stdin='{"a":1}\n')
    assert code == 64
    assert "only work in 'map'" in err


def test_zero_matches_is_exit_0(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(return_value=_verdict(False))
    code, out, _err = run_cli(["filter", "x"], stdin="a\nb\n")
    assert code == 0
    assert out == ""


def test_skipped_item_exits_1(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    # item 1 is judged (no match); item 2's verdict is unparseable twice → skip.
    # one judged + one skipped → PARTIAL (exit 1).
    unparseable = httpx.Response(200, json={"message": {"content": "hmm, maybe"}})
    respx_mock.post(CHAT).side_effect = [_verdict(False), unparseable, unparseable]
    code, out, err = run_cli(["filter", "x", "--concurrency", "1"], stdin="kept?\nbroken\n")
    assert code == 1
    assert out == ""
    assert "skipped: line 2" in err
