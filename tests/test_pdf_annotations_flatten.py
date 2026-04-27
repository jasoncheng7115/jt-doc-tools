"""Tests for the pdf-annotations-flatten tool."""
from __future__ import annotations

import io

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app


def _make_pdf_with_annots() -> bytes:
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 80), "First page text.", fontsize=12, fontname="helv")
    a = p.add_text_annot((40, 70), "review")
    a.set_info(content="review", title="Jason"); a.update()
    h = p.add_highlight_annot(fitz.Rect(50, 70, 200, 90))
    h.set_info(content="", title="Jason"); h.update()
    buf = io.BytesIO()
    doc.save(buf); doc.close()
    return buf.getvalue()


def _make_blank_pdf() -> bytes:
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    buf = io.BytesIO()
    doc.save(buf); doc.close()
    return buf.getvalue()


def _count_annots(pdf_bytes: bytes) -> int:
    n = 0
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for p in doc:
            n += sum(1 for _ in (p.annots() or []))
    return n


@pytest.fixture
def client():
    return TestClient(app)


def test_index_renders(client):
    r = client.get("/tools/pdf-annotations-flatten/")
    assert r.status_code == 200
    assert "註解固定化" in r.text


def test_analyze_returns_total_and_widget_flag(client):
    r = client.post(
        "/tools/pdf-annotations-flatten/analyze",
        files={"file": ("doc.pdf", _make_pdf_with_annots(), "application/pdf")},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 2
    assert j["page_count"] == 1
    assert j["has_widgets"] is False


def test_flatten_bakes_all_annotations(client):
    pdf = _make_pdf_with_annots()
    assert _count_annots(pdf) == 2
    r = client.post(
        "/tools/pdf-annotations-flatten/flatten",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200
    assert r.headers.get("x-annotations-baked") == "2"
    # All annotation objects are gone after baking — visuals are now in
    # the page content stream.
    assert _count_annots(r.content) == 0


def test_flatten_blank_pdf_succeeds_with_zero(client):
    r = client.post(
        "/tools/pdf-annotations-flatten/flatten",
        files={"file": ("blank.pdf", _make_blank_pdf(), "application/pdf")},
    )
    assert r.status_code == 200
    assert r.headers.get("x-annotations-baked") == "0"
    assert _count_annots(r.content) == 0


def test_flatten_rejects_non_pdf(client):
    r = client.post(
        "/tools/pdf-annotations-flatten/flatten",
        files={"file": ("note.txt", b"hi", "text/plain")},
    )
    assert r.status_code == 400


def test_flatten_filename_handles_cjk(client):
    pdf = _make_pdf_with_annots()
    r = client.post(
        "/tools/pdf-annotations-flatten/flatten",
        files={"file": ("中文.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "filename*=" in cd or "filename=" in cd


def test_api_alias_works(client):
    pdf = _make_pdf_with_annots()
    r = client.post(
        "/tools/pdf-annotations-flatten/api/pdf-annotations-flatten",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200
    assert _count_annots(r.content) == 0
