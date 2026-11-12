"""對外 API 要做得到網頁做得到的事（v1.17.37）。

MCP 走的是對外 API —— 網頁有、API 沒有的選項，AI 就永遠用不到（PDF 密碼解除的高鐵模式就是這樣漏的）。
這裡分兩件事：

1. **結構檢查**：每一支開放給 MCP 的工具，網頁端點收的欄位要嘛 API 也收，要嘛列在 `WEB_ONLY`
   並寫明為什麼（預覽、檔名、LLM、介面暫存）。新加一個網頁選項卻忘了 API → 這裡紅。
   同一支檢查也比對 MCP 參數與 API 欄位（MCP 少開的要列在 `MCP_SKIPS`）。
2. **這次補上的選項真的有作用**：逐支實際呼叫、打開結果檢查。
"""
from __future__ import annotations

import json

import fitz
import pytest

#: 網頁有、API 刻意不收的欄位 → 理由
WEB_ONLY: dict[str, dict[str, str]] = {
    "pdf-pageno": {"large": "預覽圖大小", "page": "預覽哪一頁"},
    "pdf-wordcount": {"llm_summarize": "LLM（MCP 不用）", "text": "網頁貼文字的另一支端點；API 收檔案"},
    "markdown-to-doc": {"formats": "網頁一次產多種格式；API 一次一種（MCP 要幾種就叫幾次）"},
    "pdf-watermark": {"asset_id": "圖片浮水印取自資產庫（有權限與管制），不經 API / MCP",
                      "temp_asset_file": "同上，網頁暫存上傳的浮水印圖", "params": "網頁把設定打包成 JSON；API 逐欄收",
                      "page": "預覽哪一頁", "index": "批次上傳的序號"},
    "pdf-decrypt": {"from_tool": "別的工具上傳框解密用（記錄來源工具）"},
    "pdf-nup": {"opts": "網頁把設定打包成 JSON；API 逐欄收"},
    "pdf-pages": {"filename": "網頁自訂輸出檔名", "order": "就是 API 的 spec（mode=reorder）"},
    "pdf-page-size": {"page_no": "預覽哪一頁", "filename": "網頁自訂輸出檔名"},
    "pdf-bookmark": {"text": "網頁貼上清單文字轉書籤；API 直接收 bookmarks JSON", "page_no": "預覽哪一頁",
                     "filename": "網頁自訂輸出檔名"},
    "pdf-ocr": {"langs": "網頁的多選；API 的 lang 用 + 串接", "use_llm": "LLM（MCP 不用）",
                "use_llm_align": "LLM", "use_llm_direct": "LLM", "use_llm_full": "LLM", "use_llm_vision": "LLM"},
    "doc-diff": {"llm_summarize": "LLM（MCP 不用）"},
    "doc-straighten": {"page": "預覽", "quad": "預覽時手動拉角", "rotate": "預覽時手動旋轉",
                       "out_name": "網頁自訂輸出檔名", "overrides": "網頁逐頁手動拉角的結果"},
    "pdf-border": {"large": "預覽圖大小", "page": "預覽哪一頁", "out_name": "網頁自訂輸出檔名"},
    "pdf-annotations": {"llm_group": "LLM（MCP 不用）", "authors": "網頁匯出時篩選；API 回全部、AI 自己篩",
                        "types": "同上", "group_by": "網頁匯出審閱表的分組", "fmt": "網頁匯出待辦的檔案格式"},
    "doc-deident": {"llm_augment": "LLM（MCP 不用）"},
    "pdf-fill": {"use_llm_review": "LLM 校對（MCP 不用）", "llm_max_rounds": "LLM 校對", "max_rounds": "LLM 校對"},
    "text-deident": {"filename": "網頁把結果存成檔案下載；API 直接回文字", "text": "同上"},
    "submission-check": {"case_id": "網頁在既有案件追加檔案；API 每次建新案件",
                         "finding_key": "人工覆核（網頁）", "reason": "人工覆核", "verdict": "人工覆核",
                         "name": "自己公司清單（網頁設定）", "tax_id": "同上", "address": "同上",
                         "aliases": "同上", "type": "同上", "note": "同上"},
}

#: API 有、MCP 刻意不開的欄位 → 理由
MCP_SKIPS: dict[str, dict[str, str]] = {
    "image_to_pdf": {"filename": "結果檔名由 MCP 依來源檔決定"},
    "pdf_to_markdown": {"image_format": "MCP 一律不帶圖片（include_images 強制關閉）"},
    "scan_merge": {"filename": "結果檔名由 MCP 依來源檔決定"},
}

_IGNORE = {"file", "files", "upload_id", "file_a", "file_b"}


def _all_routes():
    from app.main import app
    from tools.route_index import iter_routes
    return list(iter_routes(app))


def _post_routes():
    return [rt for rt in _all_routes() if getattr(rt, "methods", None) and "POST" in rt.methods]


def _fields(rt) -> set[str]:
    return {p.alias or p.name for p in rt.dependant.body_params}


def test_web_options_reach_the_public_api_and_mcp():
    from app.mcp import registry
    routes = _post_routes()
    problems = []
    for t in registry.TOOLS:
        if t.method != "POST":
            assert any(getattr(rt, "path", "") == t.path and t.method in (getattr(rt, "methods", None) or set())
                       for rt in _all_routes()), t.path
            continue
        api = next((rt for rt in routes if rt.path == t.path), None)
        assert api is not None, t.path
        api_fields = _fields(api)
        if t.body != "json":
            miss = api_fields - set(t.params) - set(t.force) - {t.file_field, *t.file_fields}
            miss -= set(MCP_SKIPS.get(t.name, {}))
            if miss:
                problems.append(f"MCP 沒開 {t.name}: {sorted(miss)}")
        if t.stamp:
            # AI 用印的端點刻意比網頁窄（只收資產庫裡經 AI 授權的章；不收上傳圖、文字章、日期章），不比
            continue
        for rt in routes:
            if rt.path.startswith(f"/tools/{t.tool_id}/") and rt.path != t.path:
                extra = _fields(rt) - api_fields - _IGNORE - set(WEB_ONLY.get(t.tool_id, {}))
                if extra:
                    problems.append(f"網頁有、API 沒有 {t.tool_id} {rt.path}: {sorted(extra)}")
    assert not problems, "\n".join(problems)


def test_allow_lists_have_no_stale_entries():
    """理由清單裡的欄位要真的存在，不然清單會一直變長、沒有人知道哪些已經不需要。"""
    from app.mcp import registry
    routes = _post_routes()
    for tool_id, fields in WEB_ONLY.items():
        web = set().union(*[_fields(rt) for rt in routes if rt.path.startswith(f"/tools/{tool_id}/")])
        assert set(fields) <= web, (tool_id, set(fields) - web)
    for name, fields in MCP_SKIPS.items():
        t = registry.BY_NAME[name]
        api = next(rt for rt in routes if rt.path == t.path)
        assert set(fields) <= _fields(api), (name, set(fields) - _fields(api))


# --------------------------------------------------------------------------- 補上的選項真的有作用

def _pdf(n=3, text="page") -> bytes:
    doc = fitz.open()
    for i in range(n):
        doc.new_page().insert_text((72, 72), f"{text} {i + 1}")
    out = doc.tobytes()
    doc.close()
    return out


def _call(client, path, data=None, files=None, name="in.pdf", body=None):
    return client.post(path, files=files or {"file": (name, body or _pdf(), "application/pdf")}, data=data or {})


def _open(r):
    assert r.status_code == 200, r.text[:300]
    return fitz.open(stream=r.content, filetype="pdf")


def test_rotate_mode_and_per_page(client, auth_off):
    r = _call(client, "/tools/pdf-rotate/api/pdf-rotate",
              {"angle": "180", "per_page": json.dumps({"2": "rotate-90", "3": "none"})})
    with _open(r) as d:
        assert [p.rotation for p in d] == [180, 90, 0]
    r = _call(client, "/tools/pdf-rotate/api/pdf-rotate", {"mode": "flip-h", "pages": "1"})
    with _open(r) as d:
        assert d.page_count == 3
    assert _call(client, "/tools/pdf-rotate/api/pdf-rotate", {"mode": "spin"}).status_code == 400
    assert _call(client, "/tools/pdf-rotate/api/pdf-rotate", {"per_page": "[1]"}).status_code == 400


def test_page_numbers_only_in_a_range(client, auth_off):
    r = _call(client, "/tools/pdf-pageno/api/pdf-pageno", {"from_page": "2", "fmt": "#{n}/{N}"})
    with _open(r) as d:
        texts = [p.get_text() for p in d]
    assert "#" not in texts[0] and "#1/2" in texts[1] and "#2/2" in texts[2], texts


def test_watermark_page_mode(client, auth_off):
    r = _call(client, "/tools/pdf-watermark/api/pdf-watermark",
              {"text": "MARKX", "page_mode": "last", "mode": "single"})
    with _open(r) as d:
        assert [bool(p.get_images()) for p in d] == [False, False, True]     # 浮水印畫成圖片
    assert _call(client, "/tools/pdf-watermark/api/pdf-watermark",
                 {"text": "x", "page_mode": "odd"}).status_code == 400


def test_encrypt_all_permissions(client, auth_off):
    def perms(extra):
        r = _call(client, "/tools/pdf-encrypt/api/pdf-encrypt", {"user_pw": "u1", "owner_pw": "o1", **extra})
        with _open(r) as d:
            assert d.authenticate("u1")
            return d.permissions
    base = perms({})
    more = perms({"allow_modify": "true", "allow_annotate": "true", "allow_form": "true",
                  "allow_assemble": "true", "allow_print_hq": "true"})
    for flag in (fitz.PDF_PERM_MODIFY, fitz.PDF_PERM_ANNOTATE, fitz.PDF_PERM_FORM, fitz.PDF_PERM_ASSEMBLE,
                 fitz.PDF_PERM_PRINT_HQ):
        assert not base & flag and more & flag, flag
    assert base & fitz.PDF_PERM_PRINT and base & fitz.PDF_PERM_ACCESSIBILITY, "預設跟原本一樣：列印＋無障礙"


def test_bookmark_toc_position(client, auth_off):
    bm = json.dumps([{"title": "Intro", "page": 1, "level": 1}, {"title": "End", "page": 3, "level": 1}])
    r = client.post("/tools/pdf-bookmark/api/pdf-bookmark",
                    files=[("files", ("a.pdf", _pdf(), "application/pdf"))],
                    data={"bookmarks": bm, "toc_page": "true", "toc_title": "Contents", "toc_at": "2",
                          "toc_dots": "false"})
    with _open(r) as d:
        assert d.page_count == 4
        assert "Contents" in d[1].get_text() and "Contents" not in d[0].get_text()


def test_straighten_output_size(client, auth_off):
    r = _call(client, "/tools/doc-straighten/api/doc-straighten",
              {"dpi": "150", "detect_quad": "false", "enhance": "false", "size_w": "100", "size_h": "150",
               "size_unit": "mm"}, body=_pdf(1))
    with _open(r) as d:
        w, h = d[0].rect.width * 25.4 / 72, d[0].rect.height * 25.4 / 72
    assert abs(w - 100) < 2 and abs(h - 150) < 2, (w, h)


def test_deident_custom_rule(client, auth_off):
    body = _pdf(1, "code PROJ-4821 here")
    r = _call(client, "/tools/doc-deident/api/doc-deident",
              {"mode": "redact", "types": "email", "custom": "代號|PROJ-\\d+"}, body=body)
    with _open(r) as d:
        assert "PROJ-4821" not in d[0].get_text()
    assert _call(client, "/tools/doc-deident/api/doc-deident",
                 {"custom": "壞|([a-"}, body=body).status_code == 400


def test_nup_options(client, auth_off):
    r = _call(client, "/tools/pdf-nup/api/pdf-nup",
              {"cols": "2", "rows": "1", "paper": "custom", "paper_w_mm": "200", "paper_h_mm": "100",
               "show_page_num": "true", "page_num_format": "P{n}", "show_border": "true", "gap_mm": "3",
               "direction": "ttb", "margin_mm": "500", "gap_mm": "500"}, body=_pdf(4))
    with _open(r) as d:
        assert d.page_count == 2
        w, h = sorted((d[0].rect.width * 25.4 / 72, d[0].rect.height * 25.4 / 72))
        assert abs(w - 100) < 1 and abs(h - 200) < 1, (w, h)
    for bad in ({"direction": "diag"}, {"page_align": "middle"}, {"border_color": "red"}):
        assert _call(client, "/tools/pdf-nup/api/pdf-nup", bad).status_code == 400, bad
