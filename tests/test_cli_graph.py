"""CLI surface of ``smartpipe graph`` — registration, refusals, the epilog pin."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from smartpipe.verbs.graph import GraphRequest
    from tests.conftest import RunCli


def test_graph_help_carries_the_preview_escape_hatch(run_cli: RunCli) -> None:
    code, out, _ = run_cli(["graph", "--help"])
    assert code == 0
    assert "sample 200" in out  # the weak-machine composition line (owner ruling)
    assert "--entities" in out
    assert "--window" in out


def test_graph_help_carries_the_paid_half(run_cli: RunCli) -> None:
    code, out, _ = run_cli(["graph", "--help"])
    assert code == 0
    assert "--name-top" in out
    assert "--relations" in out
    assert "--max-calls" in out
    assert "focus prompt" in out


def test_graph_without_fast_is_a_usage_fault(run_cli: RunCli) -> None:
    code, out, err = run_cli(["graph"], stdin="hello\n")
    assert code == 64
    assert out == ""
    assert "--fast" in err
    assert "focus prompt" in err
    assert "edge records on stdin" in err


def test_graph_adopts_edge_records_from_stdin(run_cli: RunCli) -> None:
    code, out, err = run_cli(["graph"], stdin='{"source": "Ann", "target": "Bob", "weight": 2}\n')
    assert code == 0
    assert '"relation":"co-occurs"' in out
    assert "0 tok" in err  # adoption spends nothing


def test_graph_fast_on_empty_stdin_is_ok_and_silent(run_cli: RunCli) -> None:
    code, out, _ = run_cli(["graph", "--fast"], stdin="")
    assert code == 0
    assert out == ""


def test_graph_fast_refuses_a_bad_save_extension_before_reading(run_cli: RunCli) -> None:
    code, out, err = run_cli(["graph", "--fast", "--save", "graph.xlsx"], stdin="hello\n")
    assert code == 64
    assert out == ""
    assert ".graphml" in err


def test_graph_help_carries_the_embed_model_flag(run_cli: RunCli) -> None:
    code, out, _ = run_cli(["graph", "--help"])
    assert code == 0
    assert "--embed-model" in out
    assert "fold" in out  # the canonicalization fold it feeds


def test_embed_model_flag_flows_from_the_cli_into_the_request(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--embed-model reaches GraphRequest.embed_model_flag, which fold_vectors threads
    to the fold embedder (specified wins, local fallback)."""
    from smartpipe.cli import graph_cmd
    from smartpipe.core.errors import ExitCode

    captured: list[GraphRequest] = []

    async def fake_run_graph(request: GraphRequest, *args: object, **kwargs: object) -> ExitCode:
        captured.append(request)
        return ExitCode.OK

    monkeypatch.setattr(graph_cmd, "run_graph", fake_run_graph)
    code, _out, _err = run_cli(
        ["graph", "--fast", "--embed-model", "openai/text-embedding-3-small"],
        stdin="Ann met Bob\n",
    )
    assert code == 0
    assert len(captured) == 1
    assert captured[0].embed_model_flag == "openai/text-embedding-3-small"
