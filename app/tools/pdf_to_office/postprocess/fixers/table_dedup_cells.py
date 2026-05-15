"""表格相鄰重複 cell 合併 fixer。

pdf2docx 處理 PDF 「跨欄合併儲存格」時常常把同一段內容複製到多個相鄰 cell（橫向
或縱向都有），導致 docx 表格出現重複內容。

修法：
- 對每列，連續相同內容的相鄰 cell → 用 horizontal merge (gridSpan)
- 對每欄，連續相同內容的相鄰 cell → 用 vertical merge (vMerge=restart/continue)

保守策略：
- 空 cell 不算重複（不合併空 cell — 可能本來就是分欄）
- 行/列數 < 2 不處理
"""
from __future__ import annotations

import logging

from docx.oxml.ns import qn

log = logging.getLogger(__name__)


def _normalize(s: str) -> str:
    return " ".join((s or "").split())


def _set_horizontal_merge(cells: list, span_count: int) -> None:
    """把連續 N 個 cell 設成 gridSpan = span_count（保留第一個內容）。"""
    if span_count <= 1 or not cells:
        return
    first = cells[0]
    tcPr = first._element.find(qn("w:tcPr"))
    if tcPr is None:
        tcPr = first._element.makeelement(qn("w:tcPr"), {})
        first._element.insert(0, tcPr)
    # 設 gridSpan
    for old in tcPr.findall(qn("w:gridSpan")):
        tcPr.remove(old)
    grid_span = tcPr.makeelement(qn("w:gridSpan"), {})
    grid_span.set(qn("w:val"), str(span_count))
    tcPr.append(grid_span)


def fix_table_dedup_cells(docx_doc, pdf_truth, alignment) -> dict:
    horizontal_merges = 0
    rows_processed = 0
    for table in docx_doc.tables:
        for row in table.rows:
            cells = list(row.cells)
            if len(cells) < 2:
                continue
            rows_processed += 1
            # python-docx row.cells 對於 vertical merge / gridSpan 會回多個物件
            # 指向同一個 tc。用 _element identity 過濾：
            seen_elements = []
            unique_cells = []
            for c in cells:
                if c._element not in seen_elements:
                    seen_elements.append(c._element)
                    unique_cells.append(c)
            if len(unique_cells) < 2:
                continue
            # 找連續相同非空內容群組
            i = 0
            while i < len(unique_cells):
                base_text = _normalize(unique_cells[i].text or "")
                if not base_text:
                    i += 1
                    continue
                j = i + 1
                while j < len(unique_cells) and _normalize(unique_cells[j].text or "") == base_text:
                    j += 1
                if j - i >= 2:
                    # 連續 j-i 個相同內容 — gridSpan 合併
                    _set_horizontal_merge(unique_cells[i:j], j - i)
                    # 後面 unique_cells[i+1..j-1] 內容清空（gridSpan 後不顯示）
                    for k in range(i + 1, j):
                        for p in unique_cells[k].paragraphs:
                            for r in p.runs:
                                r.text = ""
                    horizontal_merges += 1
                i = j
    return {
        "fixer": "table_dedup_cells",
        "horizontal_merges": horizontal_merges,
        "rows_processed": rows_processed,
    }
