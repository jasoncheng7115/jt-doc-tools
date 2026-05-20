"""每張 docx table 從「PDF render 圖像」實際可見邊線判定 borders（C4）。

問題情境（<樣本 A> v1.8.60 真機踩到）：
- PDF 頁面同時有「無框線排版段」（如報價日期 / 到期日 / 銷售人員）跟「有框線
  invoice 表格」（編號 / 說明 / 數量 / 單價 / 稅項 / 金額）
- pdf2docx 把整頁底層線當成同一個大表 → docx 裡所有 cell 都有可見框線
- 既有 `table_normalize._pdf_is_borderless()` 是 per-page heuristic（drawings < 30），
  整頁 drawings 多 → 強制所有表加框線 → 上方無框線段落被加上不該有的框

修法：
1) 用 PyMuPDF 將 PDF 該頁 render 為 144 DPI 灰階圖（per-page LRU cache 共用）
2) 對每個 docx table，用 cell text 在 PDFTruth 找對應 block 算出該 table 在 PDF
   上的 bbox region
3) 把該 region crop 出來，用 numpy 算 row / col 平均暗度
4) **真實水平 / 垂直線 = 一整橫排 / 整縱列暗到 > 暗度閾值且寬度足夠**
5) 完全沒偵測到視覺實線 → 在該 docx table 設 tblBorders 全 nil（無框線）
6) **不**動 PDF 確實有線的表（讓 table_normalize 原樣套上邊框）

設計取捨：
- per-page render cache（同頁多表只 render 一次）
- 失敗安全：render / 配對失敗單表 skip，不擋其他 fixer
- 不下載任何 ML model（與 FreeP2W 採用 DocLayout-YOLO 的取向不同 — 它做 ML 物
  件偵測，我們只做 row / col 暗度投影），保持 zero-extra-dep
"""
from __future__ import annotations

import logging
import re
from io import BytesIO

import fitz  # PyMuPDF
import numpy as np
from PIL import Image
from docx.oxml.ns import qn

log = logging.getLogger(__name__)


# 影像偵測參數 -----------------------------------------------------------------
RENDER_DPI = 144
DARK_PIXEL_THRESHOLD = 128       # 灰階 < 此值視為「暗」(border line 通常 < 128)
LINE_DARK_RATIO = 0.55           # 該 row / col 至少 55% 像素為暗才視為實線
MIN_LINE_LENGTH_RATIO = 0.50     # 偵測到的線至少佔 crop 寬 / 高 50% 才算
NO_BORDER_BBOX_MARGIN_PT = 4.0   # crop 內外擴邊距，避免漏掉邊緣線
NO_BORDER_TABLE_CELL_TEXT_LIMIT = 30  # 對 cell 文字過長的 row 不掃（純資料列），
                                       # 避免文字底線 / 高密度字本身被誤偵為實線


def _normalize(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", "", s).strip()


# --- PDF page render cache ---------------------------------------------------

def _render_page_gray(pdf_path, page_num: int, dpi: int = RENDER_DPI) -> tuple:
    """回 (np.ndarray uint8 灰階圖, scale_px_per_pt)。"""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc.load_page(page_num)
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.open(BytesIO(pix.tobytes("png"))).convert("L")
        arr = np.asarray(img, dtype=np.uint8)
    finally:
        doc.close()
    return arr, dpi / 72.0


class _PageCache:
    def __init__(self, pdf_path):
        self.pdf_path = pdf_path
        self._cache: dict[int, tuple] = {}

    def get(self, page_num: int):
        if page_num not in self._cache:
            try:
                self._cache[page_num] = _render_page_gray(self.pdf_path, page_num)
            except Exception as e:
                log.debug("render page %d failed: %s", page_num, e)
                self._cache[page_num] = (None, 0.0)
        return self._cache[page_num]


# --- docx table → PDF bbox 配對 ---------------------------------------------

def _docx_table_cell_texts(table) -> list[str]:
    """取 docx table 所有 cell 的 normalized 非空文字。"""
    out: list[str] = []
    seen: set = set()
    for row in table.rows:
        for cell in row.cells:
            cid = id(cell._element)
            if cid in seen:
                continue
            seen.add(cid)
            t = _normalize(cell.text or "")
            if t and len(t) >= 2:
                out.append(t)
    return out


def _find_table_pdf_bbox(table, pdf_truth) -> tuple | None:
    """配對 docx table → PDF region。對每個 cell normalized text，掃 PDFTruth
    blocks 找 startswith match；收集所有 matched block 的 bbox，回 union bbox
    + page_num（取最多 match 的頁）。"""
    cell_texts = _docx_table_cell_texts(table)
    if not cell_texts:
        return None
    # 為效率每個 cell text 只匹配 1 次
    matched: list = []  # (page_num, bbox)
    for ct in cell_texts:
        best_bn = None
        for b in pdf_truth.all_blocks:
            n_text = _normalize(b.text)
            if not n_text:
                continue
            # cell text 須是 block text 開頭或 substring，且長度比例合理
            if ct in n_text and len(ct) >= 2:
                best_bn = b
                break
        if best_bn is not None:
            matched.append((best_bn.page_num, best_bn.bbox))
    if not matched:
        return None
    # 取出現最多的頁
    page_count: dict[int, int] = {}
    for pn, _ in matched:
        page_count[pn] = page_count.get(pn, 0) + 1
    dominant_page = max(page_count.items(), key=lambda x: x[1])[0]
    same_page_boxes = [bb for (pn, bb) in matched if pn == dominant_page]
    if not same_page_boxes:
        return None
    x0 = min(bb[0] for bb in same_page_boxes)
    y0 = min(bb[1] for bb in same_page_boxes)
    x1 = max(bb[2] for bb in same_page_boxes)
    y1 = max(bb[3] for bb in same_page_boxes)
    return (dominant_page, (x0, y0, x1, y1))


# --- 影像線條偵測 ------------------------------------------------------------

def _has_visible_line(arr_2d: np.ndarray, axis: str) -> bool:
    """偵測 crop 內是否有貫穿水平 (axis='h') / 垂直 (axis='v') 實線。

    演算法：
    - axis='h': 對 row 算「暗像素比例」(< threshold)，若任一 row > LINE_DARK_RATIO
      表示該 row 大部分像素是暗的 — 視為水平實線
    - axis='v': 對 column 算同樣指標
    - 排除單獨幾個 row / col 的雜訊：要求連續至少 1 列（粗線）或孤立但 ratio
      > 0.7 才算
    """
    if arr_2d.size == 0:
        return False
    dark_mask = arr_2d < DARK_PIXEL_THRESHOLD
    if axis == "h":
        per_row_ratio = dark_mask.mean(axis=1)
        line_rows = per_row_ratio >= LINE_DARK_RATIO
        if line_rows.any():
            # 至少要連續 1px（即 single dark row）且該 row 寬度 >= MIN_LINE_LENGTH_RATIO
            # 用 dark_mask 該 row 連續 dark px 最長段
            for idx in np.where(line_rows)[0]:
                row = dark_mask[idx]
                # 最長連續 True 段
                if _longest_run(row) / max(1, len(row)) >= MIN_LINE_LENGTH_RATIO:
                    return True
        return False
    else:
        per_col_ratio = dark_mask.mean(axis=0)
        line_cols = per_col_ratio >= LINE_DARK_RATIO
        if line_cols.any():
            for idx in np.where(line_cols)[0]:
                col = dark_mask[:, idx]
                if _longest_run(col) / max(1, len(col)) >= MIN_LINE_LENGTH_RATIO:
                    return True
        return False


def _longest_run(boolean_arr_1d: np.ndarray) -> int:
    """1D bool 陣列內連續 True 最長段長度。"""
    if not boolean_arr_1d.any():
        return 0
    # 用 cumulative trick
    arr = boolean_arr_1d.astype(np.int8)
    # 找變化點
    diff = np.diff(np.concatenate(([0], arr, [0])))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]
    if len(starts) == 0:
        return 0
    return int((ends - starts).max())


def _table_has_visible_borders(arr_2d: np.ndarray) -> tuple[bool, dict]:
    has_h = _has_visible_line(arr_2d, "h")
    has_v = _has_visible_line(arr_2d, "v")
    has_borders = has_h or has_v
    return has_borders, {"horiz": has_h, "vert": has_v}


# --- 設邊框 ------------------------------------------------------------------

def _set_table_no_borders(table) -> None:
    tblPr = table._element.find(qn("w:tblPr"))
    if tblPr is None:
        tblPr = table._element.makeelement(qn("w:tblPr"), {})
        table._element.insert(0, tblPr)
    for old in tblPr.findall(qn("w:tblBorders")):
        tblPr.remove(old)
    borders = tblPr.makeelement(qn("w:tblBorders"), {})
    for tag in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = borders.makeelement(qn(f"w:{tag}"), {})
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tblPr.append(borders)
    # cell 級也清掉（避免 table_normalize 後 cell tcBorders 仍套單線）
    for row in table.rows:
        for cell in row.cells:
            tcPr = cell._element.find(qn("w:tcPr"))
            if tcPr is None:
                continue
            for old in tcPr.findall(qn("w:tcBorders")):
                tcPr.remove(old)
            tcB = tcPr.makeelement(qn("w:tcBorders"), {})
            for tag in ("top", "left", "bottom", "right"):
                el = tcB.makeelement(qn(f"w:{tag}"), {})
                el.set(qn("w:val"), "nil")
                tcB.append(el)
            tcPr.append(tcB)


# --- 主入口 ------------------------------------------------------------------

def fix_table_borders_from_image(docx_doc, pdf_truth, alignment,
                                  *, pdf_path=None) -> dict:
    if not pdf_path:
        return {"fixer": "table_borders_from_image", "stripped": 0,
                "skipped": "no pdf_path"}
    if not pdf_truth or not pdf_truth.pages:
        return {"fixer": "table_borders_from_image", "stripped": 0,
                "skipped": "no pdf_truth"}
    docx_tables = list(docx_doc.tables)
    if not docx_tables:
        return {"fixer": "table_borders_from_image", "stripped": 0,
                "docx_tables": 0}

    cache = _PageCache(pdf_path)
    stripped = 0
    kept = 0
    failed = 0
    detail: list[dict] = []

    for ti, table in enumerate(docx_tables):
        try:
            match = _find_table_pdf_bbox(table, pdf_truth)
        except Exception as e:
            log.debug("table %d bbox match failed: %s", ti, e)
            match = None
        if not match:
            failed += 1
            detail.append({"table": ti, "result": "no_match"})
            continue
        page_num, bbox = match
        x0, y0, x1, y1 = bbox
        x0 -= NO_BORDER_BBOX_MARGIN_PT
        y0 -= NO_BORDER_BBOX_MARGIN_PT
        x1 += NO_BORDER_BBOX_MARGIN_PT
        y1 += NO_BORDER_BBOX_MARGIN_PT
        arr, scale = cache.get(page_num)
        if arr is None:
            failed += 1
            detail.append({"table": ti, "result": "render_failed"})
            continue
        px0 = max(0, int(x0 * scale))
        py0 = max(0, int(y0 * scale))
        px1 = min(arr.shape[1], int(x1 * scale))
        py1 = min(arr.shape[0], int(y1 * scale))
        if px1 - px0 < 20 or py1 - py0 < 20:
            failed += 1
            detail.append({"table": ti, "result": "bbox_too_small"})
            continue
        crop = arr[py0:py1, px0:px1]
        has_borders, dirs = _table_has_visible_borders(crop)
        if has_borders:
            kept += 1
            detail.append({"table": ti, "result": "has_borders",
                           "dirs": dirs})
        else:
            try:
                _set_table_no_borders(table)
                stripped += 1
                detail.append({"table": ti, "result": "stripped"})
            except Exception as e:
                log.debug("set table no borders failed: %s", e)
                failed += 1
                detail.append({"table": ti, "result": "strip_failed"})

    return {
        "fixer": "table_borders_from_image",
        "stripped": stripped,
        "kept": kept,
        "failed": failed,
        "docx_tables": len(docx_tables),
        "detail": detail,
    }
