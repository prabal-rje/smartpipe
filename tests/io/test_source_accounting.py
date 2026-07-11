"""Run-scoped source accounting stays independent of emitted work units."""

from __future__ import annotations

from smartpipe.core.errors import SourceCounts
from smartpipe.io import source_accounting
from smartpipe.io.items import ItemSource


def test_dropped_ingestion_sources_merge_once_with_local_outcomes() -> None:
    source_accounting.reset()
    source_accounting.record_local(SourceCounts(succeeded=2, skipped=0, failed=0))
    source_accounting.record_ingestion_skip(failed=True)

    assert source_accounting.settle() == SourceCounts(succeeded=2, skipped=1, failed=1)
    assert source_accounting.settle() is None


def test_grouped_ocr_pages_count_as_one_successful_source() -> None:
    source_accounting.reset()
    group = source_accounting.new_group(size=3)
    counter = source_accounting.SourceCounter()

    for page in range(3):
        counter.done(ItemSource("file", "report.pdf", page, "pages", group=group))

    assert counter.counts == SourceCounts(succeeded=1, skipped=0, failed=0)


def test_grouped_source_is_skipped_once_when_one_page_fails() -> None:
    source_accounting.reset()
    group = source_accounting.new_group(size=3)
    counter = source_accounting.SourceCounter()
    counter.done(ItemSource("file", "report.pdf", 0, "pages", group=group))
    counter.skip(ItemSource("file", "report.pdf", 1, "pages", group=group), failed=True)
    counter.done(ItemSource("file", "report.pdf", 2, "pages", group=group))

    assert counter.counts == SourceCounts(succeeded=0, skipped=1, failed=1)


def test_partially_consumed_group_is_an_unsent_source_skip() -> None:
    source_accounting.reset()
    group = source_accounting.new_group(size=2)
    counter = source_accounting.SourceCounter()
    counter.done(ItemSource("file", "report.pdf", 0, "pages", group=group))

    assert counter.counts == SourceCounts(succeeded=0, skipped=1, failed=0)
