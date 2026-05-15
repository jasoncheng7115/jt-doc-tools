"""表格自動寬度 fixer。

pdf2docx 預設給每個表格設 `w:tblLayout w:type="fixed"`，且每個 cell 是嚴格固定
寬度。當 LibreOffice 渲染時：
- 字型 fallback 比原 PDF 字型寬（如 MingLiU → 新細明體）
- 中文字元渲染寬度差異
→ cell 容不下文字 → 文字被切（換行 + cell 高度也 fixed → 第二行看不到）

修法：
1. table layout 改 `w:type="autofit"` — 讓 cell 可隨內容變寬
2. table cell preferred width 改 `w:type="auto"` — cell 自己挑最佳寬度

副作用：表格實際寬度可能跟原 PDF 不完全一致，但「文字看不到」更嚴重，先解這個。
"""
from __future__ import annotations

import logging

from docx.oxml.ns import qn

log = logging.getLogger(__name__)


def _set_table_layout_autofit(table) -> bool:
    """設 table 的 w:tblLayout 為 autofit。"""
    try:
        tblPr = table._element.find(qn("w:tblPr"))
        if tblPr is None:
            tblPr = table._element.makeelement(qn("w:tblPr"), {})
            table._element.insert(0, tblPr)
        # 移除舊 w:tblLayout
        for old in tblPr.findall(qn("w:tblLayout")):
            tblPr.remove(old)
        layout = tblPr.makeelement(qn("w:tblLayout"), {})
        layout.set(qn("w:type"), "autofit")
        tblPr.append(layout)
        # 移除 tblW 的固定 width 設定（讓表格寬度也自動）
        tblW = tblPr.find(qn("w:tblW"))
        if tblW is not None:
            tblW.set(qn("w:type"), "auto")
            tblW.set(qn("w:w"), "0")
        return True
    except Exception as e:
        log.debug("set table layout autofit failed: %s", e)
        return False


def _set_cells_preferred_width_auto(table) -> int:
    """把 table 內每個 cell 的 preferred width 改成 auto，cell 自動挑寬度。"""
    n = 0
    for row in table.rows:
        for cell in row.cells:
            try:
                tcPr = cell._element.find(qn("w:tcPr"))
                if tcPr is None:
                    continue
                tcW = tcPr.find(qn("w:tcW"))
                if tcW is not None:
                    # 保留 type 的 dxa value 但改 type=auto，cell 自動 wrap content
                    # 注意：完全改 auto 會讓 cell 完全丟掉預設寬度，但通常 auto 較安全
                    tcW.set(qn("w:type"), "auto")
                    tcW.set(qn("w:w"), "0")
                    n += 1
            except Exception:
                pass
    return n


def fix_table_autofit(docx_doc, pdf_truth, alignment) -> dict:
    tables_changed = 0
    cells_relaxed = 0
    for table in docx_doc.tables:
        if _set_table_layout_autofit(table):
            tables_changed += 1
        cells_relaxed += _set_cells_preferred_width_auto(table)
    return {
        "fixer": "table_autofit",
        "tables_changed": tables_changed,
        "cells_relaxed": cells_relaxed,
    }
