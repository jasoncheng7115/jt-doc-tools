"""直排的標籤（一個字一行，寫在窄格裡）：表單自動填寫認得出來、值放在右邊的格子（2026-11-22）。

標籤比對的單位是 PDF 裡的「一行」，直排標籤每個字自成一行 ——「地」「址」湊不成「地址」，
整列漏填（全表單回歸的合成樣本 `syn_vertical_text_label`：四個直排欄位一個都認不到）。
`pdf_form_detect._vertical_label_lines` 把 x 對齊、上下緊鄰的單字行接成一行，當成多一個候選，
之後照原本的流程比對與找值格。

判準：四個直排欄位都偵測到、值格在窄格右邊而且落在同一列裡；直排的分區標題（「基本資料」）
不變成欄位；隔太遠的字不接在一起。全表單回歸（`temp_pdfs/_regress/run_fill_regress.py`）
另外證明真實表單一份都沒變差（2026-11-22：31 份不變、疊字 10 → 10）。
"""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.core import pdf_form_detect as D

FONT = "china-t"
VERT = ["地址", "電話", "傳真", "電子郵件"]


def _make(path: Path, *, section_title: bool = False) -> dict[str, fitz.Rect]:
    """上面三列橫寫（讓它像一份表），下面四列的標籤直排寫在 28pt 寬的窄格裡。回每個直排欄位那一列的框。"""
    d = fitz.open()
    pg = d.new_page(width=595, height=842)
    y = 100.0
    rows: dict[str, fitz.Rect] = {}
    for lab in ("公司全名", "統一編號", "負責人"):
        pg.draw_rect(fitz.Rect(60, y, 535, y + 28), color=(0, 0, 0), width=0.8)
        pg.draw_line(fitz.Point(160, y), fitz.Point(160, y + 28), color=(0, 0, 0), width=0.8)
        pg.insert_text((70, y + 19), lab, fontname=FONT, fontsize=11)
        y += 28
    left = 88 if not section_title else 116
    if section_title:                       # 最左邊再多一條直排的分區標題「基本資料」（跨四列）
        h = sum(16 * len(t) + 14 for t in VERT)
        pg.draw_rect(fitz.Rect(60, y, 88, y + h), color=(0, 0, 0), width=0.8)
        for i, ch in enumerate("基本資料"):
            pg.insert_text((68, y + 40 + 16 * i), ch, fontname=FONT, fontsize=11)
    for lab in VERT:
        h = 16 * len(lab) + 14
        x_left = 60 if not section_title else 88
        pg.draw_rect(fitz.Rect(x_left, y, 535, y + h), color=(0, 0, 0), width=0.8)
        pg.draw_line(fitz.Point(left, y), fitz.Point(left, y + h), color=(0, 0, 0), width=0.8)
        for i, ch in enumerate(lab):
            pg.insert_text((x_left + 8, y + 18 + 16 * i), ch, fontname=FONT, fontsize=11)
        rows[lab] = fitz.Rect(left, y, 535, y + h)
        y += h
    d.save(path)
    return rows


@pytest.mark.parametrize("section_title", [False, True])
def test_vertical_labels_get_the_cell_on_their_right(tmp_path, section_title):
    p = tmp_path / "vertical.pdf"
    rows = _make(p, section_title=section_title)
    fields, _ = D.detect_fields(p)
    for lab, row in rows.items():
        hit = [f for f in fields if f.label_text == lab]
        assert hit, f"直排的「{lab}」沒有被認出來：{[f.label_text for f in fields]}"
        f = hit[0]
        assert f.value_slot is not None, lab
        x0, y0, x1, y1 = f.value_slot
        assert x0 >= row.x0 - 1, f"「{lab}」的值格跑到窄格裡面了：{f.value_slot}"
        assert row.y0 - 1 <= y0 and y1 <= row.y1 + 1, f"「{lab}」的值格不在自己那一列：{f.value_slot} vs {row}"
    assert not [f for f in fields if "基本資料" in f.label_text], "分區標題不可以變成欄位"


def test_stacking_only_joins_characters_that_sit_on_top_of_each_other():
    """隔太遠（一行以上的空白）、左右對不齊、字級差很多的單字都不接。"""
    def line(t, x, y, size=11):
        return {"bbox": (x, y, x + size, y + size), "spans": [{"text": t, "size": size}]}
    td = {"blocks": [{"type": 0, "lines": [
        line("地", 70, 100), line("址", 70, 116),                 # 緊鄰 → 接成「地址」
        line("電", 200, 100), line("話", 200, 140),               # 中間空一大段 → 不接
        line("傳", 300, 100), line("真", 309, 116),               # 左右對不齊 → 不接
        line("郵", 400, 100), line("件", 400, 113, size=22),      # 字級差很多 → 不接
        line("ab", 500, 100), line("cd", 500, 116),               # 不是單一個中文字 → 不接
    ]}]}
    got = sorted(ln["spans"][0]["text"] for ln in D._vertical_label_lines(td))
    assert got == ["地址"], got
