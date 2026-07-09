"""Time parsing + bucketing (D38/13): fenced formats, UTC labels."""

from __future__ import annotations

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.engine.timebin import bucket_label, parse_bucket, parse_timestamp


def test_epoch_seconds_and_milliseconds() -> None:
    assert parse_timestamp(1_735_689_600) == 1_735_689_600.0
    assert parse_timestamp(1_735_689_600_000) == 1_735_689_600.0  # ms auto-detected


def test_iso_with_z_offset_and_naive() -> None:
    assert parse_timestamp("2025-01-01T00:00:00Z") == 1_735_689_600.0
    assert parse_timestamp("2025-01-01T01:00:00+01:00") == 1_735_689_600.0
    assert parse_timestamp("2025-01-01T00:00:00") == 1_735_689_600.0  # naive = UTC


def test_garbage_is_none_not_a_crash() -> None:
    assert parse_timestamp("yesterday-ish") is None
    assert parse_timestamp(True) is None
    assert parse_timestamp(None) is None


def test_bucket_menu() -> None:
    assert parse_bucket("1h") == 3600
    with pytest.raises(UsageFault, match="Buckets"):
        parse_bucket("2h")


def test_labels_by_granularity() -> None:
    noon_ish = 1_735_735_500.0  # 2025-01-01 12:45 UTC
    assert bucket_label(noon_ish, 3600) == "12:00"
    assert bucket_label(noon_ish, 900) == "12:45"
    assert bucket_label(noon_ish, 86_400) == "2025-01-01"


def test_date_only_values_bin_as_their_midnight() -> None:
    # item 56: a calendar day is a valid timestamp — its UTC midnight
    assert parse_timestamp("2025-01-01") == 1_735_689_600.0
    assert parse_timestamp(" 2025-01-01 ") == 1_735_689_600.0
    epoch = parse_timestamp("2025-01-01")
    assert epoch is not None
    assert bucket_label(epoch, 86_400) == "2025-01-01"
    assert bucket_label(epoch, 3600) == "00:00"
