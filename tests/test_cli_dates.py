"""Date types end to end (ledger item 56, the owner's condition): a fake-model
``extend`` with ``{due date}`` canonicalizes messy replies to ISO, and the
records it emits flow through ``where``, ``sort --by``, and ``summarize``'s
``bin()`` temporally. Real CLI entry point, real container, HTTP mocked."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    import respx

CHAT = "http://localhost:11434/api/chat"

# reply per row id: every date spelled differently — the coercion's job
_DUE_BY_ID = {
    "a": "Jan 5, 2026",  # month-name form
    "b": "01/05/2026",  # ambiguous slashed form — read month-first, disclosed
    "c": "15/01/2026",  # day > 12: unambiguous day-first
    "d": "2025-12-31T23:00:00Z",  # a datetime answering a date ask keeps its day
}


@pytest.fixture(autouse=True)
def local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.delenv("SMARTPIPE_OUTPUT", raising=False)


def _due_reply(request: httpx.Request) -> httpx.Response:
    prompt = json.dumps(json.loads(request.content).get("messages"))
    row_id = next(key for key in _DUE_BY_ID if f"row-{key}" in prompt)
    content = json.dumps({"due": _DUE_BY_ID[row_id]})
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def _extended(run_cli: RunCli) -> tuple[str, str]:
    stdin = "".join(f'{{"id": "row-{key}"}}\n' for key in _DUE_BY_ID)
    code, out, err = run_cli(["extend", "Add {due date}"], stdin=stdin)
    assert code == 0
    return out, err


def test_extend_canonicalizes_every_messy_date_to_iso(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(CHAT).mock(side_effect=_due_reply)
    out, err = _extended(run_cli)
    dues = {json.loads(line)["id"]: json.loads(line)["due"] for line in out.splitlines()}
    assert dues == {
        "row-a": "2026-01-05",
        "row-b": "2026-01-05",
        "row-c": "2026-01-15",
        "row-d": "2025-12-31",
    }
    assert "ambiguous date '01/05/2026' read month-first as 2026-01-05" in err


def test_where_compares_extracted_dates_temporally(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(CHAT).mock(side_effect=_due_reply)
    out, _err = _extended(run_cli)
    code, kept, _err = run_cli(["where", 'due >= "2026-01-01"'], stdin=out)
    assert code == 0
    assert [json.loads(line)["id"] for line in kept.splitlines()] == ["row-a", "row-b", "row-c"]
    code, exact, _err = run_cli(["where", 'due == "2026-01-05"'], stdin=out)
    assert code == 0
    assert [json.loads(line)["id"] for line in exact.splitlines()] == ["row-a", "row-b"]


def test_sort_orders_extracted_dates_temporally(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(CHAT).mock(side_effect=_due_reply)
    out, _err = _extended(run_cli)
    code, ordered, _err = run_cli(["sort", "--by", "due"], stdin=out)
    assert code == 0
    assert [json.loads(line)["id"] for line in ordered.splitlines()] == [
        "row-d",  # 2025-12-31 first
        "row-a",  # 2026-01-05, stable ahead of its tie
        "row-b",
        "row-c",
    ]


def test_summarize_bins_extracted_dates_by_day(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(CHAT).mock(side_effect=_due_reply)
    out, _err = _extended(run_cli)
    code, table, _err = run_cli(["summarize", "count() by bin(due, 1d)"], stdin=out)
    assert code == 0
    counts = {row["due_bin"]: row["count"] for row in map(json.loads, table.splitlines())}
    assert counts == {"2026-01-05": 2, "2026-01-15": 1, "2025-12-31": 1}


def test_datetime_braces_flow_through_where(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    content = json.dumps({"ts": "2026/01/15 9:00"})
    respx_mock.post(CHAT).mock(
        return_value=httpx.Response(
            200, json={"message": {"role": "assistant", "content": content}}
        )
    )
    code, out, _err = run_cli(["extend", "Add {ts datetime}"], stdin='{"id": 1}\n')
    assert code == 0
    assert json.loads(out)["ts"] == "2026-01-15T09:00:00"
    # a bare date on the right promotes to midnight — the datetime row matches
    code, kept, _err = run_cli(["where", 'ts >= "2026-01-15"'], stdin=out)
    assert code == 0
    assert kept == out
