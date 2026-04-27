"""Tests for the pdf-annotations tool."""
from __future__ import annotations

import io
import json

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app


def _make_pdf_with_annots() -> bytes:
    """Build a 3-page PDF with several user-facing annotations."""
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 80), "First page text — a contract clause.", fontsize=12, fontname="helv")
    a = p1.add_text_annot((40, 70), "請修改金額")
    a.set_info(content="請修改金額", title="Jason")
    a.update()

    h = p1.add_highlight_annot(fitz.Rect(50, 70, 250, 90))
    h.set_info(content="", title="Jason")  # highlight, content recovered from text
    h.update()

    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 80), "Second page about legal review.", fontsize=12, fontname="helv")
    a2 = p2.add_text_annot((40, 70), "這段需法務確認")
    a2.set_info(content="這段需法務確認", title="Mary", subject="Legal")
    a2.update()

    p3 = doc.new_page(width=595, height=842)
    p3.insert_text((50, 80), "Third page placeholder.", fontsize=12, fontname="helv")
    # no annotation on p3

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_blank_pdf() -> bytes:
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


@pytest.fixture
def client():
    return TestClient(app)


def _analyze(client, pdf_bytes: bytes = None, name: str = "doc.pdf") -> str:
    """Helper: upload + analyze, return upload_id."""
    if pdf_bytes is None:
        pdf_bytes = _make_pdf_with_annots()
    r = client.post(
        "/tools/pdf-annotations/analyze",
        files={"file": (name, pdf_bytes, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    return r.json()["upload_id"]


# ---------- index ----------

def test_index_renders(client):
    r = client.get("/tools/pdf-annotations/")
    assert r.status_code == 200
    assert "註解整理" in r.text


# ---------- analyze ----------

def test_analyze_returns_summary_and_annots(client):
    r = client.post(
        "/tools/pdf-annotations/analyze",
        files={"file": ("doc.pdf", _make_pdf_with_annots(), "application/pdf")},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["page_count"] == 3
    assert j["filename"] == "doc.pdf"
    assert "upload_id" in j
    assert len(j["upload_id"]) == 32
    assert j["summary"]["total"] == 3            # 2 text annots + 1 highlight
    assert j["summary"]["pages_with_annot"] == 2
    authors = {x["author"] for x in j["summary"]["by_author"]}
    assert authors == {"Jason", "Mary"}
    types = {x["label"] for x in j["summary"]["by_type"]}
    assert {"文字註解", "螢光筆"}.issubset(types)


def test_analyze_recovers_highlight_text(client):
    """Highlights typically have empty content. We recover text via quad rects."""
    r = client.post(
        "/tools/pdf-annotations/analyze",
        files={"file": ("doc.pdf", _make_pdf_with_annots(), "application/pdf")},
    )
    annots = r.json()["annots"]
    highlights = [a for a in annots if a["type"] == "Highlight"]
    assert highlights
    # The highlight covers some of "First page text — a contract clause."
    # content should now contain at least part of that text.
    assert highlights[0]["content"]


def test_analyze_rejects_non_pdf(client):
    r = client.post(
        "/tools/pdf-annotations/analyze",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400


def test_analyze_rejects_empty_pdf(client):
    r = client.post(
        "/tools/pdf-annotations/analyze",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert r.status_code == 400


def test_analyze_blank_pdf_has_zero_annots(client):
    r = client.post(
        "/tools/pdf-annotations/analyze",
        files={"file": ("blank.pdf", _make_blank_pdf(), "application/pdf")},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["summary"]["total"] == 0
    assert j["annots"] == []


def test_api_alias_works(client):
    r = client.post(
        "/tools/pdf-annotations/api/pdf-annotations",
        files={"file": ("doc.pdf", _make_pdf_with_annots(), "application/pdf")},
    )
    assert r.status_code == 200
    assert r.json()["summary"]["total"] == 3


# ---------- export-csv ----------

def test_export_csv_full(client):
    uid = _analyze(client)
    r = client.post("/tools/pdf-annotations/export-csv", data={"upload_id": uid})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    rows = [ln for ln in text.splitlines() if ln.strip()]
    assert rows[0].startswith("page,type,")
    assert len(rows) == 1 + 3  # header + 3 annots


def test_export_csv_filter_by_author(client):
    uid = _analyze(client)
    r = client.post("/tools/pdf-annotations/export-csv",
                    data={"upload_id": uid, "authors": "Mary"})
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    rows = [ln for ln in text.splitlines() if ln.strip()]
    assert len(rows) == 1 + 1  # only Mary's
    assert "Mary" in rows[1]


def test_export_csv_filter_by_type(client):
    uid = _analyze(client)
    r = client.post("/tools/pdf-annotations/export-csv",
                    data={"upload_id": uid, "types": "Text"})  # exclude highlight
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    rows = [ln for ln in text.splitlines() if ln.strip()]
    # 2 text annots, no highlight
    assert len(rows) == 1 + 2


def test_export_csv_filename_handles_cjk(client):
    uid = _analyze(client, name="中文檔名.pdf")
    r = client.post("/tools/pdf-annotations/export-csv", data={"upload_id": uid})
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "filename*=" in cd or "filename=" in cd


def test_export_csv_invalid_upload_id_returns_400(client):
    r = client.post("/tools/pdf-annotations/export-csv",
                    data={"upload_id": "not-hex"})
    assert r.status_code == 400


def test_export_csv_expired_upload_id_returns_410(client):
    r = client.post("/tools/pdf-annotations/export-csv",
                    data={"upload_id": "0" * 32})
    assert r.status_code == 410


# ---------- export-review (Markdown) ----------

def test_export_review_groups_by_page(client):
    uid = _analyze(client)
    r = client.post("/tools/pdf-annotations/export-review",
                    data={"upload_id": uid, "group_by": "page"})
    assert r.status_code == 200
    md = r.content.decode("utf-8")
    assert "# 註解審閱報告" in md
    assert "## 第 1 頁" in md
    assert "## 第 2 頁" in md


def test_export_review_groups_by_author(client):
    uid = _analyze(client)
    r = client.post("/tools/pdf-annotations/export-review",
                    data={"upload_id": uid, "group_by": "author"})
    assert r.status_code == 200
    md = r.content.decode("utf-8")
    assert "## Jason" in md
    assert "## Mary" in md


# ---------- export-todo ----------

def test_export_todo_markdown_has_checkboxes(client):
    uid = _analyze(client)
    r = client.post("/tools/pdf-annotations/export-todo",
                    data={"upload_id": uid, "fmt": "md"})
    assert r.status_code == 200
    md = r.content.decode("utf-8")
    assert "# 待辦清單" in md
    assert "- [ ]" in md
    # 3 todo lines
    assert md.count("- [ ]") == 3


def test_export_todo_csv(client):
    uid = _analyze(client)
    r = client.post("/tools/pdf-annotations/export-todo",
                    data={"upload_id": uid, "fmt": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    rows = [ln for ln in text.splitlines() if ln.strip()]
    assert rows[0].startswith("status,page,todo,")
    assert len(rows) == 1 + 3


# ---------- preview thumbnails ----------

def test_preview_returns_png(client):
    uid = _analyze(client)
    r = client.get(f"/tools/pdf-annotations/preview/{uid}/1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    # PNG magic bytes
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_preview_invalid_upload_id_returns_400(client):
    r = client.get("/tools/pdf-annotations/preview/not-hex/1")
    assert r.status_code == 400


def test_preview_out_of_range_page_returns_404(client):
    uid = _analyze(client)
    r = client.get(f"/tools/pdf-annotations/preview/{uid}/9999")
    assert r.status_code == 404


def test_preview_expired_upload_id_returns_410(client):
    r = client.get(f"/tools/pdf-annotations/preview/{'0'*32}/1")
    assert r.status_code == 410


# ---------- export-json ----------

def test_export_json_returns_full_payload(client):
    uid = _analyze(client)
    r = client.post("/tools/pdf-annotations/export-json", data={"upload_id": uid})
    assert r.status_code == 200
    j = json.loads(r.content.decode("utf-8"))
    assert j["page_count"] == 3
    assert j["summary"]["total"] == 3
    assert isinstance(j["annots"], list)
