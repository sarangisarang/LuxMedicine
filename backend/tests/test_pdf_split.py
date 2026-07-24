"""Splitting an oversized PDF into page-bounded parts.

Built on PDFs made in memory (blank pages), so this is a pure test of the cut: how many parts,
which pages each covers, and that nothing is dropped or duplicated across the seam. The naming
convention ("(part 01)") is pinned because the corpus already has hand-split documents that follow
it, and a re-ingest must not produce a second, differently-named set beside them.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter

from app.services.pdf_split import DEFAULT_MAX_PAGES, page_count, split_if_large


def _pdf(n_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_page_count_reads_the_document():
    assert page_count(_pdf(7)) == 7


def test_a_small_pdf_is_left_whole_and_byte_identical():
    original = _pdf(30)
    parts = split_if_large(original, max_pages=120)
    assert len(parts) == 1
    part = parts[0]
    assert part.label_suffix == ""
    assert (part.page_start, part.page_end) == (1, 30)
    assert part.data == original, "an unsplit doc must hash/store exactly like the single-file path"


def test_exactly_at_the_limit_is_not_split():
    parts = split_if_large(_pdf(120), max_pages=120)
    assert len(parts) == 1 and parts[0].label_suffix == ""


def test_one_over_the_limit_splits_into_two():
    parts = split_if_large(_pdf(121), max_pages=120)
    assert [p.label_suffix for p in parts] == [" (part 01)", " (part 02)"]
    assert [(p.page_start, p.page_end) for p in parts] == [(1, 120), (121, 121)]
    assert [page_count(p.data) for p in parts] == [120, 1]


def test_a_large_pdf_is_cut_into_contiguous_parts_covering_every_page():
    total = 250
    parts = split_if_large(_pdf(total), max_pages=120)

    assert len(parts) == 3
    # Contiguous, gapless, in order — the seam neither drops nor duplicates a page.
    assert [(p.page_start, p.page_end) for p in parts] == [(1, 120), (121, 240), (241, 250)]
    # And the actual written PDFs have those page counts, not just the labels.
    assert [page_count(p.data) for p in parts] == [120, 120, 10]
    assert sum(page_count(p.data) for p in parts) == total


def test_max_pages_is_overridable():
    parts = split_if_large(_pdf(120), max_pages=50)
    assert [page_count(p.data) for p in parts] == [50, 50, 20]


def test_part_numbers_are_zero_padded_to_at_least_two_digits():
    # 25 pages at 2/part => 13 parts, so "(part 01)" … "(part 13)".
    parts = split_if_large(_pdf(25), max_pages=2)
    assert parts[0].label_suffix == " (part 01)"
    assert parts[-1].label_suffix == " (part 13)"


def test_default_limit_is_120():
    assert DEFAULT_MAX_PAGES == 120
    assert len(split_if_large(_pdf(120))) == 1
    assert len(split_if_large(_pdf(121))) == 2


def test_max_pages_below_one_is_rejected():
    with pytest.raises(ValueError):
        split_if_large(_pdf(3), max_pages=0)
