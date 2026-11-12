"""MCP 第二批工具（v1.17.4）：每一支都經 `jtdt_run` 真的跑一次，走原本的 API、結果存進工作區。

驗的是「照 MCP 的說明呼叫就做得出來」：參數名稱與型別跟原本的 API 對得上、結果檔類型對、
需要另外處理的幾種（背景作業、兩個檔案欄位、固定檔名、ZIP 裡的圖片、精簡 JSON）都走得通。
需要 soffice / tesseract 的沒裝就跳過。
"""
from __future__ import annotations

import io
import json
import shutil

import fitz
import pytest

from tests.conftest import _make_pdf
from tests.test_mcp_server import _call, _upload_inline, mcp  # noqa: F401


def _run(c, tok, tool, files=(), params=None):
    args = {"tool": tool, "files": list(files)}
    if params is not None:
        args["params"] = params
    err, res = _call(c, tok, "jtdt_run", args)
    assert not err, (tool, res)
    if res.get("status") == "running":
        err, res = _call(c, tok, "job_status", {"job_id": res["job_id"], "wait_seconds": 120})
        assert not err and res["status"] == "done", (tool, res)
    return res


def _exts(res):
    return [f["ext"] for f in res.get("files", [])]


def _png(w=40, h=30, color=(200, 30, 30)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def _pdf_with_image_and_note() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello annotations")
    page.insert_image(fitz.Rect(72, 100, 172, 175), stream=_png(80, 60))
    page.add_text_annot((300, 300), "a note")
    page.add_highlight_annot(fitz.Rect(70, 60, 200, 80))
    out = doc.tobytes()
    doc.close()
    return out


def test_images_to_pdf_and_extract_images_back(mcp):
    c, tok = mcp
    a = _upload_inline(c, tok, _png(), "a.png")
    b = _upload_inline(c, tok, _png(color=(30, 200, 30)), "b.png")
    res = _run(c, tok, "image_to_pdf", [a, b], {"page_size": "a4"})
    assert _exts(res) == [".pdf"], res
    fid = _upload_inline(c, tok, _pdf_with_image_and_note(), "img.pdf")
    res = _run(c, tok, "pdf_extract_images", [fid])
    assert res["files"] and all(e in (".png", ".jpg", ".jpeg") for e in _exts(res)), res


def test_markdown_both_ways(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _make_pdf(2), "two.pdf")
    res = _run(c, tok, "pdf_to_markdown", [fid])
    assert _exts(res) == [".md"], res


@pytest.mark.skipif(not (shutil.which("soffice") or shutil.which("oxoffice")), reason="需要 soffice")
def test_markdown_to_document_from_text(mcp):
    c, tok = mcp
    res = _run(c, tok, "markdown_to_document", [], {"text": "# 報告\n\n- 一\n- 二", "format": "docx", "title": "rep"})
    assert _exts(res) == [".docx"], res


@pytest.mark.parametrize("tool,params", [
    ("pdf_watermark", {"text": "CONFIDENTIAL", "mode": "single"}),
    ("pdf_flatten_annotations", None),
    ("pdf_remove_annotations", {"mode": "filter", "types": "Highlight"}),
    ("pdf_n_up", {"cols": 2, "rows": 2}),
    ("pdf_clean_metadata", {"clear_toc": True}),
    ("pdf_reorder_pages", {"mode": "drop", "spec": "2"}),
    ("pdf_page_size", {"paper": "a5"}),
    ("pdf_bookmarks", {"toc_page": True}),
])
def test_pdf_edit_tools(mcp, tool, params):
    c, tok = mcp
    fid = _upload_inline(c, tok, _pdf_with_image_and_note() if "annotation" in tool else _make_pdf(3), "src.pdf")
    res = _run(c, tok, tool, [fid], params)
    assert _exts(res) == [".pdf"], res
    name = res["files"][0]["name"]
    assert name not in ("resized.pdf", "bookmarked.pdf"), "固定檔名改用來源檔名"


def test_encrypt_then_decrypt(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _make_pdf(1), "plain.pdf")
    res = _run(c, tok, "pdf_encrypt", [fid], {"user_pw": "s3cret-pw"})
    enc = res["files"][0]["file_id"]
    from app.mcp import settings as ms, registry
    ms.save({"tools": [t.name for t in registry.TOOLS]})     # 解除密碼預設不開放：測試時打開
    res = _run(c, tok, "pdf_decrypt", [enc], {"password": "s3cret-pw"})
    assert _exts(res) == [".pdf"]
    err, msg = _call(c, tok, "jtdt_run", {"tool": "pdf_decrypt", "files": [enc], "params": {"password": "wrong"}})
    if not err and msg.get("status") == "running":
        err, msg = _call(c, tok, "job_status", {"job_id": msg["job_id"], "wait_seconds": 30})
    assert err, "密碼錯要回錯誤"


def test_decrypt_thsr_mode(mcp):
    """高鐵模式（密碼＝出發日期）透過 MCP 也做得到；結果檔名是那個日期。"""
    c, tok = mcp
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "ticket")
    enc = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="20261108", owner_pw="o-20261108")
    doc.close()
    fid = _upload_inline(c, tok, enc, "thsr-ticket.pdf")
    from app.mcp import settings as ms, registry
    ms.save({"tools": [t.name for t in registry.TOOLS]})
    res = _run(c, tok, "pdf_decrypt", [fid],
               {"thsr_mode": True, "thsr_date_from": "2026-10-01", "thsr_date_to": "2026-11-30"})
    assert [f["name"] for f in res["files"]] == ["20261108.pdf"], res
    err, msg = _call(c, tok, "jtdt_run", {"tool": "pdf_decrypt", "files": [fid], "params": {
        "thsr_mode": True, "thsr_date_from": "2026-01-01", "thsr_date_to": "2026-01-31"}})
    assert err and "31" in str(msg), "範圍裡沒有那一天要回錯誤並說試了幾天"


def test_audit_does_not_record_passwords(mcp, monkeypatch):
    from app.core import audit_db
    seen = []
    real = audit_db.log_event
    monkeypatch.setattr(audit_db, "log_event", lambda ev, **kw: (seen.append((ev, kw)), real(ev, **kw))[1])
    c, tok = mcp
    fid = _upload_inline(c, tok, _make_pdf(1), "p.pdf")
    _run(c, tok, "pdf_encrypt", [fid], {"user_pw": "Top-Secret-123"})
    assert "Top-Secret-123" not in json.dumps([kw for _, kw in seen], ensure_ascii=False, default=str)


def test_text_and_document_diff(mcp):
    c, tok = mcp
    res = _run(c, tok, "text_diff", [], {"text_a": "a\nb\nc", "text_b": "a\nB\nc\nd"})
    t = res["result"]["totals"]
    assert t["added"] >= 1 and t["changed"] >= 1, t
    old = _upload_inline(c, tok, _make_pdf(2), "old.pdf")
    doc = fitz.open(stream=_make_pdf(2), filetype="pdf")
    doc[1].insert_text((72, 200), "new paragraph")
    new = _upload_inline(c, tok, doc.tobytes(), "new.pdf")
    res = _run(c, tok, "document_diff", [old, new])
    body = json.dumps(res, ensure_ascii=False)
    assert "totals" in body and '"marks"' not in body and '"size"' not in body, "兩個檔案欄位都送到、標示框座標拿掉"


def test_document_diff_needs_exactly_two_files(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _make_pdf(1), "one.pdf")
    err, msg = _call(c, tok, "jtdt_run", {"tool": "document_diff", "files": [fid]})
    assert err


@pytest.mark.skipif(not (shutil.which("soffice") or shutil.which("oxoffice")), reason="需要 soffice")
def test_office_convert_follows_the_job(mcp):
    """原本的 API 回 job_id：執行器照 API 文件輪詢、做完再下載。"""
    from docx import Document
    buf = io.BytesIO()
    d = Document()
    d.add_paragraph("hello office")
    d.save(buf)
    c, tok = mcp
    fid = _upload_inline(c, tok, buf.getvalue(), "memo.docx")
    res = _run(c, tok, "office_convert", [fid], {"target": "odt"})
    assert _exts(res) == [".odt"], res


@pytest.mark.skipif(not shutil.which("tesseract"), reason="需要 tesseract")
def test_pdf_ocr_follows_the_job(mcp, monkeypatch):
    # 用 Tesseract：EasyOCR（PyTorch）在沒有 AVX2 的機器上會 SIGILL 整個行程（開發機就是）
    from app.core import ocr_engine
    monkeypatch.setattr(ocr_engine, "get_default_engine", lambda: "tesseract")
    monkeypatch.setattr(ocr_engine, "is_easyocr_available", lambda: False)
    c, tok = mcp
    fid = _upload_inline(c, tok, _make_pdf(1), "scan.pdf")
    res = _run(c, tok, "pdf_ocr", [fid], {"lang": "eng", "dpi": 72, "skip_pages_with_text": False})
    assert _exts(res) == [".pdf"], res


def test_boolean_params_are_checked(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _make_pdf(1), "b.pdf")
    err, msg = _call(c, tok, "jtdt_run", {"tool": "pdf_clean_metadata", "files": [fid], "params": {"clear_toc": "yes"}})
    assert err and "true or false" in msg


def test_titles_match_the_site_tool_names():
    """設定頁、稽核用的中文名稱跟網站上的工具名稱一樣（譯文也才找得到）。"""
    from app import main as app_main
    from app.mcp import registry
    names = {t.metadata.id: t.metadata.name for t in app_main.tools}
    # 同一支工具拆成兩個 MCP 工具時（例如表單自動填寫的「公司清單」），標題寫成「工具名稱：用途」
    assert {t.name: t.title for t in registry.TOOLS
            if t.title != names.get(t.tool_id) and not t.title.startswith(f"{names.get(t.tool_id)}：")} == {}
    assert set(t.category for t in registry.TOOLS) <= set(registry.CATEGORIES)
