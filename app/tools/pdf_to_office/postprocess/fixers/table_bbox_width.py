"""表格欄寬從 PDF bbox 推導 fixer（Sprint B #6）。

pdf2docx 對 PDF 表格的欄寬常推測失準（按字符數而非實際 PDF 框線位置）。本
fixer 用 pdfplumber 的 `find_tables()` 取得每個表格 cell 的真實 bbox 座標，
從 cell 的 x0 / x1 集合反推「該表所有欄的真實寬度」，覆寫進 docx 的 tblGrid
+ 每個 cell 的 tcW（含 gridSpan 累加）。

策略：
- pdfplumber 表格與 docx 表格用 shape match（rows ± 2、cols ± 1 / strip-empty
  後 ± 1）+ 順序消費對應，跟既有 table_cell_repair 同套規則
- 必須在 table_autofit 之後跑：autofit 把 tcW type 設為 auto / w=0；本 fixer
  改回 type=dxa 並寫入 bbox 真值寬度。autofit layout 仍保留，所以 LibreOffice /
  Word 仍會在內容超寬時自動撐開（保留 autofit 的好處）
- 對 merged cell（gridSpan="N"）寫入「該 cell 所覆蓋連續 N 欄的寬度總和」
- 若 pdfplumber 抽出的 col 數比 docx 多 1（pdfplumber 常產生 spacer 窄欄）→
  合併最窄欄到鄰欄；多超過 1 / 少於 docx 都 skip 該表（避免猜錯）

座標：pdfplumber bbox 單位是 pt（PDF point, 1/72 in），docx dxa = 1/20 pt。
"""
from __future__ import annotations

import logging

import pdfplumber
from docx.oxml.ns import qn

log = logging.getLogger(__name__)

TWIPS_PER_PT = 20
MIN_COL_WIDTH_PT = 3.0  # 小於此寬度視為 pdfplumber spacer 欄，會優先被合併


def _normalize(s: str) -> str:
    return " ".join((s or "").split())


def _strip_empty(row: list[str]) -> list[str]:
    return [c for c in row if c and c.strip()]


def _shape_match(docx_table_text: list[list[str]],
                 pdfplumber_table: list[list[str]]) -> bool:
    if not docx_table_text or not pdfplumber_table:
        return False
    dr, pr = len(docx_table_text), len(pdfplumber_table)
    if abs(dr - pr) > 2:
        return False
    dc = max((len(r) for r in docx_table_text), default=0)
    pc = max((len(r) for r in pdfplumber_table), default=0)
    pc_stripped = max((len(_strip_empty(r)) for r in pdfplumber_table), default=0)
    return abs(dc - pc) <= 1 or abs(dc - pc_stripped) <= 1


def _col_widths_from_table(table) -> list[float]:
    """從 pdfplumber Table.cells 取唯一 x 邊界，回相鄰 x 間距的 list（pt）。"""
    xs: set = set()
    for cell in table.cells or []:
        if cell is None:
            continue
        try:
            x0, _, x1, _ = cell
            xs.add(round(float(x0), 2))
            xs.add(round(float(x1), 2))
        except Exception:
            continue
    xs_sorted = sorted(xs)
    if len(xs_sorted) < 2:
        return []
    return [xs_sorted[i + 1] - xs_sorted[i] for i in range(len(xs_sorted) - 1)]


def _extract_pdfplumber_tables_with_widths(pdf_path) -> list[dict]:
    """跑 pdfplumber 抽全 PDF 的表格，每筆含 (text_matrix, col_widths_pt)。"""
    out: list[dict] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                try:
                    tables = page.find_tables() or []
                except Exception as e:
                    log.debug("find_tables page %d failed: %s", page.page_number, e)
                    continue
                for tbl in tables:
                    try:
                        text_matrix = tbl.extract() or []
                        text_matrix = [[_normalize(c or "") for c in row]
                                       for row in text_matrix]
                        widths = _col_widths_from_table(tbl)
                    except Exception as e:
                        log.debug("extract table failed: %s", e)
                        continue
                    if not widths:
                        continue
                    out.append({
                        "text": text_matrix,
                        "widths_pt": widths,
                        "page_num": page.page_number - 1,
                    })
    except Exception as e:
        log.warning("pdfplumber open failed: %s", e)
    return out


def _docx_table_text(table) -> list[list[str]]:
    return [[_normalize(c.text or "") for c in row.cells] for row in table.rows]


def _docx_table_n_cols(table) -> int:
    """取 docx table 的「最大欄數」— 用 tblGrid 的 gridCol 數量為準（含 merge 後
    的完整欄結構），fallback 用 row.cells 最大值。"""
    grid = table._element.find(qn("w:tblGrid"))
    if grid is not None:
        cols = grid.findall(qn("w:gridCol"))
        if cols:
            return len(cols)
    return max((len(r.cells) for r in table.rows), default=0)


def _merge_smallest_into_neighbor(widths: list[float], target_len: int) -> list[float]:
    """把多出的窄欄合併進相鄰欄，逐步收斂到 target_len。優先合併 < MIN_COL_WIDTH_PT
    的欄，否則合併最窄欄。回新 widths（長度 == target_len 或原樣若無法縮）。"""
    w = list(widths)
    while len(w) > target_len:
        # 找候選 idx：優先 < MIN，否則整體最窄
        narrow_idxs = [i for i, x in enumerate(w) if x < MIN_COL_WIDTH_PT]
        if narrow_idxs:
            i = narrow_idxs[0]
        else:
            i = min(range(len(w)), key=lambda k: w[k])
        # 合併方向：靠左合（若 i==0 合到右；否則合到左鄰）
        if i == 0:
            w[1] += w[0]
            w.pop(0)
        else:
            w[i - 1] += w[i]
            w.pop(i)
    return w


def _set_tblGrid(table, twips_widths: list[int]) -> None:
    """覆寫 w:tblGrid 為新 widths。原 gridCol 元素被全清。"""
    tbl_el = table._element
    grid = tbl_el.find(qn("w:tblGrid"))
    if grid is None:
        grid = tbl_el.makeelement(qn("w:tblGrid"), {})
        # tblPr 之後、第一個 tr 之前插入
        tblPr = tbl_el.find(qn("w:tblPr"))
        if tblPr is not None:
            tblPr.addnext(grid)
        else:
            tbl_el.insert(0, grid)
    # 清舊 gridCol
    for old in grid.findall(qn("w:gridCol")):
        grid.remove(old)
    for w in twips_widths:
        gc = grid.makeelement(qn("w:gridCol"), {qn("w:w"): str(int(w))})
        grid.append(gc)


def _cell_grid_span(cell_el) -> int:
    tcPr = cell_el.find(qn("w:tcPr"))
    if tcPr is None:
        return 1
    gs = tcPr.find(qn("w:gridSpan"))
    if gs is None:
        return 1
    try:
        return max(1, int(gs.get(qn("w:val")) or 1))
    except Exception:
        return 1


def _set_cell_tcw(cell_el, twips_w: int) -> None:
    tcPr = cell_el.find(qn("w:tcPr"))
    if tcPr is None:
        tcPr = cell_el.makeelement(qn("w:tcPr"), {})
        cell_el.insert(0, tcPr)
    tcW = tcPr.find(qn("w:tcW"))
    if tcW is None:
        tcW = tcPr.makeelement(qn("w:tcW"), {})
        tcPr.append(tcW)
    tcW.set(qn("w:type"), "dxa")
    tcW.set(qn("w:w"), str(int(twips_w)))


def _apply_widths_to_table(table, twips_widths: list[int]) -> int:
    """寫 tblGrid + 每列每個 cell 的 tcW（依 gridSpan 累加連續欄寬）。
    回更動的 cell 數。"""
    _set_tblGrid(table, twips_widths)
    n_cols = len(twips_widths)
    n_changed = 0
    for row in table.rows:
        # 遍歷該列的 tc element（避免 row.cells 自動展開 merged cells 多次）
        tr_el = row._element
        col_idx = 0
        for tc_el in tr_el.findall(qn("w:tc")):
            span = _cell_grid_span(tc_el)
            if col_idx >= n_cols:
                break
            end_idx = min(col_idx + span, n_cols)
            total_w = sum(twips_widths[col_idx:end_idx])
            if total_w > 0:
                _set_cell_tcw(tc_el, total_w)
                n_changed += 1
            col_idx = end_idx
    return n_changed


def fix_table_bbox_width(docx_doc, pdf_truth, alignment, *, pdf_path=None) -> dict:
    """主入口。pdf_path 是原 PDF 路徑（由 pipeline 帶入）。"""
    if not pdf_path:
        return {"fixer": "table_bbox_width", "applied_tables": 0,
                "skipped": "no pdf_path"}
    pdf_tables = _extract_pdfplumber_tables_with_widths(pdf_path)
    if not pdf_tables:
        return {"fixer": "table_bbox_width", "applied_tables": 0,
                "skipped": "pdfplumber no tables"}
    docx_tables = list(docx_doc.tables)
    if not docx_tables:
        return {"fixer": "table_bbox_width", "applied_tables": 0,
                "pdf_tables": len(pdf_tables), "docx_tables": 0}

    applied = 0
    cells_changed = 0
    skipped_col_mismatch = 0
    pdf_consumed: set = set()
    for d_tbl in docx_tables:
        d_text = _docx_table_text(d_tbl)
        d_cols = _docx_table_n_cols(d_tbl)
        # 找順序內第一個 shape match 的 pdfplumber table
        best_pi = -1
        for pi, p in enumerate(pdf_tables):
            if pi in pdf_consumed:
                continue
            if _shape_match(d_text, p["text"]):
                best_pi = pi
                break
        if best_pi < 0:
            continue
        pdf_consumed.add(best_pi)
        widths_pt = pdf_tables[best_pi]["widths_pt"]
        p_cols = len(widths_pt)
        # 對齊 col 數：多了就合併最窄 / spacer；少於 docx 就 skip
        if p_cols == d_cols:
            aligned = widths_pt
        elif p_cols > d_cols and (p_cols - d_cols) <= 3:
            aligned = _merge_smallest_into_neighbor(widths_pt, d_cols)
            if len(aligned) != d_cols:
                skipped_col_mismatch += 1
                continue
        else:
            skipped_col_mismatch += 1
            continue
        # 健全性檢查：總寬要合理（30 pt ~ 800 pt 範圍）
        total_pt = sum(aligned)
        if total_pt < 30 or total_pt > 1500:
            skipped_col_mismatch += 1
            continue
        twips = [max(1, round(w * TWIPS_PER_PT)) for w in aligned]
        n = _apply_widths_to_table(d_tbl, twips)
        if n > 0:
            applied += 1
            cells_changed += n

    return {
        "fixer": "table_bbox_width",
        "applied_tables": applied,
        "cells_changed": cells_changed,
        "skipped_col_mismatch": skipped_col_mismatch,
        "pdf_tables": len(pdf_tables),
        "docx_tables": len(docx_tables),
    }
