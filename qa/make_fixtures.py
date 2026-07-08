"""Regenerate the manual-QA fixtures (deterministic — seed 7, stdlib only).

Run from the repo root:  uv run python qa/make_fixtures.py
The outputs are committed; rerunning must be a no-op diff.
"""

from __future__ import annotations

import base64
import json
import random
from pathlib import Path

__all__ = ["main"]

FIXTURES = Path(__file__).parent / "fixtures"

REGIONS = ("EU", "US", "APAC")
CUSTOMERS = ("acme", "globex", "initech", "umbrella", "stark", "wayne")

TICKET_THEMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "checkout",
        (
            "payment fails on my iphone every time",
            "cart crashes when I hit pay",
            "checkout button does nothing on safari",
            "card declined but I was still charged",
            "the pay screen freezes at the spinner",
        ),
    ),
    (
        "auth",
        (
            "password reset email never arrives",
            "two factor code always says expired",
            "logged out every five minutes",
            "sso login loops back to the start",
        ),
    ),
    (
        "praise",
        (
            "love the new dark mode!!",
            "dark theme is beautiful",
            "the redesign is so much faster",
            "support was quick and kind",
        ),
    ),
    (
        "requests",
        (
            "please add csv export",
            "need a bulk delete option",
            "would pay extra for an api",
            "add a keyboard shortcut for search",
        ),
    ),
)

LOG_ERRORS = (
    "timeout calling payments-v2 (504)",
    "upstream payments-v2 gateway timeout",
    "payments-v2 connection pool exhausted",
    "db replica lag exceeded 30s",
    "cache miss storm on session store",
)
LOG_NOISE = (
    "GET /health 200 2ms",
    "GET /api/users 200 41ms",
    "POST /api/orders 201 88ms",
    "retry scheduled for job 4411",
    "cron sweep finished in 1.2s",
)

PRODUCTS = (
    "Espresso One machine",
    "LaserJet 9 printer",
    "Standing desk, oak",
    "Aeron chair size B",
    "UltraWide 34 monitor",
    "Mechanical keyboard TKL",
    "Noise cancelling headset",
    "4TB backup drive",
    "Conference speakerphone",
    "Label maker pro",
)


def _tickets(rng: random.Random) -> list[str]:
    rows: list[str] = []
    identifier = 800
    for _ in range(110):
        _, bodies = TICKET_THEMES[rng.randrange(len(TICKET_THEMES))]
        body = bodies[rng.randrange(len(bodies))]
        identifier += 1
        record: dict[str, object] = {
            "id": identifier,
            "customer": CUSTOMERS[rng.randrange(len(CUSTOMERS))],
            "body": body,
            "region": REGIONS[rng.randrange(len(REGIONS))],
            "ts": f"2026-07-01T{9 + rng.randrange(9):02d}:{rng.randrange(60):02d}:00Z",
            "total": round(rng.uniform(5, 400), 2),
        }
        if rng.random() < 0.12:
            del record["region"]  # coverage gaps are part of the test
        if rng.random() < 0.06:
            record["total"] = "n/a"  # mixed types are part of the test
        rows.append(json.dumps(record, ensure_ascii=False))
    # exact duplicates (distinct's free fold)
    rows.extend(rows[3] for _ in range(4))
    # the planted outlier
    rows.append(
        json.dumps(
            {
                "id": 9999,
                "customer": "n/a",
                "body": "kernel: watchdog: BUG: soft lockup CPU#3 stuck for 22s",
                "region": "EU",
                "ts": "2026-07-01T13:37:00Z",
                "total": 0,
            }
        )
    )
    rng.shuffle(rows)
    return rows


def _feedback(rng: random.Random) -> list[str]:
    lines: list[str] = []
    for _, bodies in TICKET_THEMES:
        for body in bodies:
            lines.append(body)
            for _ in range(rng.randrange(2, 5)):
                lines.append(rng.choice(bodies))
    rng.shuffle(lines)
    return lines[:90]


def _logs(rng: random.Random) -> list[str]:
    rows: list[str] = []
    for _ in range(150):
        hour = 13 + rng.randrange(4)
        minute = rng.randrange(60)
        error_hour = hour == 15  # the incident hour — --by-time should show the spike
        is_error = rng.random() < (0.55 if error_hour else 0.12)
        message = rng.choice(LOG_ERRORS if is_error else LOG_NOISE)
        rows.append(
            json.dumps(
                {
                    "level": "error" if is_error else "info",
                    "ts": f"2026-07-01T{hour:02d}:{minute:02d}:00Z",
                    "msg": message,
                }
            )
        )
    return rows


def _orders_and_invoices(rng: random.Random) -> tuple[list[str], list[str]]:
    orders: list[str] = []
    invoices: list[str] = []
    for number, product in enumerate(PRODUCTS * 4, start=1):
        sku = f"SKU-{number:03d}"  # shared key: join --on finds the same gaps for free
        orders.append(json.dumps({"id": 4000 + number, "sku": sku, "desc": product}))
        if number % 7 != 0:  # every 7th order has NO invoice — anti-join finds them
            invoices.append(json.dumps({"invoice": f"A-{number}", "sku": sku, "item": product}))
    rng.shuffle(orders)
    return orders, invoices


REPORT_SENTENCES = (
    "Quarterly throughput held steady across all three fulfilment hubs.",
    "The Frankfurt hub cleared its seasonal backlog two weeks ahead of forecast.",
    "Carrier renegotiations trimmed line-haul rates on the northern corridor.",
    "Warehouse cycle counts matched the ledger within a tenth of a percent.",
    "The night shift pilot reduced dock-to-stock time by a third.",
    "Returns processing remains the slowest station in the network.",
    "Packaging waste fell again after the right-sizing project landed.",
    "Driver retention improved once the new rota software rolled out.",
    "The Lyon cross-dock absorbed the overflow without added headcount.",
    "Forecast accuracy for bulky goods still trails the network average.",
)


def _big_report(rng: random.Random) -> str:
    """A deliberately HUGE document (~0.5 MB ≈ 125k estimated tokens — past
    every wired provider's table budget) so the QA oversize flow can watch
    map's auto-chunk disclosure fire. The headline finding is planted in the
    closing paragraph, where only a whole-document pass would see it."""
    paragraphs = [
        "ACME LOGISTICS - ANNUAL OPERATIONS REVIEW "
        "(QA fixture: deliberately oversized; see qa/README.md section 12)"
    ]
    total = 0
    while total < 500_000:
        sentences = [rng.choice(REPORT_SENTENCES) for _ in range(rng.randrange(4, 9))]
        paragraph = " ".join(sentences)
        paragraphs.append(paragraph)
        total += len(paragraph) + 2
    paragraphs.append(
        "Headline finding: unit costs fell 23 percent after the Rotterdam "
        "automation pilot, and the board approved a fleet-wide rollout for "
        "next fiscal year."
    )
    return "\n\n".join(paragraphs) + "\n"


def _media(assets: Path) -> list[str]:
    wav = base64.b64encode((assets / "probe.wav").read_bytes()).decode()
    png = base64.b64encode((assets / "probe.png").read_bytes()).decode()
    return [
        json.dumps(
            {
                "__media": {"kind": "audio", "mime": "audio/wav", "data_b64": wav},
                "__source": {"path": "probe.wav", "as": "file"},
            }
        ),
        json.dumps(
            {
                "__media": {"kind": "image", "mime": "image/png", "data_b64": png},
                "__source": {"path": "probe.png", "as": "file"},
            }
        ),
        json.dumps({"text": "a plain text row mixed in with the media"}),
    ]


def main() -> None:
    rng = random.Random(7)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "tickets.jsonl").write_text("\n".join(_tickets(rng)) + "\n", encoding="utf-8")
    (FIXTURES / "feedback.txt").write_text("\n".join(_feedback(rng)) + "\n", encoding="utf-8")
    (FIXTURES / "logs.jsonl").write_text("\n".join(_logs(rng)) + "\n", encoding="utf-8")
    orders, invoices = _orders_and_invoices(rng)
    (FIXTURES / "orders.jsonl").write_text("\n".join(orders) + "\n", encoding="utf-8")
    (FIXTURES / "invoices.jsonl").write_text("\n".join(invoices) + "\n", encoding="utf-8")
    # NB: new rng-consuming fixtures append AFTER the existing ones — the shared
    # rng sequence is what keeps regeneration a no-op diff for the older files
    (FIXTURES / "big_report.txt").write_text(_big_report(rng), encoding="utf-8")
    assets = Path(__file__).parent.parent / "src" / "smartpipe" / "assets"
    (FIXTURES / "media.jsonl").write_text("\n".join(_media(assets)) + "\n", encoding="utf-8")
    triage = FIXTURES / "triage.sem"
    triage.write_text(
        "[stage.hot]\n"
        'verb = "where"\n'
        "predicate = 'level == \"error\"'\n"
        "\n"
        "[stage.count]\n"
        'verb = "summarize"\n'
        'expression = "count() by bin(ts, 1h)"\n',
        encoding="utf-8",
    )
    for path in sorted(FIXTURES.iterdir()):
        print(f"{path.name}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
