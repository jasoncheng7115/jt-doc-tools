"""偵測 docx vMerge cell 漏掉 PDFTruth 內存在的 label，拆 merge 補回（v1.8.62 D3）。

問題情境（<供應商表類>表）：
- PDF 有兩列「業務聯絡人 | 電話 | 信箱」「財會聯絡人 | 電話 | 信箱」(label 重複)
- pdf2docx 看到兩列「電話 / 信箱」label 一致 → 自動 vMerge (vertical merge)
- 結果：docx 變成「業務聯絡人 | 電話 | 信箱」「財會聯絡人 | (空白) | (空白)」
  — 第二列的 label 消失被 merge 吃了

修法：
1) 對每個 docx table，找含 `<w:vMerge val="restart">` + 下方接 `<w:vMerge>` (continue)
   的 cell 模式
2) 對「被 merge 的下方 cell」位置在 PDF 上的對應 bbox（從同列其他非 merge cell
   推算），看 PDFTruth 該位置是否有獨立 text block
3) 若 PDF 有獨立 text → docx 該被 merge 的 cell **本來不該被合併** → 拆 vMerge
   + 補 label

保守策略：
- 只對「vMerge 下方 cell 完全空」+「PDF 同位置有獨立 text」才拆 merge
- 拆 merge = 移除 `<w:vMerge>` element，cell 變獨立
- 補 label = 把 PDFTruth 的文字寫入該 cell
"""
from __future__ import annotations

import logging
import re

from docx.oxml.ns import qn

log = logging.getLogger(__name__)


def _normalize(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", "", s).strip()


def _tc_text(tc_el) -> str:
    parts = []
    for t in tc_el.iter(qn("w:t")):
        if t.text:
            parts.append(t.text)
    return "".join(parts)


def _has_vmerge(tc_el) -> tuple[bool, str]:
    """回 (有 vMerge, val)。val = 'restart' / 'continue' (預設 continue)。"""
    tcPr = tc_el.find(qn("w:tcPr"))
    if tcPr is None:
        return False, ""
    vMerge = tcPr.find(qn("w:vMerge"))
    if vMerge is None:
        return False, ""
    val = vMerge.get(qn("w:val")) or "continue"
    return True, val


def _remove_vmerge(tc_el) -> bool:
    tcPr = tc_el.find(qn("w:tcPr"))
    if tcPr is None:
        return False
    vMerge = tcPr.find(qn("w:vMerge"))
    if vMerge is not None:
        tcPr.remove(vMerge)
        return True
    return False


def _set_cell_text(tc_el, text: str) -> None:
    """寫入 cell 第一段第一個 run（無 run 則新增）。"""
    p_el = tc_el.find(qn("w:p"))
    if p_el is None:
        p_el = tc_el.makeelement(qn("w:p"), {})
        tc_el.append(p_el)
    # 清原 runs
    for r in p_el.findall(qn("w:r")):
        p_el.remove(r)
    new_r = p_el.makeelement(qn("w:r"), {})
    new_t = new_r.makeelement(qn("w:t"), {qn("xml:space"): "preserve"})
    new_t.text = text
    new_r.append(new_t)
    p_el.append(new_r)


def _find_table_pdf_region(table, pdf_truth):
    """配對 docx table → PDF region（用非空 cell text 找 PDFTruth blocks）。
    回 (page_num, (x0,y0,x1,y1)) 或 None。"""
    cell_texts = []
    for row in table.rows:
        for tc_el in row._element.findall(qn("w:tc")):
            t = _normalize(_tc_text(tc_el))
            if t and len(t) >= 2:
                cell_texts.append(t)
    if not cell_texts:
        return None
    matched_bboxes: dict[int, list] = {}
    for ct in cell_texts:
        for b in pdf_truth.all_blocks:
            n_text = _normalize(b.text)
            if not n_text:
                continue
            if ct in n_text:
                matched_bboxes.setdefault(b.page_num, []).append(b.bbox)
                break
    if not matched_bboxes:
        return None
    dominant = max(matched_bboxes.items(), key=lambda x: len(x[1]))
    pn = dominant[0]
    boxes = dominant[1]
    return (pn, (min(b[0] for b in boxes), min(b[1] for b in boxes),
                  max(b[2] for b in boxes), max(b[3] for b in boxes)))


def _unpack_region(region) -> tuple:
    """region 可能是 (page_num, (x0,y0,x1,y1)) 或 (x0,y0,x1,y1)。回 (x0,y0,x1,y1)。"""
    if isinstance(region, tuple) and len(region) == 2 and isinstance(region[1], tuple):
        return region[1]
    return region


def _row_y_range(table, row_idx: int, region) -> tuple:
    """估算 docx table row_idx 在 PDF region 內對應的 y range。"""
    rows = list(table.rows)
    n_rows = len(rows)
    if n_rows == 0:
        return (0, 0)
    _, ry0, _, ry1 = _unpack_region(region)
    row_h = (ry1 - ry0) / n_rows
    return (ry0 + row_idx * row_h, ry0 + (row_idx + 1) * row_h)


def _col_x_range(table, row_idx: int, col_idx: int, region) -> tuple:
    """估算 docx 表 row N col M 在 PDF region 內對應的 x range。"""
    rx0, _, rx1, _ = _unpack_region(region)
    rows = list(table.rows)
    if row_idx >= len(rows):
        return (rx0, rx1)
    row = rows[row_idx]
    tcs = row._element.findall(qn("w:tc"))
    n_cols = len(tcs)
    if n_cols == 0:
        return (rx0, rx1)
    col_w = (rx1 - rx0) / n_cols
    return (rx0 + col_idx * col_w, rx0 + (col_idx + 1) * col_w)


def _pdf_text_in_xy_range(pdf_truth, page_num: int,
                           x_range: tuple, y_range: tuple) -> str:
    """從 PDFTruth 取指定 (x_range, y_range) 範圍內 text。"""
    try:
        page = pdf_truth.pages[page_num]
    except IndexError:
        return ""
    x0, x1 = x_range
    y0, y1 = y_range
    parts = []
    for b in page.blocks:
        if b.block_type != "text":
            continue
        for ln in b.lines:
            lx0, ly0, lx1, ly1 = ln.bbox
            # 中心點在範圍內就算
            lcx = (lx0 + lx1) / 2.0
            lcy = (ly0 + ly1) / 2.0
            if x0 <= lcx <= x1 and y0 <= lcy <= y1:
                t = (ln.text or "").strip()
                if t:
                    parts.append(t)
    return " ".join(parts).strip()


def fix_table_unmerge_with_pdf_labels(docx_doc, pdf_truth, alignment) -> dict:
    if not pdf_truth or not pdf_truth.pages:
        return {"fixer": "table_unmerge_with_pdf_labels", "unmerged": 0,
                "skipped": "no pdf_truth"}
    tables = list(docx_doc.tables)
    if not tables:
        return {"fixer": "table_unmerge_with_pdf_labels", "unmerged": 0}

    unmerged = 0
    filled = 0

    for table in tables:
        try:
            match = _find_table_pdf_region(table, pdf_truth)
        except Exception as e:
            log.debug("table region match failed: %s", e)
            match = None
        if not match:
            continue
        page_num, region_bbox = match

        rows = list(table.rows)
        for r_idx, row in enumerate(rows):
            tcs = row._element.findall(qn("w:tc"))
            for c_idx, tc_el in enumerate(tcs):
                has_vm, vm_val = _has_vmerge(tc_el)
                if not has_vm or vm_val == "restart":
                    continue
                # 此 cell 是 vMerge continue —被合併進上方的延續
                # 檢查該 cell 是否完全空（沒實際文字 — vMerge continue 預設應該空）
                if _tc_text(tc_el).strip():
                    continue
                # 估算該 cell 對應 PDF (x_range, y_range)
                try:
                    y_range = _row_y_range(table, r_idx, (page_num, region_bbox))
                    x_range = _col_x_range(table, r_idx, c_idx, (page_num, region_bbox))
                except Exception:
                    continue
                pdf_text = _pdf_text_in_xy_range(pdf_truth, page_num,
                                                  x_range, y_range)
                if not pdf_text or len(pdf_text) > 30:
                    continue
                # PDF 該位置有獨立短 text → 此 vMerge 是 pdf2docx 誤合 → 拆
                if _remove_vmerge(tc_el):
                    unmerged += 1
                    try:
                        _set_cell_text(tc_el, pdf_text)
                        filled += 1
                    except Exception as e:
                        log.debug("fill unmerged cell failed: %s", e)

    return {
        "fixer": "table_unmerge_with_pdf_labels",
        "unmerged": unmerged,
        "filled": filled,
        "tables": len(tables),
    }
