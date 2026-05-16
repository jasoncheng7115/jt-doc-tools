"""假表格移除 fixer。

pdf2docx 對 absolute-positioned text block（例如 invoice description cell 內的
每個 bullet point）會各自包成獨立 1×1 表格。視覺上會跟其他元素重疊，且嚴重破壞
docx 流動排版。

判定為假表格（任一即移除）：
- 表格只有 1 列且只有 1 欄
- 表格只有 1 列且所有 cell 文字加起來像一個正常段落（< 100 字）
- 表格所有 cell 都空白
- (未來：用 PDFTruth.drawings 確認該位置無對應線條 → 真值校正)

移除策略：把 cell 內的所有段落「展平」到 table 原位置（用 XML insert before），
保留段落文字 / 字型屬性，刪掉 table element。
"""
from __future__ import annotations

import logging
from copy import deepcopy

from docx.oxml.ns import qn

log = logging.getLogger(__name__)

MAX_CELL_TEXT_FOR_FAKE = 200  # 1-row 表格內所有文字 < 此字數視為「其實是一段」


def _is_fake_table(table) -> tuple[bool, str]:
    """回 (is_fake, reason)。"""
    rows = list(table.rows)
    cols = list(table.columns) if rows else []
    if not rows:
        return True, "no rows"

    # 全空白
    all_text = " ".join((c.text or "").strip() for r in rows for c in r.cells).strip()
    if not all_text:
        return True, "all empty"

    # 1×1
    if len(rows) == 1 and len(cols) == 1:
        return True, "1x1 single cell"

    # 1 列 + 文字短
    if len(rows) == 1 and len(all_text) < MAX_CELL_TEXT_FOR_FAKE:
        return True, "1-row short text"

    return False, ""


def fix_fake_table_remove(docx_doc, pdf_truth, alignment) -> dict:
    """掃整份 docx，移除假表格並把內容還原成段落。"""
    body = docx_doc.element.body
    removed = 0
    flattened_paragraphs = 0
    reasons: dict[str, int] = {}

    for table in list(docx_doc.tables):
        is_fake, reason = _is_fake_table(table)
        if not is_fake:
            continue
        # 收集 table 內所有段落 element 副本（保留字型 / 樣式）
        para_elements = []
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    if (p.text or "").strip():
                        para_elements.append(deepcopy(p._element))
        # 在 table 原位置 insert 段落 elements
        tbl_elem = table._element
        parent = tbl_elem.getparent()
        if parent is None:
            continue
        idx = list(parent).index(tbl_elem)
        for i, p_elem in enumerate(para_elements):
            parent.insert(idx + i, p_elem)
            flattened_paragraphs += 1
        # 移除 table
        parent.remove(tbl_elem)
        removed += 1
        reasons[reason] = reasons.get(reason, 0) + 1

    return {
        "fixer": "fake_table_remove",
        "removed_tables": removed,
        "flattened_paragraphs": flattened_paragraphs,
        "reasons": reasons,
    }
