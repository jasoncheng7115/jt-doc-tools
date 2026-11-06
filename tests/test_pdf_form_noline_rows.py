"""沒有格線的表單：整張只有一個外框、裡面一列一列的「標籤：」（v1.17.32）。

客戶回報的版型：值格從標籤那一列一路延伸到下一條畫出來的橫線（二十幾列），填寫時
值放在格子垂直置中的位置，就掉到下面幾列、壓在別的標籤上。這裡用合成表重現（不放
客戶樣本的任何內容）：

* 值格只用標籤自己那一列，右界停在同一列下一段字（「發票章：」）或章框直線前面
* 「公司地址(英文)」填英文地址；「發票地址(英文)」沒有英文版欄位，不填中文進去
* 「連絡人:　職務:」同一段字：標籤框用逐字座標切；值不跨過同一列的下一段字
* 標籤後面單獨一段的冒號：值從冒號後面開始
"""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.core import pdf_form_detect as D

FONT = "china-t"
ROWS = ["1. 公司名稱:", "2. 統一編號:", "3. 公司地址(含郵遞區號):", "4. 公司地址(英文):",
        "5. 發票地址(英文):               發票章：", "6. 負責人:", "7. 員工人數:",
        "8. 電子郵件信箱:", "9. 公司網頁網址:", "10. 主要客戶:"]
TOP, PITCH = 196.0, 18.0
SEAL = fitz.Rect(430, 284, 520, 380)


def _frame(pg, r: fitz.Rect, w: float = 0.48) -> None:
    """外框照客戶那份的畫法：四條細的實心矩形（Word 匯出的表格框線長這樣）。"""
    for x0, y0, x1, y1 in ((r.x0, r.y0, r.x1, r.y0 + w), (r.x0, r.y1 - w, r.x1, r.y1),
                           (r.x0, r.y0, r.x0 + w, r.y1), (r.x1 - w, r.y0, r.x1, r.y1)):
        pg.draw_rect(fitz.Rect(x0, y0, x1, y1), color=None, fill=(0, 0, 0), width=0)


def _make(path: Path) -> None:
    d = fitz.open()
    pg = d.new_page(width=595, height=842)
    pg.insert_text((90, 172), "連絡人:       職務:       電話:", fontname=FONT, fontsize=10)
    _frame(pg, fitz.Rect(68, 180, 554, 560))                                         # 唯一的外框
    pg.draw_rect(SEAL, color=(0, 0, 0), width=3)                                     # 章框
    pg.insert_text((74, TOP), "1. ", fontname=FONT, fontsize=12)
    pg.insert_text((89, TOP), "公司名稱", fontname=FONT, fontsize=12)
    pg.insert_text((137.5, TOP), "：", fontname=FONT, fontsize=11)                 # 冒號單獨一段
    for i, t in enumerate(ROWS[1:], start=1):
        pg.insert_text((74, TOP + PITCH * i), t, fontname=FONT, fontsize=10)
    d.save(path)


@pytest.fixture(scope="module")
def form(tmp_path_factory):
    p = tmp_path_factory.mktemp("noline") / "noline.pdf"
    _make(p)
    fields, _ = D.detect_fields(p)
    return p, fields


def _by_label(fields, part):
    return [f for f in fields if part in f.label_text]


def test_inline_slots_stay_on_their_own_row(form):
    _, fields = form
    inline = [f for f in fields if f.slot_kind == "inline"]
    assert len(inline) >= 6, [(f.label_text, f.slot_kind) for f in fields]
    for f in inline:
        lh = f.label_rect[3] - f.label_rect[1]
        sx0, sy0, sx1, sy1 = f.value_slot
        assert sy1 - sy0 <= lh * 2.2, (f.label_text, f.value_slot)
        # 不可以跨到別列的標籤
        for g in fields:
            if g is f or g.label_rect[1] == f.label_rect[1]:
                continue
            assert min(sy1, g.label_rect[3]) - max(sy0, g.label_rect[1]) <= 1, (f.label_text, g.label_text)


def test_english_annotated_labels(form):
    _, fields = form
    keys = {f.profile_key for f in _by_label(fields, "公司地址(英文)")}
    assert keys == {"english_address"}, keys
    assert not any(f.profile_key == "invoice_address" for f in _by_label(fields, "發票地址(英文)")), \
        "英文發票地址沒有對應欄位：不可以填中文發票地址"


def test_right_edge_stops_before_the_next_text_and_the_seal_box(form):
    p, fields = form
    with fitz.open(p) as d:
        seal_word = next(w for w in d[0].get_text("words") if "發票章" in w[4])
    for f in fields:
        if f.slot_kind != "inline" or f.value_slot is None:
            continue
        cy = (f.label_rect[1] + f.label_rect[3]) / 2
        if seal_word[1] - 1 <= cy <= seal_word[3] + 1:
            assert f.value_slot[2] <= seal_word[0], f.value_slot
        if SEAL.y0 <= cy <= SEAL.y1:                           # 章框穿過的那幾列
            assert f.value_slot[2] <= SEAL.x0, (f.label_text, f.value_slot)


def test_split_uses_real_glyph_positions():
    """同一段字裡中文與半形空白寬度不同：有逐字座標時，標籤框照實際位置切，不用等寬估。"""
    text = "連絡人:    職務:"
    boxes, x = [], 90.0
    for ch in text:
        w = 10.0 if ord(ch) > 0x2E7F else 2.8           # 中文 10pt、半形 2.8pt
        boxes.append((x, 162.0, x + w, 174.0))
        x += w
    exact = D._split_multi_colon_span(text, (90.0, 162.0, x, 174.0), boxes)
    assert [t for t, _ in exact] == ["連絡人:", "職務:"]
    assert abs(exact[0][1][2] - boxes[3][2]) < 0.01            # 「:」的右緣
    assert abs(exact[1][1][0] - boxes[8][0]) < 0.01            # 「職」的左緣
    # 沒有逐字座標（或對不上字數）時退回等寬估算，行為跟原本一樣
    est = D._split_multi_colon_span(text, (90.0, 162.0, x, 174.0))
    assert est == D._split_multi_colon_span(text, (90.0, 162.0, x, 174.0), boxes[:-1])
    assert abs(est[0][1][2] - exact[0][1][2]) > 5, "這個例子估算會偏，確認測試真的分得出兩者"


def test_value_starts_after_a_separate_colon(form):
    _, fields = form
    f = next(f for f in fields if f.profile_key == "company_name")
    assert f.value_slot[0] >= 137.5 + 10, f.value_slot


def test_filled_values_never_cover_printed_words(form, tmp_path):
    """端到端：填完之後，每一個值格都不蓋到紙上原本印的字（回歸腳本的疊字判準）。"""
    from app.tools.pdf_fill import service
    p, _ = form
    profile = {"company_name": "測試工具箱有限公司", "tax_id": "12345678", "address": "台北市信義區測試路 1 號",
               "english_address": "1 Test Rd., Taipei", "invoice_address": "台北市信義區測試路 1 號",
               "owner": "王小明", "employees": "10", "email": "test@example.com", "contact": "王小明",
               "phone": "02-12345678", "company_website": "https://example.com"}
    rep = service.fill_pdf(p, tmp_path / "out.pdf", profile)
    assert rep.filled_count >= 8, rep.filled_count
    with fitz.open(p) as d:
        words = [w[:4] for w in d[0].get_text("words")]
    for pl in rep.placements:
        s = pl.slot
        for bx0, by0, bx1, by1 in words:
            ox = min(s[2], bx1) - max(s[0], bx0)
            oy = min(s[3], by1) - max(s[1], by0)
            assert not (ox > 1 and oy > 1 and ox * oy > (bx1 - bx0) * (by1 - by0) * 0.5), (pl.source_key, s)
