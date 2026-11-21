"""文件翻譯：產出**同格式、同版面**的檔案。

這支工具的賣點就是「排版不會跑掉」，所以驗收一路走到產出檔本身：
翻完之後**用同一支抽取器再讀一次**，確認每一段都換成了譯文，而且檔案
還打得開（能轉成 PDF）。只驗端點回 200 沒有意義。
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path

import pytest

from app.core import office_text_map as M


# ---------- 核心：抽出 / 寫回 ----------

def _minimal_docx() -> bytes:
    """手工做一份最小的 .docx（不依賴 soffice，測試才能離線跑）。"""
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>Quarterly </w:t></w:r><w:r><w:t>Report</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Revenue grew 12%.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>2026-09-02</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def test_paragraph_is_one_unit_not_one_per_run():
    """一段話被切成好幾個 run 時要**合起來**當一段翻。

    逐個 run 翻等於把半句話丟給模型（「Quarterly 」+「Report」），
    譯文一定走樣。
    """
    units, _ = M.extract_units(_minimal_docx(), ".docx")
    texts = [u.text for u in units]
    assert "Quarterly Report" in texts, f"run 沒有合併：{texts}"


def test_pure_number_or_date_is_skipped():
    units, _ = M.extract_units(_minimal_docx(), ".docx")
    assert all("2026-09-02" != u.text for u in units), "純日期不該送去翻"


def test_rebuild_replaces_text_and_keeps_the_package():
    data = _minimal_docx()
    units, state = M.extract_units(data, ".docx")
    out = M.rebuild(state, {i: f"[{u.text}]" for i, u in enumerate(units)}, units)

    again, _ = M.extract_units(out, ".docx")
    assert [u.text for u in again] == [f"[{u.text}]" for u in units]

    # zip 的其他成員要原封不動 —— 少一個檔案 Word 就開不起來
    with zipfile.ZipFile(io.BytesIO(data)) as a, zipfile.ZipFile(io.BytesIO(out)) as b:
        assert set(a.namelist()) == set(b.namelist())


@pytest.mark.parametrize("text,want", [
    ("Hello world", True),
    ("報告內容", True),
    ("12,345.67", False),
    ("2026-09-02", False),
    ("https://example.com", False),
    ("<number>", False),        # 頁碼欄位的佔位字
    ("   ", False),
    ("——", False),
])
def test_should_translate(text: str, want: bool):
    assert M.should_translate(text) is want


def test_supported_extensions_cover_the_three_families():
    for ext in (".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods",
                ".ppt", ".pptx", ".odp"):
        assert M.is_supported("x" + ext), ext
    assert not M.is_supported("x.pdf"), "PDF 不該被收 —— 版面一定跑掉"


# ---------- 端點 ----------

def test_pdf_is_rejected_with_the_reason(client, sample_pdf):
    r = client.post("/tools/doc-translate/upload",
                    files={"file": ("a.pdf", sample_pdf, "application/pdf")})
    assert r.status_code == 400
    assert "PDF" in r.text and "逐句翻譯" in r.text, \
        f"擋下來了但沒說原因與替代方案：{r.text[:200]}"


def test_upload_reports_units(client):
    r = client.post("/tools/doc-translate/upload",
                    files={"file": ("a.docx", _minimal_docx(),
                                    "application/vnd.openxmlformats-officedocument"
                                    ".wordprocessingml.document")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["units"] == 2 and d["upload_id"]


def test_start_without_llm_is_rejected(client):
    up = client.post("/tools/doc-translate/upload",
                     files={"file": ("a.docx", _minimal_docx(), "application/octet-stream")})
    assert up.status_code == 200
    r = client.post("/tools/doc-translate/start",
                    json={"upload_id": up.json()["upload_id"], "target_lang": "en"})
    # 測試環境沒有 LLM → 要明確回 503，不可以 500
    assert r.status_code == 503, r.status_code


def test_tool_page_loads(client):
    r = client.get("/tools/doc-translate/")
    assert r.status_code == 200
    assert "文件翻譯" in r.text
    assert "不支援 PDF" in r.text, "頁面要講清楚為什麼不收 PDF"


# ---------- 端到端：整個作業真的跑一次 ----------

def test_job_produces_a_translated_file(tmp_path, monkeypatch):
    """**走完整條路徑**：假的 LLM → 產出檔裡每一段都換成了譯文。

    這條測試的由來：第一版把 `_translate_one` 的回傳當成字串用（它其實回
    dict），結果作業跑到最後才炸 `'dict' object has no attribute 'strip'`，
    而使用者只看到「失敗」兩個字。單元層級的測試看不出來 ——
    只有真的把作業跑完、打開產出檔才會發現。
    """
    import importlib
    R = importlib.import_module("app.tools.doc_translate.router")

    class _FakeClient:
        """會照批次格式回覆的假模型。"""

        def text_query(self, prompt: str, **kw) -> str:
            if "⟦1⟧" in prompt:                     # 批次請求
                segs = []
                for line in prompt.splitlines():
                    m = re.match(r"^⟦(\d+)⟧(.*)$", line)
                    if m:
                        segs.append((m.group(1), m.group(2)))
                return "\n".join(f"⟦{n}⟧<{s}>" for n, s in segs)
            src = prompt.rsplit("<source>", 1)[-1].split("</source>")[0].strip()   # 單段請求
            return f"<{src}>"

    monkeypatch.setattr(R.llm_settings, "is_enabled", lambda: True)
    monkeypatch.setattr(R.llm_settings, "make_client", lambda *a, **k: _FakeClient())
    monkeypatch.setattr(R.llm_settings, "get_model_for", lambda _t: "fake-model")
    monkeypatch.setattr(R.llm_settings, "get", lambda: {"translate_concurrency": 2})
    monkeypatch.setattr(R, "_warmup_llm", lambda *a, **k: None)
    # 預覽要起 soffice，這裡不是重點（另有 §0.5 的實檔驗收）
    monkeypatch.setattr(R, "_make_preview", lambda *a, **k: (0, 0))

    upload_id = "b" * 32
    R._src_path(upload_id).write_bytes(_minimal_docx())
    R._meta_path(upload_id).write_text(
        '{"filename": "a.docx", "ext": ".docx", "work_ext": ".docx",'
        ' "units": 2, "chars": 30}', encoding="utf-8")

    class _Job:
        progress = 0.0
        message = ""
        cancelled = False
        meta: dict = {}

    job = _Job()
    job.meta = {}
    R._run_job(job, upload_id, {"filename": "a.docx", "ext": ".docx",
                                "work_ext": ".docx"},
               "en", "zh-TW", "")

    out = R._out_path(upload_id, ".docx")
    assert out.exists(), "作業跑完卻沒有產出檔"
    units, _ = M.extract_units(out.read_bytes(), ".docx")
    texts = [u.text for u in units]
    assert texts == ["<Quarterly Report>", "<Revenue grew 12%.>"], texts
    assert job.meta["download_url"].endswith(upload_id)
    assert job.meta["translated"] == 2
    # **「我的作業」的下載鈕看的是 `result_path`**，不是 meta 裡的網址。
    # 少了它，那一列會顯示「已完成」卻沒有任何東西可以下載（實際踩過）。
    assert job.result_path == out, "沒有把產出設成作業結果"
    assert job.result_filename.endswith(".docx")


def test_main_part_may_have_a_number_suffix():
    """`word/document2.xml` 也是合法的 .docx —— Word 自己會這樣寫。

    寫死 `word/document.xml` 的話，那份文件在文件翻譯會「找不到可以翻譯的
    文字」、在工作區會被判成「不支援的檔案類型」，而錯誤訊息還寫著接受 .docx，
    使用者完全看不出為什麼（使用者實際踩到）。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(
            "word/document2.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello there</w:t>'
            "</w:r></w:p></w:body></w:document>")
    units, _ = M.extract_units(buf.getvalue(), ".docx")
    assert [u.text for u in units] == ["Hello there"]


# ---------- 批次翻譯（把好幾段合成一次請求） ----------

def _R():
    import importlib
    return importlib.import_module("app.tools.doc_translate.router")


def test_batches_respect_both_limits():
    R = _R()

    class U:
        def __init__(self, text): self.text = text

    units = [U("x" * 300) for _ in range(20)]
    batches = R._make_batches(list(range(20)), units)
    for b in batches:
        assert len(b) <= R.BATCH_MAX_SEGMENTS
        assert sum(len(units[i].text) for i in b) <= R.BATCH_MAX_CHARS or len(b) == 1
    assert [i for b in batches for i in b] == list(range(20)), "順序不可以亂掉"


def test_batch_reply_is_parsed_back_in_order():
    R = _R()
    reply = "⟦1⟧甲\n⟦2⟧乙\n⟦3⟧丙"
    assert R._parse_batch_reply(reply, 3) == ["甲", "乙", "丙"]


@pytest.mark.parametrize("reply,count", [
    ("⟦1⟧甲\n⟦2⟧乙", 3),            # 少一段
    ("⟦1⟧甲\n⟦2⟧乙\n⟦4⟧丁", 3),     # 編號跳號
    ("甲\n乙\n丙", 3),               # 沒有標記
    ("", 3),
])
def test_broken_batch_reply_is_rejected(reply: str, count: int):
    """**段數對不上絕對不可以硬湊** —— 那會把 A 段的譯文寫進 B 段，
    產出的文件看起來很正常，只有讀的人會發現整份意思都錯了。"""
    assert _R()._parse_batch_reply(reply, count) is None


def test_echoed_prompt_is_not_mistaken_for_a_translation():
    """模型把 prompt 連同原文吐回來時，⟦n⟧ 標記剛好對得上段數 ——
    會「解析成功」但內容其實是原文，一個字都沒翻。"""
    R = _R()
    echo = ("只輸出翻譯結果，不要附上原文。\n⟦1⟧Hello\n⟦2⟧World")
    assert R._looks_like_echo(echo)


def test_job_uses_batching_and_keeps_segments_aligned(tmp_path, monkeypatch):
    """實際跑一次：三段各自拿到**自己的**譯文，而且只送一次請求。"""
    R = _R()
    calls = []

    class _Batch:
        def text_query(self, prompt: str, **kw) -> str:
            calls.append(prompt)
            segs = re.findall(r"^⟦(\d+)⟧(.*)$", prompt, re.M)
            return "\n".join(f"⟦{n}⟧譯[{s}]" for n, s in segs)

    monkeypatch.setattr(R.llm_settings, "is_enabled", lambda: True)
    monkeypatch.setattr(R.llm_settings, "make_client", lambda *a, **k: _Batch())
    monkeypatch.setattr(R.llm_settings, "get_model_for", lambda _t: "m")
    monkeypatch.setattr(R.llm_settings, "get", lambda: {"translate_concurrency": 2})
    monkeypatch.setattr(R, "_warmup_llm", lambda *a, **k: None)
    monkeypatch.setattr(R, "_make_preview", lambda *a, **k: (0, 0))

    doc = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body>'
        "<w:p><w:r><w:t>Alpha</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Beta</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Gamma</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)

    uid = "c" * 32
    R._src_path(uid).write_bytes(buf.getvalue())

    class _Job:
        progress = 0.0
        message = ""
        cancelled = False
        result_path = None
        result_filename = ""
        meta: dict = {}

    job = _Job()
    job.meta = {}
    R._run_job(job, uid, {"filename": "a.docx", "ext": ".docx", "work_ext": ".docx"},
               "en", "zh-TW", "")

    units, _ = M.extract_units(R._out_path(uid, ".docx").read_bytes(), ".docx")
    assert [u.text for u in units] == ["譯[Alpha]", "譯[Beta]", "譯[Gamma]"], \
        [u.text for u in units]
    assert len(calls) == 1, f"三段應該合成一次請求，實際送了 {len(calls)} 次"


def test_cancelled_job_produces_no_file(tmp_path, monkeypatch):
    """按了取消就不要產出檔案。

    一份只翻了一半的文件**比沒有更危險** —— 看起來是正常的檔案，
    實際上後半段還是原文，寄出去才會發現。
    """
    R = _R()

    class _Slow:
        def text_query(self, prompt: str, **kw) -> str:
            return "⟦1⟧甲"

    monkeypatch.setattr(R.llm_settings, "is_enabled", lambda: True)
    monkeypatch.setattr(R.llm_settings, "make_client", lambda *a, **k: _Slow())
    monkeypatch.setattr(R.llm_settings, "get_model_for", lambda _t: "m")
    monkeypatch.setattr(R.llm_settings, "get", lambda: {"translate_concurrency": 1})
    monkeypatch.setattr(R, "_warmup_llm", lambda *a, **k: None)
    monkeypatch.setattr(R, "_make_preview", lambda *a, **k: (0, 0))

    uid = "d" * 32
    R._src_path(uid).write_bytes(_minimal_docx())

    class _Job:
        progress = 0.0
        message = ""
        cancelled = True          # 一開始就處於已取消
        result_path = None
        result_filename = ""
        meta: dict = {}

    job = _Job()
    job.meta = {}
    R._run_job(job, uid, {"filename": "a.docx", "ext": ".docx", "work_ext": ".docx"},
               "en", "zh-TW", "")
    assert not R._out_path(uid, ".docx").exists(), "取消了還是產出了檔案"
    assert job.result_path is None


def test_line_breaks_inside_a_cell_survive(tmp_path, monkeypatch):
    """儲存格裡的換行要保留。

    試算表的一格常是「一句話 + 好幾個項目符號」。整格丟給模型會回成一整行，
    條列全擠成一團（使用者拿 PCI DSS 責任矩陣實測回報）。而且批次的格式是
    「一段一行」，多行的段落本身就會把批次結構弄亂。
    """
    R = _R()

    class _PerLine:
        def text_query(self, prompt: str, **kw) -> str:
            segs = re.findall(r"^⟦(\d+)⟧(.*)$", prompt, re.M)
            return "\n".join(f"⟦{n}⟧譯[{s}]" for n, s in segs)

    monkeypatch.setattr(R.llm_settings, "is_enabled", lambda: True)
    monkeypatch.setattr(R.llm_settings, "make_client", lambda *a, **k: _PerLine())
    monkeypatch.setattr(R.llm_settings, "get_model_for", lambda _t: "m")
    monkeypatch.setattr(R.llm_settings, "get", lambda: {"translate_concurrency": 1})
    monkeypatch.setattr(R, "_warmup_llm", lambda *a, **k: None)
    monkeypatch.setattr(R, "_make_preview", lambda *a, **k: (0, 0))

    # xlsx：一個 <si> 裡帶換行的儲存格
    cell = "All policies are:\n- Documented.\n- In use."
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<si><t xml:space="preserve">{cell}</t></si></sst>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", shared)

    uid = "e" * 32
    R._src_path(uid).write_bytes(buf.getvalue())

    class _Job:
        progress = 0.0
        message = ""
        cancelled = False
        result_path = None
        result_filename = ""
        meta: dict = {}

    job = _Job()
    job.meta = {}
    R._run_job(job, uid, {"filename": "a.xlsx", "ext": ".xlsx", "work_ext": ".xlsx"},
               "en", "zh-TW", "")

    units, _ = M.extract_units(R._out_path(uid, ".xlsx").read_bytes(), ".xlsx")
    assert len(units) == 1
    got = units[0].text
    assert got.count("\n") == 2, f"換行被吃掉了：{got!r}"
    assert got == "譯[All policies are:]\n譯[- Documented.]\n譯[- In use.]", got


def test_per_line_colour_survives_the_translation(tmp_path, monkeypatch):
    """一格裡「說明文字 + 換行 + 紅色斜體補充」，翻完那行還是紅色斜體。

    使用者實測回報：「有些字顏色 翻譯後失去了」。原本譯文全部寫進第一個
    文字節點，整格就統一成第一個 run 的樣式 —— 那行「這是新增要求」的紅字
    提示變成黑色正體。**沒有錯誤訊息、版面也沒跑掉**，只有對照原稿才看得出來。
    """
    R = _R()

    class _PerLine:
        def text_query(self, prompt: str, **kw) -> str:
            segs = re.findall(r"^⟦(\d+)⟧(.*)$", prompt, re.M)
            return "\n".join(f"⟦{n}⟧譯[{s}]" for n, s in segs)

    monkeypatch.setattr(R.llm_settings, "is_enabled", lambda: True)
    monkeypatch.setattr(R.llm_settings, "make_client", lambda *a, **k: _PerLine())
    monkeypatch.setattr(R.llm_settings, "get_model_for", lambda _t: "m")
    monkeypatch.setattr(R.llm_settings, "get", lambda: {"translate_concurrency": 1})
    monkeypatch.setattr(R, "_warmup_llm", lambda *a, **k: None)
    monkeypatch.setattr(R, "_make_preview", lambda *a, **k: (0, 0))

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    shared = (
        f'<sst xmlns="{ns}"><si>'
        '<r><t xml:space="preserve">Roles are documented.\n</t></r>'
        '<r><rPr><i/><color rgb="FFFF0000"/></rPr>'
        '<t>New requirement - effective immediately</t></r>'
        '</si></sst>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", shared)

    uid = "c" * 32
    R._src_path(uid).write_bytes(buf.getvalue())

    class _Job:
        progress = 0.0
        message = ""
        cancelled = False
        result_path = None
        result_filename = ""

    job = _Job()
    job.meta = {}
    R._run_job(job, uid, {"filename": "a.xlsx", "ext": ".xlsx", "work_ext": ".xlsx"},
               "en", "zh-TW", "")

    out = R._out_path(uid, ".xlsx").read_bytes()
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        xml = z.read("xl/sharedStrings.xml").decode("utf-8")

    root = ET.fromstring(xml)
    runs = list(root.iter(f"{{{ns}}}r"))
    assert len(runs) == 2, xml
    texts = [(r.find(f"{{{ns}}}t").text or "") for r in runs]
    # 兩行各自回到自己的 run（第二行帶著分隔的換行）
    assert texts[0] == "譯[Roles are documented.]", texts
    assert texts[1] == "\n譯[New requirement - effective immediately]", texts
    # **紅色斜體還在，而且掛在放譯文的那個 run 上**
    rpr = runs[1].find(f"{{{ns}}}rPr")
    assert rpr is not None and rpr.find(f"{{{ns}}}color") is not None, xml
    assert rpr.find(f"{{{ns}}}color").get("rgb") == "FFFF0000"
    # 整格讀回來的文字仍是兩行
    units, _ = M.extract_units(out, ".xlsx")
    assert units[0].text == (
        "譯[Roles are documented.]\n譯[New requirement - effective immediately]")


def test_mismatched_batch_is_halved_not_dropped_to_one_by_one(tmp_path, monkeypatch):
    """模型漏段時把那一批**對切重試**，不要一路退回逐行。

    批次調大之後，一次漏段原本要賠上幾十次單行請求 —— 比不合併還慢
    （使用者實測 343 段送了 305 次請求，回報「太慢了」）。
    """
    R = _R()

    class _PickyClient:
        """一次超過三段就故意少回一段（模擬模型漏段）。"""
        def __init__(self) -> None:
            self.calls: list[int] = []

        def text_query(self, prompt: str, **kw) -> str:
            segs = re.findall(r"^⟦(\d+)⟧(.*)$", prompt, re.M)
            self.calls.append(len(segs))
            if len(segs) > 3:
                segs = segs[:-1]          # 少一段 → 解析必定失敗
            return "\n".join(f"⟦{n}⟧譯[{s}]" for n, s in segs)

    client = _PickyClient()
    monkeypatch.setattr(R.llm_settings, "is_enabled", lambda: True)
    monkeypatch.setattr(R.llm_settings, "make_client", lambda *a, **k: client)
    monkeypatch.setattr(R.llm_settings, "get_model_for", lambda _t: "m")
    monkeypatch.setattr(R.llm_settings, "get", lambda: {
        "translate_concurrency": 1, "doctr_batch_segments": 12,
        "doctr_batch_chars": 20000})
    monkeypatch.setattr(R, "_warmup_llm", lambda *a, **k: None)
    monkeypatch.setattr(R, "_make_preview", lambda *a, **k: (0, 0))

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    cells = [f"Sentence number {i} here." for i in range(12)]
    shared = (f'<sst xmlns="{ns}">'
              + "".join(f"<si><t>{c}</t></si>" for c in cells)
              + "</sst>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", shared)

    uid = "d" * 32
    R._src_path(uid).write_bytes(buf.getvalue())

    class _Job:
        progress = 0.0
        message = ""
        cancelled = False
        result_path = None
        result_filename = ""

    job = _Job()
    job.meta = {}
    R._run_job(job, uid, {"filename": "a.xlsx", "ext": ".xlsx", "work_ext": ".xlsx"},
               "en", "zh-TW", "")

    # 12 → 6+6 → 3+3+3+3：只有四次成功的請求，不是十二次單行
    assert job.meta["llm_requests"] == 4, (job.meta, client.calls)
    assert job.meta["batch_fallbacks"] == 3, job.meta
    assert client.calls == [12, 6, 3, 3, 6, 3, 3], client.calls

    units, _ = M.extract_units(R._out_path(uid, ".xlsx").read_bytes(), ".xlsx")
    assert [u.text for u in units] == [f"譯[{c}]" for c in cells]


def test_blank_line_inside_a_cell_is_not_lost():
    """空白行會切出一個「分不到節點」的組 —— 那時要退回舊做法，一行都不能少。

    連續兩個換行（一格裡空一行）會讓中間那一行沒有任何 run 可以寫。硬跑的話
    產出的檔案會**少一行**，而且沒有任何錯誤訊息。
    """
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    shared = (f'<sst xmlns="{ns}"><si>'
              '<r><t xml:space="preserve">first\n</t></r>'
              '<r><t xml:space="preserve">\nthird</t></r>'
              '</si></sst>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", shared)

    units, state = M.extract_units(buf.getvalue(), ".xlsx")
    assert units[0].text == "first\n\nthird"
    out = M.rebuild(state, {0: "第一\n\n第三"}, units, target_lang="zh-TW")
    again, _ = M.extract_units(out, ".xlsx")
    assert again[0].text == "第一\n\n第三", again[0].text


def test_progress_counts_batches_not_just_segments(tmp_path, monkeypatch):
    """進度列要講「已完成 N/M 批」。

    合併批次之後進度只在整批回來時才跳 —— 一批 40 行、四條並行，畫面上就是
    跳一大格然後很久不動，使用者會以為卡住（實測回報「太慢了」，但同時量到的
    每段耗時其實沒有變）。分母用**原本切好的批數**：對切重試時分母不可以變大，
    會動的分母比沒有進度列更糟。
    """
    R = _R()

    class _Client:
        def text_query(self, prompt: str, **kw) -> str:
            segs = re.findall(r"^⟦(\d+)⟧(.*)$", prompt, re.M)
            return "\n".join(f"⟦{n}⟧譯[{s}]" for n, s in segs)

    monkeypatch.setattr(R.llm_settings, "is_enabled", lambda: True)
    monkeypatch.setattr(R.llm_settings, "make_client", lambda *a, **k: _Client())
    monkeypatch.setattr(R.llm_settings, "get_model_for", lambda _t: "m")
    monkeypatch.setattr(R.llm_settings, "get", lambda: {
        "translate_concurrency": 1, "doctr_batch_segments": 2,
        "doctr_batch_chars": 20000})
    monkeypatch.setattr(R, "_warmup_llm", lambda *a, **k: None)
    monkeypatch.setattr(R, "_make_preview", lambda *a, **k: (0, 0))

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    cells = [f"Sentence number {i} here." for i in range(6)]
    shared = (f'<sst xmlns="{ns}">'
              + "".join(f"<si><t>{c}</t></si>" for c in cells) + "</sst>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", shared)

    uid = "f" * 32
    R._src_path(uid).write_bytes(buf.getvalue())

    seen: list[str] = []

    class _Job:
        progress = 0.0
        cancelled = False
        result_path = None
        result_filename = ""

        def __init__(self) -> None:
            self.meta: dict = {}
            self._msg = ""

        @property
        def message(self) -> str:
            return self._msg

        @message.setter
        def message(self, v: str) -> None:
            self._msg = v
            seen.append(v)

    job = _Job()
    R._run_job(job, uid, {"filename": "a.xlsx", "ext": ".xlsx", "work_ext": ".xlsx"},
               "en", "zh-TW", "")

    batch_msgs = [m for m in seen if "批" in m and m.startswith("翻譯中")]
    assert batch_msgs, seen
    # 6 段 / 每批 2 段 = 3 批，分母全程是 3
    assert all("/3 批" in m for m in batch_msgs), batch_msgs
    assert "已完成 3/3 批" in batch_msgs[-1], batch_msgs
    assert "（約 6/6 段）" in batch_msgs[-1], batch_msgs[-1]


def test_trailing_newline_run_does_not_defeat_the_colour_fix():
    """最後一個 run 只裝一個換行時，紅字那行還是要留在紅色 run 上。

    真實檔案很常見這種收尾：`<r><rPr 紅色/><t>\\n</t></r>`。那個換行會切出一個
    「空白行」的組，而它前後的節點都歸給別組 —— 這一組因此一個節點都沒有。
    早期版本遇到這種就整段退回「全部寫進第一個節點」，於是**修法明明在，
    客戶檔案的紅字還是變黑的**（使用者實測回報「有字顏色沒出來啊」）。
    """
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    shared = (
        f'<sst xmlns="{ns}"><si>'
        '<r><rPr><b/></rPr><t>1.1</t></r>'
        '<r><t xml:space="preserve"> Roles are documented.\n</t></r>'
        '<r><rPr><i/><color rgb="FFC00000"/></rPr>'
        '<t>New requirement - effective immediately</t></r>'
        '<r><rPr><i/><color rgb="FFFF0000"/></rPr>'
        '<t xml:space="preserve">\n</t></r>'
        '</si></sst>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", shared)

    units, state = M.extract_units(buf.getvalue(), ".xlsx")
    assert units[0].text == "1.1 Roles are documented.\n新要求前的原文\n".replace(
        "新要求前的原文", "New requirement - effective immediately")

    out = M.rebuild(state, {0: "1.1 角色已記錄。\n新要求 - 立即生效\n"}, units,
                    target_lang="zh-TW")

    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(out)).read("xl/sharedStrings.xml"))
    by_colour = {}
    for r in root.iter(f"{{{ns}}}r"):
        rpr = r.find(f"{{{ns}}}rPr")
        col = rpr.find(f"{{{ns}}}color") if rpr is not None else None
        key = col.get("rgb") if col is not None else None
        t = r.find(f"{{{ns}}}t")
        if t is not None and (t.text or "").strip():
            by_colour[key] = (t.text or "")
    # 紅字那行落在紅色 run 上，不是被塞進第一個（粗體黑字）run
    assert "FFC00000" in by_colour, by_colour
    assert "新要求 - 立即生效" in by_colour["FFC00000"], by_colour
    assert "角色已記錄" not in by_colour["FFC00000"], by_colour
    # 而且整格讀回來一個字都沒少
    again, _ = M.extract_units(out, ".xlsx")
    assert again[0].text == "1.1 角色已記錄。\n新要求 - 立即生效\n", again[0].text


def test_stray_byte_token_in_the_marker_is_tolerated():
    """`⟦<0xC2>5⟧` 這種標記也要認得。

    模型遇到不成字的位元組時，tokenizer 會把它原樣吐成 `<0xC2>` 這種**字面文字**。
    實測 gemma4:26b 翻 40 段的批次**每一次都會出現一次**，害那一段解析不到、
    整批被判定漏段 → 對切重試 → 白跑一次生成。這正是正式環境上兩成批次「漏段」
    的真正原因：譯文其實好端端在那裡。
    """
    R = _R()
    reply = "\n".join(
        f"⟦{i}⟧譯文 {i}" if i != 5 else f"⟦<0xC2>{i}⟧譯文 {i}"
        for i in range(1, 6))
    got = R._parse_batch_reply(reply, 5)
    assert got is not None, "帶位元組 token 的標記應該要認得"
    assert got[4] == "譯文 5", got
    # 標記裡有空白也要認
    assert R._parse_batch_reply("⟦ 1 ⟧甲\n⟦2⟧乙", 2) == ["甲", "乙"]
    # 但段數真的對不上時仍然要退回 None（不可以硬湊）
    assert R._parse_batch_reply("⟦1⟧甲", 2) is None


def test_spreadsheet_preview_fits_all_columns():
    """試算表的預覽要看得到**所有欄位**，不能只有第一欄。

    超出紙寬的欄位會被 Calc 丟到後面好幾頁（實測一份四欄的責任矩陣：72 頁，
    而前 6 頁全部只有 A 欄）。預覽只取前幾頁 —— 使用者於是看到「只有第一欄」，
    而這個工具的預覽正是要證明「版面沒跑掉」，看不到其他欄就證明不了任何事。
    """
    R = _R()
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = (f'<worksheet xmlns="{ns}"><dimension ref="A1:D2"/>'
             '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>甲</t></is></c>'
             '</row></sheetData></worksheet>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", sheet)

    out = R._fit_to_width(buf.getvalue())
    got = zipfile.ZipFile(io.BytesIO(out)).read("xl/worksheets/sheet1.xml").decode()
    assert 'fitToPage="1"' in got, got
    assert 'fitToWidth="1"' in got and 'fitToHeight="0"' in got, got
    # 其他檔案不可以被動到
    assert zipfile.ZipFile(io.BytesIO(out)).read("[Content_Types].xml") == b"<Types/>"


def test_fit_to_width_leaves_a_broken_file_alone():
    """改不動就原樣回傳 —— 預覽是附屬品，不可以因為它讓整個作業失敗。"""
    R = _R()
    assert R._fit_to_width(b"not a zip") == b"not a zip"


def test_start_records_the_upload_id_before_the_job_runs(client, monkeypatch):
    """`upload_id` 一送出就要在作業的 meta 裡 —— 清理暫存檔時靠它認出「排隊中 / 跑到一半的作業的檔案」。
    等跑完才記的話，排隊超過暫存保留時間（預設 2 小時）時原檔與歸屬紀錄會先被清掉（2026-10-08 盤點抓到）。"""
    import importlib
    R = importlib.import_module("app.tools.doc_translate.router")
    seen = {}

    class _Job:
        id = "0" * 32
        meta: dict = {}

    def fake_submit(tool, fn, meta=None, request=None, **kw):
        seen.update(meta or {})
        j = _Job()
        j.meta = dict(meta or {})
        return j

    monkeypatch.setattr(R.llm_settings, "is_enabled", lambda: True)
    monkeypatch.setattr(R.job_manager, "submit", fake_submit)
    up = client.post("/tools/doc-translate/upload",
                     files={"file": ("a.docx", _minimal_docx(), "application/octet-stream")})
    uid = up.json()["upload_id"]
    r = client.post("/tools/doc-translate/start", json={"upload_id": uid, "target_lang": "en"})
    assert r.status_code == 200, r.text
    assert seen.get("upload_id") == uid, seen
