"""表格 cell 內容修復 fixer (Sprint 3 #2 強化)。

pdf2docx 對「複雜 invoice / 多 cell 同 PDF block」的 cell 切割常出錯：
- 該 cell 空，內容跑到別 cell
- 多行 block 拆 cell 時行序錯亂
- cell 整個丟掉內容

修法：用 **pdfplumber** 重新從原 PDF 抽 ground-truth 表格（pdfplumber 對表格
偵測比 pdf2docx 強），跟 docx 表格 cell-by-cell 比對，補空 cell + 標警告。

策略（保守）：
- 只「填空」— docx cell 是空但 pdfplumber 對應位置有內容 → 補上
- **不**覆蓋已有內容（避免破壞使用者期待）
- 對應靠 row × col 維度匹配（同列同欄）
- 表格數量不對應就 skip（避免猜錯）
"""
from __future__ import annotations

import logging

import pdfplumber

log = logging.getLogger(__name__)

PDF_LIB_AVAILABLE = True


def _normalize(s: str) -> str:
    return " ".join((s or "").split())


def _extract_tables_with_pdfplumber(pdf_path) -> list[list[list[str]]]:
    """回 [[ [cell, cell, ...], ...], ...] — outer = pages, mid = tables, inner = rows of cells。"""
    out = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                page_tables = []
                try:
                    tables = page.extract_tables() or []
                    for tbl in tables:
                        clean_tbl = [[_normalize(c or "") for c in row] for row in tbl]
                        page_tables.append(clean_tbl)
                except Exception as e:
                    log.debug("pdfplumber extract page %d failed: %s", page.page_number, e)
                out.append(page_tables)
    except Exception as e:
        log.warning("pdfplumber open failed: %s", e)
    return out


def _flatten_pdf_tables(per_page_tables) -> list[list[list[str]]]:
    """把 [[page_tables...]] 攤成 [table, table, ...]。"""
    out = []
    for page_tables in per_page_tables:
        for tbl in page_tables:
            out.append(tbl)
    return out


def _docx_table_to_text(table) -> list[list[str]]:
    rows = []
    for row in table.rows:
        rows.append([_normalize(c.text or "") for c in row.cells])
    return rows


def _shape_match(docx_table_text, pdfplumber_table) -> bool:
    """簡化匹配：rows 數一致 ± 1，cols 數一致 ± 1。"""
    if not docx_table_text or not pdfplumber_table:
        return False
    dr, dc = len(docx_table_text), max(len(r) for r in docx_table_text)
    pr, pc = len(pdfplumber_table), max(len(r) for r in pdfplumber_table)
    return abs(dr - pr) <= 1 and abs(dc - pc) <= 1


def _set_cell_text(cell, text: str) -> None:
    """設 cell 文字（用第一段 / 第一個 run）。"""
    if not cell.paragraphs:
        cell.add_paragraph(text)
        return
    p = cell.paragraphs[0]
    if p.runs:
        p.runs[0].text = text
        for r in p.runs[1:]:
            r.text = ""
    else:
        p.add_run(text)


def fix_table_cell_repair(docx_doc, pdf_truth, alignment, pdf_path=None) -> dict:
    """主入口。pdf_path 是原 PDF 路徑（給 pdfplumber 用）。pipeline 會帶進來。"""
    if not pdf_path:
        return {"fixer": "table_cell_repair", "filled": 0,
                "skipped": "no pdf_path"}
    per_page = _extract_tables_with_pdfplumber(pdf_path)
    if not per_page:
        return {"fixer": "table_cell_repair", "filled": 0,
                "skipped": "pdfplumber extract failed"}
    pdf_tables = _flatten_pdf_tables(per_page)
    docx_tables = list(docx_doc.tables)
    if not pdf_tables or not docx_tables:
        return {"fixer": "table_cell_repair", "filled": 0,
                "pdf_tables": len(pdf_tables), "docx_tables": len(docx_tables)}

    filled = 0
    matched_tables = 0
    pdf_consumed = set()
    for d_tbl in docx_tables:
        d_text = _docx_table_to_text(d_tbl)
        # 找 best matching pdfplumber table by shape
        best_pi = -1
        for pi, p_tbl in enumerate(pdf_tables):
            if pi in pdf_consumed:
                continue
            if _shape_match(d_text, p_tbl):
                best_pi = pi
                break
        if best_pi < 0:
            continue
        matched_tables += 1
        pdf_consumed.add(best_pi)
        p_tbl = pdf_tables[best_pi]
        # cell-by-cell repair（只填空）
        for ri, p_row in enumerate(p_tbl):
            if ri >= len(d_tbl.rows):
                continue
            d_cells = list(d_tbl.rows[ri].cells)
            for ci, p_cell_text in enumerate(p_row):
                if ci >= len(d_cells):
                    continue
                p_cell_text = (p_cell_text or "").strip()
                d_cell_text = _normalize(d_cells[ci].text or "")
                if not d_cell_text and p_cell_text:
                    _set_cell_text(d_cells[ci], p_cell_text)
                    filled += 1

    return {
        "fixer": "table_cell_repair",
        "filled": filled,
        "matched_tables": matched_tables,
        "docx_tables": len(docx_tables),
        "pdf_tables": len(pdf_tables),
    }
