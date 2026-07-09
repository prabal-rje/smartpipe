"""CLI surface of ``smartpipe graph`` — registration, refusals, the epilog pin."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.conftest import RunCli


def test_graph_help_carries_the_preview_escape_hatch(run_cli: RunCli) -> None:
    code, out, _ = run_cli(["graph", "--help"])
    assert code == 0
    assert "sample 200" in out  # the weak-machine composition line (owner ruling)
    assert "--entities" in out
    assert "--window" in out


def test_graph_without_fast_is_a_usage_fault(run_cli: RunCli) -> None:
    code, out, err = run_cli(["graph"], stdin="hello\n")
    assert code == 64
    assert out == ""
    assert "--fast" in err


def test_graph_fast_on_empty_stdin_is_ok_and_silent(run_cli: RunCli) -> None:
    code, out, _ = run_cli(["graph", "--fast"], stdin="")
    assert code == 0
    assert out == ""


def test_graph_fast_refuses_a_bad_save_extension_before_reading(run_cli: RunCli) -> None:
    code, out, err = run_cli(["graph", "--fast", "--save", "graph.xlsx"], stdin="hello\n")
    assert code == 64
    assert out == ""
    assert ".graphml" in err
