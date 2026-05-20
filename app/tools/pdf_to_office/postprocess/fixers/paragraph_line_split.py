"""依 PDF block lines 還原段落內被吃掉的行尾換行（Sprint B 2 強化）。

`paragraph_split` 處理「aligner 給的 1:N alignment」— 對應上游給多個 PDF block
被合成 docx 一段的情境。但實務上另一條漏洞：**單一 PDF block 內就有多個
PDFLine**，pdf2docx 把 lines 間的換行吃掉，整段文字黏成連續字串。例：

  PDF block 含 4 行：
    <公司名稱>
    <地址>…
    <區號>
    Taiwan
  → pdf2docx 給 docx 三段：「<公司名稱>」「<地址>」「<區號><國家>」

最後一段把 「<區號>」+「Taiwan」黏在一起，因為 pdf2docx 內部判斷某兩 line
y-gap 小到視為「同一段」。本 fixer 補救：

對每個 docx body paragraph（不在表格內），掃 PDFTruth 內**所有 block 的 lines
連續視窗**，找其 concat（normalized）== docx paragraph text 且視窗 size >= 2
的最佳匹配。找到就依 line 邊界把 docx 段落拆成 N 段，使用 block 的 dominant
font 屬性套到新段落。
"""
from __future__ import annotations

import logging
import re
from copy import deepcopy

from docx.oxml.ns import qn

log = logging.getLogger(__name__)


MAX_LINES_WINDOW = 8  # 視窗最多覆蓋 8 行（避免大 block 三重迴圈炸開）


def _normalize(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", "", s).strip()


def _find_line_window(docx_text: str, all_blocks) -> tuple | None:
    """掃 all_blocks，找一個 block 內連續 lines 視窗，其 normalized concat 等於
    docx_text。回 (block, start_idx, end_idx) 或 None。"""
    target = _normalize(docx_text)
    if not target:
        return None
    for b in all_blocks:
        if not b.lines or len(b.lines) < 2:
            continue
        # 先快速 reject：target 必須在 block 全文 normalized 內
        if target not in _normalize(b.text):
            continue
        n = min(len(b.lines), MAX_LINES_WINDOW)
        for s in range(n):
            concat = ""
            for e in range(s + 1, n + 1):
                concat = concat + _normalize(b.lines[e - 1].text)
                # 提前終止：concat 已長於 target 還沒中 → 跳出
                if len(concat) > len(target):
                    break
                if concat == target and (e - s) >= 2:
                    return b, s, e
    return None


def _clone_paragraph_with_text(p_element, new_text: str):
    """以 p_element 為樣板複製新段落，文字置換成 new_text。"""
    new_elem = deepcopy(p_element)
    # 移除所有 run，留 pPr
    rPr_template = None
    orig_runs = p_element.findall(qn("w:r"))
    if orig_runs:
        rPr_template = orig_runs[0].find(qn("w:rPr"))
    for r in new_elem.findall(qn("w:r")):
        new_elem.remove(r)
    new_r = new_elem.makeelement(qn("w:r"), {})
    if rPr_template is not None:
        new_r.append(deepcopy(rPr_template))
    new_t = new_r.makeelement(qn("w:t"), {qn("xml:space"): "preserve"})
    new_t.text = new_text
    new_r.append(new_t)
    new_elem.append(new_r)
    return new_elem


def _paragraph_text(p_element) -> str:
    parts = []
    for r in p_element.findall(qn("w:r")):
        for t in r.findall(qn("w:t")):
            if t.text:
                parts.append(t.text)
    return "".join(parts)


def fix_paragraph_line_split(docx_doc, pdf_truth, alignment) -> dict:
    if not pdf_truth or not pdf_truth.pages:
        return {"fixer": "paragraph_line_split", "split": 0,
                "skipped": "no pdf_truth"}
    all_blocks = pdf_truth.all_blocks
    if not all_blocks:
        return {"fixer": "paragraph_line_split", "split": 0,
                "skipped": "no pdf blocks"}

    # 掃整份 docx 內所有 w:p (body + table cells)。table cell 內段落也可能是
    # pdf2docx 把 PDF 同 block 多 line 黏成單段的受害者（例：<樣本 A>右上「<區號>
    # Taiwan」被塞進 pdf2docx 製造的表格 cell 內，body iter 才看得到）。
    body = docx_doc.element.body
    para_tag = qn("w:p")
    body_paras = list(body.iter(para_tag))

    split_count = 0
    pieces_inserted = 0
    used_block_window: set = set()  # (id(block), s, e) 避免一個 block 被多次切

    for p_el in body_paras:
        text = _paragraph_text(p_el).strip()
        if not text or len(text) < 4:
            continue
        # docx 段落應該是被合掉換行的「單行文字」— 若已含 \n 視為已拆好不動
        if "\n" in text:
            continue
        match = _find_line_window(text, all_blocks)
        if not match:
            continue
        block, s, e = match
        key = (id(block), s, e)
        if key in used_block_window:
            continue
        used_block_window.add(key)
        # 拆段：第 1 行塞回原 p，2..N 行 insert after
        lines = block.lines[s:e]
        first_text = lines[0].text.strip()
        if not first_text:
            continue
        # 第一個 run 文字置 lines[0]，後面 run 清空
        runs = p_el.findall(qn("w:r"))
        if not runs:
            continue
        first_t = runs[0].find(qn("w:t"))
        if first_t is None:
            continue
        first_t.text = first_text
        first_t.set(qn("xml:space"), "preserve")
        for r in runs[1:]:
            for t in r.findall(qn("w:t")):
                t.text = ""
        # 多餘空 runs 移掉（保留 first run 帶 rPr）
        for r in runs[1:]:
            p_el.remove(r)
        # 後續 lines 各成一新段落
        parent = p_el.getparent()
        idx = list(parent).index(p_el)
        for i, ln in enumerate(lines[1:], start=1):
            piece_text = (ln.text or "").strip()
            if not piece_text:
                continue
            new_el = _clone_paragraph_with_text(p_el, piece_text)
            parent.insert(idx + i, new_el)
            pieces_inserted += 1
        split_count += 1

    return {
        "fixer": "paragraph_line_split",
        "split": split_count,
        "pieces_inserted": pieces_inserted,
    }
