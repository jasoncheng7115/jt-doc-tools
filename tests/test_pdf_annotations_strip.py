"""Tests for the pdf-annotations-strip tool."""
from __future__ import annotations

import io

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app


def _make_pdf_with_annots() -> bytes:
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 80), "Page 1.", fontsize=12, fontname="helv")
    a = p1.add_text_annot((40, 70), "todo")
    a.set_info(content="todo", title="Jason"); a.update()
    h = p1.add_highlight_annot(fitz.Rect(50, 70, 250, 90))
    h.set_info(content="hi", title="Jason"); h.update()

    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 80), "Page 2.", fontsize=12, fontname="helv")
    a2 = p2.add_text_annot((40, 70), "law check")
    a2.set_info(content="law check", title="Mary"); a2.update()

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
    r = client.get("/tools/pdf-annotations-strip/")
    assert r.status_code == 200
    assert "註解清除" in r.text


def test_analyze_lists_types_and_authors(client):
    r = client.post(
        "/tools/pdf-annotations-strip/analyze",
        files={"file": ("doc.pdf", _make_pdf_with_annots(), "application/pdf")},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 3
    types = {x["label"]: x["type"] for x in j["by_type"]}
    assert "文字註解" in types
    assert types["文字註解"] == "Text"
    authors = {x["author"] for x in j["by_author"]}
    assert authors == {"Jason", "Mary"}


def test_strip_all_removes_every_annotation(client):
    pdf = _make_pdf_with_annots()
    assert _count_annots(pdf) == 3
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
        data={"mode": "all"},
    )
    assert r.status_code == 200
    assert r.headers.get("x-annotations-removed") == "3"
    assert _count_annots(r.content) == 0


def test_strip_filter_by_author_keeps_others(client):
    pdf = _make_pdf_with_annots()
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
        data={"mode": "filter", "authors": "Jason"},
    )
    assert r.status_code == 200
    # Both of Jason's annots gone, Mary's stays
    assert r.headers.get("x-annotations-removed") == "2"
    assert _count_annots(r.content) == 1


def test_strip_filter_by_type_keeps_others(client):
    pdf = _make_pdf_with_annots()
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
        data={"mode": "filter", "types": "Highlight"},
    )
    assert r.status_code == 200
    # Only the highlight is removed
    assert r.headers.get("x-annotations-removed") == "1"
    assert _count_annots(r.content) == 2


def test_strip_filter_empty_selection_returns_400(client):
    pdf = _make_pdf_with_annots()
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
        data={"mode": "filter"},
    )
    assert r.status_code == 400


def test_strip_rejects_non_pdf(client):
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        files={"file": ("note.txt", b"hi", "text/plain")},
        data={"mode": "all"},
    )
    assert r.status_code == 400


def test_strip_filename_handles_cjk(client):
    pdf = _make_pdf_with_annots()
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        files={"file": ("中文.pdf", pdf, "application/pdf")},
        data={"mode": "all"},
    )
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "filename*=" in cd or "filename=" in cd


def test_api_alias_works(client):
    pdf = _make_pdf_with_annots()
    r = client.post(
        "/tools/pdf-annotations-strip/api/pdf-annotations-strip",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
        data={"mode": "all"},
    )
    assert r.status_code == 200
    assert _count_annots(r.content) == 0
