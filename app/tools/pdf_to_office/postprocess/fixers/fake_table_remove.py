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
HEAVY_EMPTY_RATIO = 0.70      # 表格 cell 70% 以上為空白 → 候選假表格
HEAVY_EMPTY_MIN_CELLS = 4     # cell 數至少 4 才考慮（避免誤殺 2x1 / 1x2）
HEAVY_EMPTY_MAX_NONEMPTY = 5  # 非空 cell 不超過 5 個（總文字也稀疏）


def _count_cells_with_text(table) -> tuple[int, int, int]:
    """回 (total_cells, non_empty_cells, total_text_len)，跨 merged cell 只算一次。"""
    seen: set = set()
    total = 0
    non_empty = 0
    total_text_len = 0
    for row in table.rows:
        for cell in row.cells:
            cid = id(cell._element)
            if cid in seen:
                continue
            seen.add(cid)
            total += 1
            text = (cell.text or "").strip()
            if text:
                non_empty += 1
                total_text_len += len(text)
    return total, non_empty, total_text_len


def _pdf_has_rect_near(pdf_truth, ratio_threshold: float = 0.001) -> bool:
    """快速 proxy：PDFTruth 全文有沒有任何「夠大」的矩形 drawing。沒有 → 多數
    docx 表格都是 pdf2docx 編造的假表，可較積極移除。"""
    if not pdf_truth or not pdf_truth.pages:
        return False
    for pg in pdf_truth.pages:
        page_area = float(pg.width or 0) * float(pg.height or 0)
        if page_area <= 0:
            continue
        for drw in pg.drawings or []:
            if drw.type != "rect":
                continue
            x0, y0, x1, y1 = drw.bbox
            w = max(0.0, x1 - x0)
            h = max(0.0, y1 - y0)
            if (w * h) / page_area >= ratio_threshold:
                return True
    return False


def _is_fake_table(table, *, pdf_has_real_table: bool) -> tuple[bool, str]:
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

    # 重空 + 稀疏 — 大量空 cell + 內容很少（典型 pdf2docx 把分散 short text
    # 編成表格的副作用）
    total, non_empty, total_text_len = _count_cells_with_text(table)
    if total >= HEAVY_EMPTY_MIN_CELLS:
        empty_ratio = 1.0 - (non_empty / total)
        if (empty_ratio >= HEAVY_EMPTY_RATIO
                and non_empty <= HEAVY_EMPTY_MAX_NONEMPTY
                and total_text_len < MAX_CELL_TEXT_FOR_FAKE):
            # 對 PDF 內**無真實表格**的整份文件，更積極移除；有真實表格的文件
            # 仍可移除但要求更稀疏（避免把真的 sparse 表格吃掉）
            if not pdf_has_real_table:
                return True, "heavy_empty_no_pdf_table"
            # 有 PDF 表格時更謹慎：empty_ratio >= 0.85 才動
            if empty_ratio >= 0.85 and non_empty <= 3:
                return True, "heavy_empty_strict"

    return False, ""


def fix_fake_table_remove(docx_doc, pdf_truth, alignment) -> dict:
    """掃整份 docx，移除假表格並把內容還原成段落。"""
    body = docx_doc.element.body
    removed = 0
    flattened_paragraphs = 0
    reasons: dict[str, int] = {}
    pdf_has_real_table = _pdf_has_rect_near(pdf_truth)

    for table in list(docx_doc.tables):
        is_fake, reason = _is_fake_table(
            table, pdf_has_real_table=pdf_has_real_table)
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
