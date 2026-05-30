"""Regression tests for pdf-stamp per-page selection (`_resolve_pages`).

The web UI replaced the all/first/last dropdown with per-page toggle chips,
sending the chosen 0-based indices as `pages_json`. The legacy `page_mode`
field is kept for the public REST API. `_resolve_pages` reconciles the two and
returns the 0-based indices to stamp (None = every page).
"""

from __future__ import annotations

from app.tools.pdf_stamp.router import _resolve_pages


def test_legacy_page_mode_still_works():
    assert _resolve_pages("all", None, 5) is None
    assert _resolve_pages("first", None, 5) == [0]
    assert _resolve_pages("last", None, 5) == [4]
    assert _resolve_pages("last", None, 1) == [0]


def test_explicit_pages_take_precedence():
    # Explicit selection overrides page_mode entirely.
    assert _resolve_pages("first", "[1,3]", 5) == [1, 3]
    assert _resolve_pages("all", "[2]", 5) == [2]


def test_all_pages_selected_collapses_to_none():
    # Selecting every page is equivalent to "all" → None (stamp_pdf stamps all).
    assert _resolve_pages("all", "[0,1,2,3,4]", 5) is None


def test_dedup_sort_and_drop_out_of_range():
    # Duplicates removed, sorted ascending, indices >= page count dropped
    # (so the same selection can apply to a shorter file in a batch).
    assert _resolve_pages("all", "[4,1,1,9,-1]", 5) == [1, 4]


def test_invalid_or_empty_falls_back_to_page_mode():
    assert _resolve_pages("first", "[]", 5) == [0]          # empty list
    assert _resolve_pages("all", "garbage", 5) is None      # not JSON
    assert _resolve_pages("all", "[7,8,9]", 5) is None      # all out of range
    assert _resolve_pages("last", '{"a":1}', 5) == [4]      # not a list
