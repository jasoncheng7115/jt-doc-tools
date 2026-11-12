"""MCP 第三批工具（v1.17.37）：原本「之後開放」的 9 支＋有 LLM 選項的 5 支，每一支都經 `jtdt_run` 真的跑一次。

驗的是「照 MCP 的說明呼叫就做得出來」：參數名稱與型別跟原本的 API 對得上、結果類型對；
新加的三種處理也要走得通 —— JSON 字串參數（清單處理的 ops）、逗號清單參數（文字去識別化的 types）、
背景作業做完回 JSON 報告（送件前檢核）。有 LLM 的工具：MCP 這條路一定不呼叫 LLM（就算站台設定了 LLM）。
需要 soffice / pdf2docx / pyzbar 的沒裝就跳過。
"""
from __future__ import annotations

import importlib.util
import io
import shutil

import fitz
import pytest

from tests.conftest import _make_pdf
from tests.test_mcp_server import _call, _upload_inline, mcp  # noqa: F401
from tests.test_mcp_tools_batch2 import _exts, _png, _run

_SOFFICE = bool(shutil.which("soffice") or shutil.which("oxoffice") or shutil.which("libreoffice"))


def _text_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    out = doc.tobytes()
    doc.close()
    return out


def _err(c, tok, tool, files=(), params=None):
    err, msg = _call(c, tok, "jtdt_run", {"tool": tool, "files": list(files), "params": params or {}})
    if not err and isinstance(msg, dict) and msg.get("status") == "running":
        err, msg = _call(c, tok, "job_status", {"job_id": msg["job_id"], "wait_seconds": 60})
    return err, str(msg)


# --------------------------------------------------------------------------- 轉檔

@pytest.mark.skipif(importlib.util.find_spec("pdf2docx") is None, reason="需要 pdf2docx")
def test_pdf_to_office(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _text_pdf("Hello office"), "letter.pdf")
    res = _run(c, tok, "pdf_to_office", [fid], {"output_format": "docx"})
    assert _exts(res) == [".docx"], res


@pytest.mark.skipif(not _SOFFICE, reason="需要 soffice（Impress）")
def test_pdf_to_slides(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _make_pdf(2), "deck.pdf")
    res = _run(c, tok, "pdf_to_slides", [fid], {"output_format": "odp"})
    assert _exts(res) == [".odp"], res


# --------------------------------------------------------------------------- PDF 編修

def test_border_straighten_and_scan_merge(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _make_pdf(3), "pages.pdf")
    res = _run(c, tok, "pdf_add_border", [fid], {"style": "dashed", "pages": "1,3", "double": True})
    assert _exts(res) == [".pdf"], res
    assert _err(c, tok, "pdf_add_border", [fid], {"color": "red"})[0], "顏色要是 #RRGGBB"

    res = _run(c, tok, "document_straighten", [fid], {"dpi": 150})
    assert _exts(res) == [".pdf"], res
    assert _err(c, tok, "document_straighten", [fid], {"dpi": 400})[0]

    a = _upload_inline(c, tok, _png(200, 120), "front.png")
    b = _upload_inline(c, tok, _png(200, 120, (30, 30, 200)), "back.png")
    res = _run(c, tok, "scan_merge", [a, b], {"layout": "auto"})
    assert _exts(res) == [".pdf"] and res["files"][0]["name"] != "scan-merge.pdf", res


# --------------------------------------------------------------------------- 擷取與 JSON 結果

def test_attachments_hidden_scan_and_annotations(mcp):
    c, tok = mcp
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "with attachment")
    page.add_text_annot((200, 200), "please fix")
    doc.embfile_add("notes.txt", b"attached notes")
    doc.embfile_add("tool.exe", b"MZ not allowed in workspace")
    data = doc.tobytes()
    doc.close()
    fid = _upload_inline(c, tok, data, "att.pdf")

    res = _run(c, tok, "pdf_extract_attachments", [fid])
    assert [f["name"] for f in res["files"]] == ["notes.txt"], "工作區不收的種類略過"
    plain = _upload_inline(c, tok, _make_pdf(1), "plain.pdf")
    err, msg = _err(c, tok, "pdf_extract_attachments", [plain])
    assert err and "NOT_FOUND" in msg or "404" in msg

    res = _run(c, tok, "pdf_hidden_content_scan", [fid])
    assert res["result"]["totals"]["embeds"] >= 1, res

    res = _run(c, tok, "pdf_list_annotations", [fid])
    out = res["result"]
    assert "upload_id" not in out and any("please fix" in str(a) for a in out["annots"]), out


def test_transit_proof_reports_unreadable_files(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _text_pdf("not a receipt"), "random.pdf")
    res = _run(c, tok, "transit_proof_parse", [fid])
    out = res["result"]
    assert out["count"] == 0 and len(out["failed"]) == 1, out


def _zbar_available() -> bool:
    """pyzbar 套件裝了不代表能用：還要系統的 libzbar（CI 沒有）。跟工具回 503 用同一個判斷。"""
    from app.tools.einvoice_scan import qr_decoder
    return qr_decoder.is_qr_backend_available()


@pytest.mark.skipif(not _zbar_available(), reason="zbar / pyzbar 不可用")
def test_einvoice_scan_without_qr(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _png(300, 300, (255, 255, 255)), "blank.png")
    res = _run(c, tok, "einvoice_scan", [fid])
    out = res["result"]
    assert out["parsed_count"] == 0 and "buffer" not in out, out


# --------------------------------------------------------------------------- 新的參數型別

def test_text_list_ops_are_a_json_string(mcp):
    c, tok = mcp
    res = _run(c, tok, "text_list_process", [], {"text": " b\na\nb \n\nA",
                                                  "ops": '[{"op":"trim"},{"op":"drop_empty"},'
                                                         '{"op":"dedup","case_insensitive":true},{"op":"sort"}]'})
    out = res["result"]
    assert out["lines"] == ["a", "b"] and out["ops_applied"] == ["trim", "drop_empty", "dedup", "sort"], out
    res = _run(c, tok, "text_list_process", [], {"text": "x\ny"})
    assert res["result"]["lines"] == ["x", "y"], "沒給 ops 就原樣回"
    for bad in ("not json", '{"op":"sort"}'):
        err, msg = _err(c, tok, "text_list_process", [], {"text": "a", "ops": bad})
        assert err and "ops" in msg, (bad, msg)


def test_text_deidentify_types_are_comma_separated(mcp):
    c, tok = mcp
    text = "手機 0912-345-678，信箱 someone@example.com"
    res = _run(c, tok, "text_deidentify", [], {"text": text, "types": "mobile", "mode": "redact"})
    out = res["result"]
    kinds = {f.get("type") for f in out["findings"]}
    assert kinds == {"mobile"}, out["findings"]
    assert "0912-345-678" not in out["text"] and "someone@example.com" in out["text"], out["text"]
    res = _run(c, tok, "text_deidentify", [], {"text": text})
    assert "someone@example.com" not in res["result"]["text"], "沒給 types 用預設集（含 email）"
    assert _err(c, tok, "text_deidentify", [], {"text": text, "types": "mobile; email"})[0]


def test_document_deidentify(mcp):
    c, tok = mcp
    fid = _upload_inline(c, tok, _text_pdf("Contact: someone@example.com"), "contact.pdf")
    res = _run(c, tok, "document_deidentify", [fid], {"mode": "redact", "types": "email"})
    assert _exts(res) == [".pdf"], res
    from app.core import workspace as ws
    from app.mcp import runner
    got = runner.resolve_inputs(__import__("app.mcp.registry", fromlist=["x"]).BY_NAME["pdf_compress"],
                                [res["files"][0]["file_id"]], ws.key_for_user_id(None))
    with fitz.open(got[0]["path"]) as d:
        assert "someone@example.com" not in d[0].get_text(), "redact 要真的把文字拿掉"


# --------------------------------------------------------------------------- LLM 一律不用

def test_submission_check_returns_the_report_and_never_calls_the_llm(mcp, monkeypatch):
    import importlib
    sc = importlib.import_module("app.tools.submission_check.router")
    called = []
    monkeypatch.setattr(sc._l3, "llm_available", lambda: True)
    monkeypatch.setattr(sc._l4, "vision_available", lambda: True)
    monkeypatch.setattr(sc._l3, "merge_entity_variants", lambda *a, **k: called.append("l5") or [])
    monkeypatch.setattr(sc._l3, "detect_template_residue", lambda *a, **k: called.append("l5b") or [])
    monkeypatch.setattr(sc._l4, "vision_check_file", lambda *a, **k: called.append("l6") or [])
    c, tok = mcp
    a = _upload_inline(c, tok, _text_pdf("Example Co., Ltd. 12345678"), "quote.pdf")
    b = _upload_inline(c, tok, _text_pdf("Example Co Ltd 12345678"), "contract.pdf")
    res = _run(c, tok, "submission_check", [a, b], {"main_entity_name": "Example Co., Ltd.",
                                                       "deadline": "2026-12-31"})
    rep = res["result"]
    assert isinstance(rep, dict) and rep.get("summary") is not None, rep
    assert called == [], f"MCP 不可以呼叫 LLM：{called}"
    assert _err(c, tok, "submission_check", [a], {"deadline": "2026/12/31"})[0]


def test_submission_check_web_flow_still_uses_llm_when_available(client, auth_off, monkeypatch):
    """網頁的流程不受影響：有 LLM 時照樣跑 L5（allow_llm 預設 True）。"""
    import importlib
    sc = importlib.import_module("app.tools.submission_check.router")
    from app.tools.submission_check import case_manager as cm
    called = []
    monkeypatch.setattr(sc._l3, "llm_available", lambda: True)
    monkeypatch.setattr(sc._l3, "merge_entity_variants", lambda *a, **k: [])
    monkeypatch.setattr(sc._l3, "detect_template_residue", lambda *a, **k: called.append("l5") or [])
    monkeypatch.setattr(sc._l4, "vision_available", lambda: False)
    r = client.post("/tools/submission-check/upload",
                    files=[("files", ("a.pdf", _text_pdf("Example Co. 12345678"), "application/pdf"))],
                    data={"main_entity_name": "Example Co."})
    assert r.status_code == 200, r.text
    case_id = r.json()["case_id"]
    r = client.post(f"/tools/submission-check/run/{case_id}")
    assert r.status_code == 200, r.text
    import time
    from app.core.job_manager import job_manager
    jid = r.json()["job_id"]
    for _ in range(600):
        j = job_manager.get(jid)
        if j.status in ("done", "error"):
            break
        time.sleep(0.05)
    assert j.status == "done", j.error
    assert called, "網頁流程有 LLM 時要照常跑 L5"
    assert cm.load_case(case_id)


def test_new_tools_are_registered_and_open():
    from app.mcp import registry
    names = {"pdf_to_office", "pdf_to_slides", "document_straighten", "pdf_add_border", "pdf_extract_attachments",
             "pdf_hidden_content_scan", "scan_merge", "text_list_process", "transit_proof_parse",
             "pdf_list_annotations", "einvoice_scan", "document_deidentify", "text_deidentify", "submission_check"}
    assert names <= set(registry.NAMES)
    assert registry.BY_NAME["submission_check"].force == {"allow_llm": False}
    left = {k: v for k, v in registry.CLASSIFIED.items() if v != "open"}
    assert set(left.values()) <= {"excluded", "sensitive"}, left


def test_form_tool_json_param_passes_through(mcp):
    """表單型 API 的 JSON 字串參數（頁面轉向的 per_page）：MCP 先檢查格式，原樣送給 API。"""
    c, tok = mcp
    fid = _upload_inline(c, tok, _make_pdf(3), "turn.pdf")
    res = _run(c, tok, "pdf_rotate", [fid], {"angle": 180, "per_page": '{"2": "rotate-90", "3": "none"}'})
    from app.core import workspace as ws
    from app.mcp import registry, runner
    got = runner.resolve_inputs(registry.BY_NAME["pdf_compress"], [res["files"][0]["file_id"]],
                                ws.key_for_user_id(None))
    with fitz.open(got[0]["path"]) as d:
        assert [p.rotation for p in d] == [180, 90, 0]
    err, msg = _err(c, tok, "pdf_rotate", [fid], {"per_page": "[1, 2]"})
    assert err and "per_page" in msg


def test_api_md_lists_exactly_the_open_tools():
    """API 手冊 MCP 一節的工具清單要跟登記表一致（數字與名稱），不然文件會停在舊的數字。"""
    import re
    from app.mcp import registry
    from tools.repo_paths import public_root
    md = (public_root() / "API.md").read_text(encoding="utf-8")
    sec = md.split("## 10b. MCP", 1)[1].split("\n## ", 1)[0]
    m = re.search(r"目前開放的工具（共 (\d+) 支）：\n\n((?:\* .+\n)+)", sec)
    assert m, "找不到 MCP 工具清單"
    open_names = [t.name for t in registry.TOOLS if not t.stamp]
    assert int(m.group(1)) == len(open_names)
    assert sorted(re.findall(r"`([a-z_]+)`（", m.group(2))) == sorted(open_names)
    stamp = [t.name for t in registry.TOOLS if t.stamp]
    for n in stamp:
        assert f"`{n}`" in sec, f"AI 用印的工具 {n} 要在 MCP 一節寫出來"
