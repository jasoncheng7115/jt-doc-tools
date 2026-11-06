"""從字形反查還原文字 —— 對付壞掉的 ToUnicode 對照表。

客戶回報（2026-08-26）：PDF 編輯器點文件上原本的中文，文字框裡整排變成
`••••••`。那類 PDF 的 ToUnicode 把每個字碼都對應到同一個符號，**畫面正常、
抽出來的字不對**。

重點是**不該用 OCR 解**：PDF 記著每個字用了哪個字形編號，內嵌字型檔裡就有
字形編號 ↔ Unicode 的對照表，反查回去是精確的。OCR 會認錯字、要幾秒鐘，
只留給「字型沒內嵌 / 沒有 cmap / 真的是掃描件」。
"""
from __future__ import annotations

import re

import fitz
import pytest

from app.core.glyph_text import recover_text_in_bbox

SAMPLE = "測試單位：範例文字"


def _make_broken_cmap_pdf(path, text=SAMPLE, size=20):
    from app.core.font_catalog import best_cjk_path, embeddable_font

    _cjk = best_cjk_path("sans", "traditional")
    if not _cjk:
        # `best_cjk_path` 沒有字型時回 None，直接解包會是 TypeError 而不是
        # 「這台沒有中文字型」—— 缺系統相依要**明確跳過並寫原因**
        # （2026-09-06 CI 的核心 job 沒裝字型，一次紅了 10 條）。
        pytest.skip("這台機器沒有中文字型")
    fpath, idx = _cjk
    if not fpath:
        pytest.skip("這台機器沒有 CJK 字型")
    fontfile, fontbuffer = embeddable_font(str(fpath), idx, text)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="F0", fontfile=fontfile, fontbuffer=fontbuffer)
    page.insert_text((72, 120), text, fontname="F0", fontsize=size)
    raw = doc.tobytes()
    doc.close()

    doc = fitz.open(stream=raw, filetype="pdf")
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=False) or ""
        except Exception:
            continue
        m = re.search(r"/ToUnicode (\d+) 0 R", obj)
        if not m:
            continue
        tu = int(m.group(1))
        cmap = doc.xref_stream(tu).decode("latin-1", "replace")
        cmap = re.sub(r"(<[0-9A-Fa-f]{4}>)\s*<[0-9A-Fa-f]{4,}>", r"\1 <2022>", cmap)
        doc.update_stream(tu, cmap.encode("latin-1", "replace"))
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="module")
def broken_pdf(tmp_path_factory):
    return _make_broken_cmap_pdf(tmp_path_factory.mktemp("glyph") / "broken.pdf")


def test_recovers_the_exact_original_text(broken_pdf):
    """精確還原 —— 一個字都不能錯（這是它勝過 OCR 的整個理由）。

    注意用**整行**的框：全形冒號那個字碼落在別的對照表寫法裡，會把 span
    切成兩段，逐 span 只會拿到半句（那半句本身是對的）。
    """
    doc = fitz.open(str(broken_pdf))
    page = doc[0]
    line = next(line for b in page.get_text("dict")["blocks"]
                for line in b.get("lines", []))
    assert "•" in "".join(s["text"] for s in line["spans"]), \
        "重現檔沒做成，擷取結果不是圓點"
    assert recover_text_in_bbox(page, line["bbox"]) == SAMPLE
    doc.close()


def test_healthy_pdf_recovers_the_same_text(tmp_path):
    """對照組：對正常 PDF 反查，結果要跟正常擷取一致（不會反而弄壞）。"""
    from app.core.font_catalog import best_cjk_path, embeddable_font

    _cjk = best_cjk_path("sans", "traditional")
    if not _cjk:
        # `best_cjk_path` 沒有字型時回 None，直接解包會是 TypeError 而不是
        # 「這台沒有中文字型」—— 缺系統相依要**明確跳過並寫原因**
        # （2026-09-06 CI 的核心 job 沒裝字型，一次紅了 10 條）。
        pytest.skip("這台機器沒有中文字型")
    fpath, idx = _cjk
    if not fpath:
        pytest.skip("這台機器沒有 CJK 字型")
    fontfile, fontbuffer = embeddable_font(str(fpath), idx, SAMPLE)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="F0", fontfile=fontfile, fontbuffer=fontbuffer)
    page.insert_text((72, 120), SAMPLE, fontname="F0", fontsize=20)
    out = tmp_path / "ok.pdf"
    doc.save(str(out))
    doc.close()

    doc = fitz.open(str(out))
    page = doc[0]
    line = next(line for b in page.get_text("dict")["blocks"]
                for line in b.get("lines", []))
    assert "".join(s["text"] for s in line["spans"]) == SAMPLE   # 本來就是好的
    assert recover_text_in_bbox(page, line["bbox"]) == SAMPLE
    doc.close()


def test_no_embedded_font_returns_empty(tmp_path):
    """字型沒內嵌就查不到 —— 要安靜回空字串讓呼叫端退回 OCR，不可以丟例外。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 120), "hello", fontsize=20)   # 標準 14 字型，不內嵌
    out = tmp_path / "nofont.pdf"
    doc.save(str(out))
    doc.close()

    doc = fitz.open(str(out))
    assert recover_text_in_bbox(doc[0], (60, 100, 200, 130)) == ""
    doc.close()


def test_bad_bbox_is_safe(broken_pdf):
    doc = fitz.open(str(broken_pdf))
    assert recover_text_in_bbox(doc[0], ("x", None, 1, 2)) == ""
    assert recover_text_in_bbox(doc[0], (0, 0, 1, 1)) == ""     # 空白處
    doc.close()


def test_partial_lookup_is_all_or_nothing(broken_pdf, monkeypatch):
    """有字查不到就整段放棄 —— 半段正確半段問號比整段失敗更糟，
    使用者會以為那就是原文（缺字方框那次的教訓）。"""
    import app.core.glyph_text as gt

    doc = fitz.open(str(broken_pdf))
    page = doc[0]
    line = next(line for b in page.get_text("dict")["blocks"]
                for line in b.get("lines", []))
    # 拿掉的必須是**這段文字真的用到**的字形 —— 隨便刪一半刪不到重點
    # （子集字型只留 117 個字形，用到的都排在後面）。
    used = [c[1] for c in page.get_texttrace()[0]["chars"]]
    assert used, "沒讀到字形編號，測試前提不成立"
    victim = used[len(used) // 2]

    real = gt._gid_to_unicode

    def holey(buf):
        table = dict(real(buf))
        table.pop(victim, None)
        return table

    gt._gid_map_cache.clear()
    monkeypatch.setattr(gt, "_gid_to_unicode", holey)
    got = gt.recover_text_in_bbox(page, line["bbox"])
    assert got == "", f"少一個字就要整段放棄，不可以吐 {got!r}"
    doc.close()
    gt._gid_map_cache.clear()


def test_editor_prefers_glyph_recovery_over_ocr(broken_pdf, monkeypatch, tmp_path):
    """接線：編輯器要先反查字形，**不要**去叫 OCR。"""
    import importlib

    monkeypatch.setenv("JTDT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JTDT_CSRF_DISABLE", "1")

    mod = importlib.import_module("app.tools.pdf_editor.router")
    called = []
    monkeypatch.setattr(mod, "_ocr_bbox",
                        lambda *a, **k: called.append("ocr") or "OCR 猜的結果")

    from fastapi.testclient import TestClient
    import app.main as app_main
    client = TestClient(app_main.app)

    uid = client.post("/tools/pdf-editor/load",
                      files={"file": ("t.pdf", broken_pdf.read_bytes(),
                                      "application/pdf")}).json()["upload_id"]
    data = client.post("/tools/pdf-editor/detect-objects",
                       json={"upload_id": uid, "page": 0, "x": 110, "y": 112}).json()

    assert data.get("kind") == "text", f"沒點到文字：{data}"
    got = data.get("text") or ""
    # 點到的是那一行的其中一段 → 應該是原文的一部分，而且不含圓點
    assert got and got in SAMPLE, f"沒還原出原文：{got!r}"
    assert "\u2022" not in got
    assert data.get("recovered_from_font") is True
    assert called == [], "字形反查得到答案時不應該再叫 OCR"
    # 這個旗標會讓前端整個放棄、只顯示「請自己重打」—— 已經還原出文字時
    # 一定要清掉（真實瀏覽器測試抓到過）
    assert data.get("extracted_text_unreliable") is False
    assert data.get("is_filler") is False


def test_frontend_tells_the_user_it_is_exact():
    """畫面上要說清楚是「從字型還原」，不是 OCR —— 否則使用者會逐字校對。"""
    from pathlib import Path
    html = Path("app/tools/pdf_editor/templates/pdf_editor.html").read_text(encoding="utf-8")
    assert "data.recovered_from_font" in html
    assert "已從字型還原出原文" in html


def test_does_not_bleed_into_the_neighbouring_glyph(tmp_path):
    """反查不可以抓到隔壁的字。

    一個字的原點就是它的左緣，而**下一個字的原點正好落在這個框的右緣** ——
    水平範圍用閉區間就會多吃一個字。實測拿真實表單掃過：`□` 變成 `□主`、
    `□刪除` 尾巴多一個字元，等於把本來正確的文字改壞（比原本的 bug 更糟）。
    """
    from app.core.font_catalog import best_cjk_path, embeddable_font

    _cjk = best_cjk_path("sans", "traditional")
    if not _cjk:
        # `best_cjk_path` 沒有字型時回 None，直接解包會是 TypeError 而不是
        # 「這台沒有中文字型」—— 缺系統相依要**明確跳過並寫原因**
        # （2026-09-06 CI 的核心 job 沒裝字型，一次紅了 10 條）。
        pytest.skip("這台機器沒有中文字型")
    fpath, idx = _cjk
    if not fpath:
        pytest.skip("這台機器沒有 CJK 字型")
    text = "甲乙丙丁"
    fontfile, fontbuffer = embeddable_font(str(fpath), idx, text)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="F0", fontfile=fontfile, fontbuffer=fontbuffer)
    page.insert_text((72, 120), text, fontname="F0", fontsize=20)
    out = tmp_path / "neighbour.pdf"
    doc.save(str(out))
    doc.close()

    doc = fitz.open(str(out))
    page = doc[0]
    # 只框住前兩個字（每字 20pt 寬）
    got = recover_text_in_bbox(page, (72, 104, 72 + 40, 126))
    assert got == "甲乙", f"多吃到隔壁的字：{got!r}"
    doc.close()


def test_control_characters_are_treated_as_lookup_failure(broken_pdf, monkeypatch):
    """反查到控制字元表示那張表本身不對勁 —— 實測在真實表單上踩到 NUL。
    塞一個看不見的字進使用者的文字框比整段放棄糟。"""
    import app.core.glyph_text as gt

    doc = fitz.open(str(broken_pdf))
    page = doc[0]
    line = next(line for b in page.get_text("dict")["blocks"]
                for line in b.get("lines", []))
    used = [c[1] for c in page.get_texttrace()[0]["chars"]]
    victim = used[-1]

    real = gt._gid_to_unicode

    def with_nul(buf):
        table = dict(real(buf))
        table[victim] = 0x00
        return table

    gt._gid_map_cache.clear()
    monkeypatch.setattr(gt, "_gid_to_unicode", with_nul)
    assert gt.recover_text_in_bbox(page, line["bbox"]) == ""
    doc.close()
    gt._gid_map_cache.clear()


def test_cache_never_leaks_between_documents(tmp_path):
    """兩份不同的 PDF 不可以拿到對方的字形資料。

    這條守的是一個**正式環境會產生錯字**的 bug：快取原本掛在模組層級的
    字典、用 `id(doc)` 當鍵，而文件是每個請求開一份、用完就關 ——
    `id()` 在物件被回收後會被重複使用，下一份文件很可能拿到同一個 id，
    於是反查出**別份文件的字**，而且完全無聲。

    2026-08-27 完整測試抓到（單跑全綠、合跑失敗，失敗的項目每次還不一樣）。
    修法是把快取掛在文件物件上，跟著文件一起消失。

    這裡用「開一份、關掉、再開另一份」重現 id 重用的情境。**注意這條測試
    對「改回 id(doc) 當鍵」這個變異沒有牙齒** —— id 重用不保證每次都發生。
    真正釘住修法的是下面那條 `test_page_cache_lives_on_the_document`。
    """
    from app.core.font_catalog import best_cjk_path, embeddable_font
    import app.core.glyph_text as gt

    _cjk = best_cjk_path("sans", "traditional")
    if not _cjk:
        # `best_cjk_path` 沒有字型時回 None，直接解包會是 TypeError 而不是
        # 「這台沒有中文字型」—— 缺系統相依要**明確跳過並寫原因**
        # （2026-09-06 CI 的核心 job 沒裝字型，一次紅了 10 條）。
        pytest.skip("這台機器沒有中文字型")
    fpath, idx = _cjk
    if not fpath:
        pytest.skip("這台機器沒有 CJK 字型")

    def _make(text: str, path):
        fontfile, fontbuffer = embeddable_font(str(fpath), idx, text)
        d = fitz.open()
        page = d.new_page()
        page.insert_font(fontname="F0", fontfile=fontfile, fontbuffer=fontbuffer)
        page.insert_text((72, 120), text, fontname="F0", fontsize=20)
        d.save(str(path))
        d.close()
        return path

    a = _make("甲甲甲甲", tmp_path / "a.pdf")
    b = _make("乙乙乙乙", tmp_path / "b.pdf")

    for _ in range(6):
        for path, expected in ((a, "甲甲甲甲"), (b, "乙乙乙乙")):
            doc = fitz.open(str(path))
            line = next(line for blk in doc[0].get_text("dict")["blocks"]
                        for line in blk.get("lines", []))
            got = gt.recover_text_in_bbox(doc[0], line["bbox"], doc=doc)
            doc.close()
            assert got == expected, (
                f"反查到別份文件的字：拿到 {got!r}，應該是 {expected!r}")


def test_page_cache_lives_on_the_document(tmp_path):
    """字形快取必須掛在文件物件上，**不可以是模組層級的全域狀態**。

    這條是上面那個 bug 的結構性判準。行為測試（跨文件不可污染）抓得到
    「真的發生了」，但 `id()` 重用不保證每次都發生 —— 只有直接釘住「快取
    在哪裡」，才擋得住有人把它改回全域字典。
    """
    from app.core.font_catalog import best_cjk_path, embeddable_font
    import app.core.glyph_text as gt

    _cjk = best_cjk_path("sans", "traditional")
    if not _cjk:
        # `best_cjk_path` 沒有字型時回 None，直接解包會是 TypeError 而不是
        # 「這台沒有中文字型」—— 缺系統相依要**明確跳過並寫原因**
        # （2026-09-06 CI 的核心 job 沒裝字型，一次紅了 10 條）。
        pytest.skip("這台機器沒有中文字型")
    fpath, idx = _cjk
    if not fpath:
        pytest.skip("這台機器沒有 CJK 字型")
    fontfile, fontbuffer = embeddable_font(str(fpath), idx, "測試")
    d = fitz.open()
    page = d.new_page()
    page.insert_font(fontname="F0", fontfile=fontfile, fontbuffer=fontbuffer)
    page.insert_text((72, 120), "測試", fontname="F0", fontsize=20)
    out = tmp_path / "cache.pdf"
    d.save(str(out))
    d.close()

    doc = fitz.open(str(out))
    before = {k: v for k, v in vars(gt).items()
              if isinstance(v, dict) and k.endswith("_cache")}
    sizes_before = {k: len(v) for k, v in before.items()}

    gt.recover_text_in_bbox(doc[0], (0, 0, 600, 400), doc=doc)

    # 快取要出現在**文件物件**上
    assert getattr(doc, gt._PAGE_CACHE_ATTR, None), \
        "字形快取沒有掛在文件物件上"

    # 模組層級的頁面快取不可以長大（字型指紋快取除外 —— 那個以字型內容
    # 為鍵，跨文件共用是安全的，也正是它該有的行為）
    grew = [k for k, v in vars(gt).items()
            if isinstance(v, dict) and k.endswith("_cache")
            and "gid" not in k and "internal_name" not in k
            and len(v) > sizes_before.get(k, 0)]
    doc.close()
    assert not grew, (
        f"這些模組層級的快取跟著文件在長大：{grew}\n"
        "  文件是每個請求開一份、用完就關，`id()` 會被重複使用 —— "
        "以文件為單位的資料放模組層級遲早會串到別份文件。")


def test_shared_glyph_prefers_the_unified_ideograph_over_the_radical():
    """一形多碼：同一個字形同時對應「方」(U+65B9) 與康熙部首「⽅」(U+2F45)。

    原本「取最小的字碼」會挑到部首，正常的字被換成字面一樣、字碼不同的部首
    （2026-11-06 客戶表單：有勾選框的那幾列整段走反查，「方」「支」「二」都被換掉）。
    """
    import io

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    from app.core import glyph_text as G

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "fang", "zhi"])
    fb.setupCharacterMap({0x65B9: "fang", 0x2F45: "fang", 0x652F: "zhi", 0x2F40: "zhi", 0xF900: "zhi"})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0)); pen.lineTo((0, 500)); pen.lineTo((500, 500)); pen.closePath()
    g = pen.glyph()
    fb.setupGlyf({".notdef": g, "fang": g, "zhi": g})
    fb.setupHorizontalMetrics({n: (1000, 0) for n in (".notdef", "fang", "zhi")})
    fb.setupHorizontalHeader(ascent=880, descent=-120)
    fb.setupNameTable({"familyName": "T", "styleName": "R"})
    fb.setupOS2(); fb.setupPost()
    buf = io.BytesIO(); fb.save(buf)
    G._gid_map_cache.clear()
    table = G._gid_to_unicode(buf.getvalue())
    assert table[1] == 0x65B9, hex(table[1])
    assert table[2] == 0x652F, hex(table[2])
    # 只有部首可選時仍然用它（不要變成查不到）
    assert G._uni_rank(0x2F45) > G._uni_rank(0x9FA5) and G._uni_rank(0x41) < G._uni_rank(0x2F45)
