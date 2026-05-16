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


def _strip_empty(row: list[str]) -> list[str]:
    """過濾掉純空白 cell — pdfplumber 對某些 PDF 會回多個 spacer column 是 empty。"""
    return [c for c in row if c and c.strip()]


def _shape_match(docx_table_text, pdfplumber_table) -> bool:
    """簡化匹配：rows ± 2、cols (含 / 不含 empty) 任一吻合 ± 1。

    pdfplumber 對寬欄位 PDF 會抽出 spacer empty column；strip empty 後 col 數常
    跟 docx 一致。所以兩種都試。
    """
    if not docx_table_text or not pdfplumber_table:
        return False
    dr = len(docx_table_text)
    pr = len(pdfplumber_table)
    if abs(dr - pr) > 2:
        return False
    dc = max(len(r) for r in docx_table_text) if docx_table_text else 0
    pc = max(len(r) for r in pdfplumber_table) if pdfplumber_table else 0
    pc_stripped = max((len(_strip_empty(r)) for r in pdfplumber_table), default=0)
    # 任一 col 數對得起來都算
    return abs(dc - pc) <= 1 or abs(dc - pc_stripped) <= 1


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


def _repair_via_pdf_truth_blocks(docx_doc, pdf_truth) -> int:
    """用 PDFTruth multi-line block 補 docx table empty cell。

    對每個 docx table row, 看是否有空 cell。對非空 cells 的內容 (concat) 在
    PDFTruth blocks 找含這些內容的 block (block.text 含 \\n 多行)。block 內 lines
    跟 row cells 數一致時，把 block 對應位置的 line 填回空 cell。

    通用情境：item-line 表格（編號 / 品項 / 數量 / 單價 / 稅項 / 金額）等 N 欄
    結構，pdf2docx 對 PDF item block (各欄文字以同 block 多 line 形式存在) 偵測
    cell 邊界錯位 → 部份欄被誤判為空。本 fallback 用 PDF 真值 block 補回。
    """
    if not pdf_truth or not pdf_truth.pages:
        return 0
    # 收集 PDF 內所有 multi-line text blocks（lines ≥ 2 行）
    multi_blocks = []
    for page in pdf_truth.pages:
        for b in page.blocks:
            if b.block_type != "text":
                continue
            if len(b.lines) >= 2:
                multi_blocks.append(b)
    if not multi_blocks:
        return 0

    filled = 0
    consumed_blocks: set[int] = set()  # 每個 PDF block 只能用來補一個 row
    for table in docx_doc.tables:
        # 統計：哪些 tc element 在「不同欄位位置」被多 row reference
        # 正常 vMerge：同 element 在多 row 但都同一欄位 → 寫入會正確 spread
        # 不正常 (pdf2docx bug)：同 element 在多 row 不同欄位 → 寫一次 spillover
        # 後者才禁止填
        element_col_positions: dict = {}
        for row in table.rows:
            seen_in_row = set()
            for ci, c in enumerate(row.cells):
                eid = id(c._element)
                if eid in seen_in_row:
                    continue
                seen_in_row.add(eid)
                element_col_positions.setdefault(eid, set()).add(ci)
        shared_eids = {eid for eid, cols in element_col_positions.items() if len(cols) >= 2}
        for row in table.rows:
            cells = list(row.cells)
            n_cells = len(cells)
            if n_cells < 3:
                continue
            cell_texts = [_normalize(c.text or "") for c in cells]
            empty_idxs = [i for i, t in enumerate(cell_texts) if not t]
            if not empty_idxs:
                continue
            nonempty_texts = [t for t in cell_texts if t]
            if len(nonempty_texts) < 2:
                continue  # 太少 anchor — 容易誤配
            # 找 block.lines 數量跟 n_cells 一致 + 含全部 nonempty 內容 + 還沒用過
            best_block = None
            best_idx = -1
            for bi, b in enumerate(multi_blocks):
                if bi in consumed_blocks:
                    continue
                lines_text = [_normalize(ln.text or "") for ln in b.lines]
                if len(lines_text) != n_cells:
                    continue
                # 嚴格匹配：每個 nonempty 都要在 lines 內 substr，且位置一致
                # （避免 row r4/r5/r6 全配到同一個 block 6 的 case）
                # 位置對準：cell_texts[i] 非空時必須 == 或 in lines_text[i]
                position_match = True
                for i, ct in enumerate(cell_texts):
                    if not ct:
                        continue
                    if i >= len(lines_text):
                        position_match = False
                        break
                    lt = lines_text[i]
                    if not (ct == lt or ct in lt or lt in ct):
                        position_match = False
                        break
                if position_match:
                    best_block = b
                    best_idx = bi
                    break
            if best_block is None:
                continue
            # 對 empty cell, block.lines[idx] 補上
            block_lines = [_normalize(ln.text or "") for ln in best_block.lines]
            for idx in empty_idxs:
                if idx < len(block_lines) and block_lines[idx]:
                    if id(cells[idx]._element) in shared_eids:
                        # 跨 row 共用 element — 填進去會 spillover 到其他 row
                        # （pdf2docx 給的 table layout 不健康，保險起見不寫）
                        continue
                    _set_cell_text(cells[idx], block_lines[idx])
                    filled += 1
            consumed_blocks.add(best_idx)
    return filled


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
        # 跨 row 共用 element 偵測（同 _repair_via_pdf_truth_blocks 邏輯）
        element_col_positions: dict = {}
        for row in d_tbl.rows:
            seen_in_row = set()
            for ci, c in enumerate(row.cells):
                eid = id(c._element)
                if eid in seen_in_row:
                    continue
                seen_in_row.add(eid)
                element_col_positions.setdefault(eid, set()).add(ci)
        shared_eids = {eid for eid, cols in element_col_positions.items() if len(cols) >= 2}
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
        # cell-by-cell repair（只填空）— 若 pdfplumber row 的 col 數 > docx row，
        # 先 strip empty cells 對齊
        for ri, p_row in enumerate(p_tbl):
            if ri >= len(d_tbl.rows):
                continue
            d_cells = list(d_tbl.rows[ri].cells)
            # 若 cols 不一致且 strip empty 後一致 → 用 strip 後的 row
            if len(p_row) != len(d_cells):
                p_row_stripped = _strip_empty(p_row)
                if abs(len(p_row_stripped) - len(d_cells)) <= 1:
                    p_row = p_row_stripped
            for ci, p_cell_text in enumerate(p_row):
                if ci >= len(d_cells):
                    continue
                p_cell_text = (p_cell_text or "").strip()
                d_cell_text = _normalize(d_cells[ci].text or "")
                if not d_cell_text and p_cell_text:
                    if id(d_cells[ci]._element) in shared_eids:
                        continue  # spillover 風險，不寫
                    _set_cell_text(d_cells[ci], p_cell_text)
                    filled += 1

    # Fallback / 強化：用 PDFTruth multi-line block 配對 docx row（pdfplumber 抽
    # 不到 / shape 對不上時用這條補）
    truth_filled = _repair_via_pdf_truth_blocks(docx_doc, pdf_truth)
    filled += truth_filled

    return {
        "fixer": "table_cell_repair",
        "filled": filled,
        "via_pdfplumber": filled - truth_filled,
        "via_pdf_truth_blocks": truth_filled,
        "matched_tables": matched_tables,
        "docx_tables": len(docx_tables),
        "pdf_tables": len(pdf_tables),
    }
