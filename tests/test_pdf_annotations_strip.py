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


def _analyze(client, pdf=None, name="doc.pdf"):
    pdf = pdf if pdf is not None else _make_pdf_with_annots()
    r = client.post(
        "/tools/pdf-annotations-strip/analyze",
        files={"file": (name, pdf, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_index_renders(client):
    r = client.get("/tools/pdf-annotations-strip/")
    assert r.status_code == 200
    assert "註解清除" in r.text


def test_analyze_lists_types_and_authors(client):
    j = _analyze(client)
    assert j["total"] == 3
    assert j["upload_id"] and len(j["upload_id"]) == 32
    assert isinstance(j.get("annots"), list) and len(j["annots"]) == 3
    types = {x["label"]: x["type"] for x in j["by_type"]}
    assert "文字註解" in types
    assert types["文字註解"] == "Text"
    authors = {x["author"] for x in j["by_author"]}
    assert authors == {"Jason", "Mary"}


def test_analyze_annots_have_page_and_type(client):
    j = _analyze(client)
    a0 = j["annots"][0]
    assert "page" in a0 and "type" in a0 and "type_label" in a0 and "author" in a0


def test_strip_all_removes_every_annotation(client):
    j = _analyze(client)
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        data={"upload_id": j["upload_id"], "mode": "all"},
    )
    assert r.status_code == 200
    assert r.headers.get("x-annotations-removed") == "3"
    assert _count_annots(r.content) == 0


def test_strip_filter_by_author_keeps_others(client):
    j = _analyze(client)
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        data={"upload_id": j["upload_id"], "mode": "filter", "authors": "Jason"},
    )
    assert r.status_code == 200
    assert r.headers.get("x-annotations-removed") == "2"
    assert _count_annots(r.content) == 1


def test_strip_filter_by_type_keeps_others(client):
    j = _analyze(client)
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        data={"upload_id": j["upload_id"], "mode": "filter", "types": "Highlight"},
    )
    assert r.status_code == 200
    assert r.headers.get("x-annotations-removed") == "1"
    assert _count_annots(r.content) == 2


def test_strip_filter_empty_selection_returns_400(client):
    j = _analyze(client)
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        data={"upload_id": j["upload_id"], "mode": "filter"},
    )
    assert r.status_code == 400


def test_strip_invalid_upload_id_returns_400(client):
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        data={"upload_id": "not-hex", "mode": "all"},
    )
    assert r.status_code == 400


def test_strip_expired_upload_id_returns_410(client):
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        data={"upload_id": "0" * 32, "mode": "all"},
    )
    assert r.status_code == 410


def test_preview_returns_png(client):
    j = _analyze(client)
    r = client.get(f"/tools/pdf-annotations-strip/preview/{j['upload_id']}/1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_preview_invalid_upload_id_400(client):
    r = client.get("/tools/pdf-annotations-strip/preview/bad/1")
    assert r.status_code == 400


def test_preview_expired_upload_id_410(client):
    r = client.get("/tools/pdf-annotations-strip/preview/" + "a" * 32 + "/1")
    assert r.status_code == 410


def test_strip_filename_handles_cjk(client):
    j = _analyze(client, name="中文.pdf")
    r = client.post(
        "/tools/pdf-annotations-strip/strip",
        data={"upload_id": j["upload_id"], "mode": "all"},
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


def test_api_alias_rejects_non_pdf(client):
    r = client.post(
        "/tools/pdf-annotations-strip/api/pdf-annotations-strip",
        files={"file": ("note.txt", b"hi", "text/plain")},
        data={"mode": "all"},
    )
    assert r.status_code == 400


def test_api_alias_filter_empty_400(client):
    pdf = _make_pdf_with_annots()
    r = client.post(
        "/tools/pdf-annotations-strip/api/pdf-annotations-strip",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
        data={"mode": "filter"},
    )
    assert r.status_code == 400
