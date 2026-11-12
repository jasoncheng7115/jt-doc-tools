"""MCP 第四批（v1.17.37）：表單自動填寫、PDF 編輯器（不含圖片）、會議錄音轉逐字稿。

* 表單自動填寫：先列公司（只有編號與名稱）、再填；指定不存在的公司要失敗，不可以默默用別家公司的資料。
* PDF 編輯器：文字、框、線這些可以；**圖片、印章、簽名一律不行** —— 資產庫的章經 AI 用印另有規則，
  不可以從編輯器繞過。擋兩層：MCP 送出前檢查，原本的 API 也認得「經 MCP 來的」再擋一次。
* 逐字稿：存一份可閱讀的 .txt 與完整 .json，回前一段文字。
"""
from __future__ import annotations

import json

import fitz
import pytest

from tests.test_mcp_server import _call, _upload_inline, mcp  # noqa: F401
from tests.test_mcp_tools_batch2 import _exts, _run
from tests.test_mcp_tools_batch3 import _err, _text_pdf
from tests.test_meeting_transcribe import unconfigured  # noqa: F401  逐字稿：語音服務設定的前後清理


def _open_ws(file_id: str):
    from app.core import workspace as ws
    from app.mcp import registry, runner
    got = runner.resolve_inputs(registry.BY_NAME["pdf_compress"], [file_id], ws.key_for_user_id(None))
    return fitz.open(got[0]["path"])


def _enable_all():
    from app.mcp import registry, settings as ms
    ms.save({"tools": [t.name for t in registry.TOOLS]})


def test_pdf_fill_lists_companies_then_fills(mcp):
    c, tok = mcp
    _enable_all()
    res = _run(c, tok, "pdf_fill_companies")
    companies = res["result"]["companies"]
    assert companies and set(companies[0]) == {"id", "name", "is_active"}, "只回編號與名稱，不含欄位資料"
    fid = _upload_inline(c, tok, _text_pdf("公司名稱："), "vendor-form.pdf")
    res = _run(c, tok, "pdf_fill_form", [fid], {"company_id": companies[0]["id"]})
    assert _exts(res) == [".pdf"], res
    err, msg = _err(c, tok, "pdf_fill_form", [fid], {"company_id": "no-such-company"})
    assert err and "company_id" in msg, "不存在的公司要失敗"


def test_pdf_fill_tools_are_off_by_default():
    from app.mcp import registry
    assert not registry.BY_NAME["pdf_fill_form"].default_on, "填進去的是公司資料（地址、帳戶）：管理員另外勾"
    assert not registry.BY_NAME["pdf_fill_companies"].default_on
    assert not registry.BY_NAME["meeting_transcribe"].default_on, "錄音很大、處理很久：管理員另外勾"


def test_pdf_edit_draws_text_and_shapes(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _text_pdf("original"), "doc.pdf")
    model = {"pages": [{"page": 0, "objects": [
        {"type": "text", "x": 72, "y": 120, "w": 200, "h": 20, "text": "REVIEWED-BY-AI", "font_size": 14,
         "color": "#cc0000"},
        {"type": "rect", "x": 60, "y": 110, "w": 230, "h": 40, "stroke": "#0000ff", "stroke_width": 1}]}]}
    res = _run(c, tok, "pdf_edit", [fid], {"model": json.dumps(model)})
    with _open_ws(res["files"][0]["file_id"]) as d:
        assert "REVIEWED-BY-AI" in d[0].get_text() and "original" in d[0].get_text()


@pytest.mark.parametrize("obj", [
    {"type": "image", "x": 0, "y": 0, "w": 50, "h": 50, "asset_id": "anything"},
    {"type": "text", "x": 0, "y": 0, "w": 50, "h": 50, "text": "x", "asset_id": "anything"},
    {"type": "image", "x": 0, "y": 0, "w": 50, "h": 50, "existing_src": "/tools/pdf-editor/preview/pe_x.png"},
])
def test_pdf_edit_refuses_images_and_stamps(mcp, obj):
    c, tok = mcp
    fid = _upload_inline(c, tok, _text_pdf("doc"), "doc.pdf")
    err, msg = _err(c, tok, "pdf_edit", [fid], {"model": json.dumps({"pages": [{"page": 0, "objects": [obj]}]})})
    assert err and "not available through MCP" in msg, msg


def test_editor_api_itself_refuses_images_from_mcp(client, auth_off):
    """第二層：就算 MCP 那一層的檢查被拿掉，原本的 API 看到「經 MCP 來的」也不收圖片。"""
    from app.core import api_tokens as at
    from app.mcp import runner
    tok = at.api_tokens.create("editor-guard")
    try:
        model = json.dumps({"pages": [{"page": 0, "objects": [
            {"type": "image", "x": 0, "y": 0, "w": 50, "h": 50, "asset_id": "anything"}]}]})
        files = {"file": ("a.pdf", _text_pdf("doc"), "application/pdf")}
        h = {"Authorization": f"Bearer {tok.token}"}
        r = client.post("/tools/pdf-editor/api/pdf-editor", files=files, data={"model": model},
                        headers={**h, "X-JTDT-Via-MCP": runner.VIA_SECRET})
        assert r.status_code == 400 and "MCP" in r.json()["detail"], r.text
        r = client.post("/tools/pdf-editor/api/pdf-editor", files=files, data={"model": model}, headers=h)
        assert r.status_code == 200, "一般 API 呼叫照舊（找不到的資產略過）"
        r = client.post("/tools/pdf-editor/api/pdf-editor", files=files, data={"model": model},
                        headers={**h, "X-JTDT-Via-MCP": "forged"})
        assert r.status_code == 200, "偽造的標頭不算經 MCP（也不會因此被擋或被放行）"
    finally:
        at.api_tokens.revoke(tok.token)


def test_meeting_transcribe_saves_text_and_json(mcp, unconfigured):
    from tests.test_meeting_transcribe import FakeJtlw, _M4A, _configure
    c, tok = mcp
    _enable_all()
    with FakeJtlw() as fake:
        _configure(fake)
        fid = _upload_inline(c, tok, _M4A, "weekly.m4a")
        res = _run(c, tok, "meeting_transcribe", [fid], {"language": "zh-Hant"})
    assert sorted(_exts(res)) == [".json", ".txt"], res
    assert res["segments"] >= 1 and res["text"].startswith("["), res
    assert _err(c, tok, "meeting_transcribe", [fid], {"language": "zh Hant"})[0]

