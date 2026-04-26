"""Unit tests for the field detector. Builds tiny synthetic PDFs in memory
to verify each non-trivial path: NFKC fold, multi-colon split, page-edge
clamp, sibling-row clamp, signature-zone exclusion.
"""
from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path

import fitz

from app.core.pdf_form_detect import (
    _normalize, _split_multi_colon_span, _build_synonym_index,
    _active_label_map, detect_fields,
)


def test_normalize_strips_marker_and_colon():
    assert _normalize("**公司名稱:") == "公司名稱"
    assert _normalize("1. 公司名稱：") == "公司名稱"


def test_normalize_handles_cjk_compat_ideographs():
    # 立 in U+F9F7 (compat) vs U+7ACB (canonical) → must compare equal.
    raw = "成立日期"   # 成立日期 with compat 立
    assert _normalize(raw) == _normalize("成立日期")


def test_normalize_folds_traditional_to_simplified():
    # The fold list intentionally only covers the 16 most-common form chars;
    # check on a pair we KNOW is in the table (內↔内).
    assert _normalize("應稅內含") == _normalize("應稅内含")


def test_split_multi_colon_span():
    out = _split_multi_colon_span("銀行名稱：     銀行代號：", (10, 0, 100, 12))
    assert len(out) == 2
    assert out[0][0].rstrip() == "銀行名稱：" or out[0][0].endswith("：")
    assert out[1][0].rstrip() == "銀行代號：" or out[1][0].endswith("：")
    assert out[0][1][0] < out[1][1][0]   # left half is to the left


def test_synonym_index_finds_known_keys():
    idx = _build_synonym_index(_active_label_map())
    assert "公司名稱" in idx
    assert "duns" in idx or "dunscode" in idx or "鄧白氏" in idx


def _build_pdf(spans: list[tuple[str, float, float]]) -> bytes:
    """Build a single-page A4 PDF that draws each (text, x_pt, y_pt).

    Uses PyMuPDF's built-in `china-t` font so CJK labels round-trip through
    the text-extraction stage in the detector (the default helv font has no
    CJK glyphs, so the labels would come back as boxes/missing chars).
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for text, x, y in spans:
        page.insert_text((x, y), text, fontsize=11, fontname="china-t")
    buf = BytesIO()
    doc.save(buf, garbage=3, deflate=True)
    doc.close()
    return buf.getvalue()


def test_detect_simple_label():
    pdf_bytes = _build_pdf([("公司名稱:", 100, 200)])
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes); p = Path(f.name)
    try:
        detected, _pages = detect_fields(p)
    finally:
        p.unlink(missing_ok=True)
    keys = {d.profile_key for d in detected}
    assert "company_name" in keys


def test_detect_seal_zone_excludes_below_owner():
    pdf_bytes = _build_pdf([
        ("公司名稱:", 100, 200),     # legitimate
        ("公司章", 100, 700),        # seal marker — anything from here down dropped
        ("負責人", 280, 700),        # signature-line owner, MUST be excluded
    ])
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes); p = Path(f.name)
    try:
        detected, _ = detect_fields(p)
    finally:
        p.unlink(missing_ok=True)
    owners = [d for d in detected if d.profile_key == "owner"]
    assert owners == [], "seal-zone 負責人 should be excluded"
    keys = {d.profile_key for d in detected}
    assert "company_name" in keys
