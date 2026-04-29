"""Tests for the renamed 文件差異比對 tool (formerly pdf-diff).

Covers: PDF×PDF compare, accept-list extension validation, redirect from
old `/tools/pdf-diff` to `/tools/doc-diff`, and the new Office-input path
(skipped if soffice isn't installed on the test runner)."""
from __future__ import annotations

import io

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core import office_convert


def _pdf_with_text(lines: list[str]) -> bytes:
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 80), "\n".join(lines), fontsize=12, fontname="helv")
    out = io.BytesIO()
    doc.save(out); doc.close()
    return out.getvalue()


@pytest.fixture
def client():
    return TestClient(app)


def test_index_renders_new_name(client):
    r = client.get("/tools/doc-diff/")
    assert r.status_code == 200
    assert "文件差異比對" in r.text
    # The accept attribute should advertise the office extensions
    assert ".docx" in r.text and ".odt" in r.text


def test_legacy_pdf_diff_url_redirects(client):
    """Bookmarks pointing at /tools/pdf-diff/ must keep working (301 → doc-diff)."""
    r = client.get("/tools/pdf-diff/", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"].endswith("/tools/doc-diff/")
    # And without trailing slash
    r = client.get("/tools/pdf-diff", follow_redirects=False)
    assert r.status_code == 301


def test_compare_two_pdfs_returns_diff(client):
    a = _pdf_with_text(["alpha", "beta", "gamma"])
    b = _pdf_with_text(["alpha", "BETA-changed", "gamma", "delta"])
    r = client.post(
        "/tools/doc-diff/compare",
        files={
            "file_a": ("a.pdf", a, "application/pdf"),
            "file_b": ("b.pdf", b, "application/pdf"),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page_count_a"] == 1 and body["page_count_b"] == 1
    totals = body["totals"]
    # Some non-zero diff expected (changed line + inserted line)
    assert totals["added"] + totals["removed"] + totals["changed"] >= 2


def test_compare_rejects_unsupported_extension(client):
    a = _pdf_with_text(["x"])
    r = client.post(
        "/tools/doc-diff/compare",
        files={
            "file_a": ("a.pdf", a, "application/pdf"),
            "file_b": ("b.png", b"\x89PNG\r\n\x1a\n", "image/png"),
        },
    )
    assert r.status_code == 400
    assert "不支援" in r.json()["detail"]


@pytest.mark.skipif(
    office_convert.find_soffice() is None,
    reason="soffice (OxOffice/LibreOffice) not installed on this runner",
)
def test_compare_office_to_pdf_works(client):
    """End-to-end with a real Office input: build a tiny .docx via python-docx,
    compare it against a PDF version of (similar) text, expect a 200 response."""
    try:
        from docx import Document
    except Exception:
        pytest.skip("python-docx not available")
    docx_buf = io.BytesIO()
    d = Document()
    d.add_paragraph("hello world")
    d.add_paragraph("second line")
    d.save(docx_buf)

    pdf_b = _pdf_with_text(["hello world", "different line"])
    r = client.post(
        "/tools/doc-diff/compare",
        files={
            "file_a": ("a.docx", docx_buf.getvalue(),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "file_b": ("b.pdf", pdf_b, "application/pdf"),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page_count_a"] >= 1 and body["page_count_b"] == 1
