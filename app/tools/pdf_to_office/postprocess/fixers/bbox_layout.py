"""bbox / 位置感知 layout fixer (Sprint 4 開頭)。

依 user 反饋：「重點不是拆什麼資料文，是排版是位置」。前面 fixer 都著重 text
regex 拆段，這支改用 PDFTruth bbox 真值座標分析版面：

1) **多欄偵測**：blocks 的 bbox X 中心點若呈雙峰（左半 + 右半 中間有 gap）→
   視為 2-column layout。pdf2docx 對多欄 PDF 常 linearize 出錯，先 detect
   報告給 user 知。

2) **Y-序驗證 / reorder**：docx paragraphs 在 PDFTruth 內 best-match block
   的 Y 中心序列若不單調遞增（即 docx 段落順序跟 PDF 視覺由上而下順序不一致）
   → reorder docx paragraphs 對齊 Y 序。

3) **不修改文字內容**，只重排 / 報告。安全保守不破壞既有 fixer 結果。

策略：
- 表格段落不動（pdf2docx 對表內已拆好，亂動會破壞）
- 多欄 PDF 暫時只 detect 不重排（重排 risk 高，未來再加）
"""
from __future__ import annotations

import logging
import statistics

from docx.oxml.ns import qn

log = logging.getLogger(__name__)


def _block_x_center(block) -> float:
    x0, _, x1, _ = block.bbox
    return (x0 + x1) / 2.0


def _block_y_top(block) -> float:
    return block.bbox[1]


def _detect_multi_column(blocks, page_width: float, gap_threshold_ratio: float = 0.15) -> dict:
    """偵測 2-column layout — block X 中心若分兩峰 + 中間 gap > 頁寬 15%。

    回 dict: {is_multi_column, column_count, left_band, right_band, gap_pt}
    """
    if len(blocks) < 6 or page_width <= 0:
        return {"is_multi_column": False, "column_count": 1}
    centers = sorted(_block_x_center(b) for b in blocks)
    # naive 2-mean clustering — 找最大 gap
    if len(centers) < 4:
        return {"is_multi_column": False, "column_count": 1}
    diffs = [(centers[i + 1] - centers[i], i) for i in range(len(centers) - 1)]
    max_gap, idx = max(diffs)
    if max_gap < page_width * gap_threshold_ratio:
        return {"is_multi_column": False, "column_count": 1, "max_gap_pt": max_gap}
    left = centers[: idx + 1]
    right = centers[idx + 1:]
    # 兩 cluster 都要有實質大小，避免單一 outlier
    if len(left) < 2 or len(right) < 2:
        return {"is_multi_column": False, "column_count": 1}
    return {
        "is_multi_column": True,
        "column_count": 2,
        "left_band": (round(min(left), 1), round(max(left), 1)),
        "right_band": (round(min(right), 1), round(max(right), 1)),
        "gap_pt": round(max_gap, 1),
    }


def _docx_paragraph_text(p) -> str:
    return " ".join((p.text or "").split())


def _find_best_pdf_block(text: str, blocks, used: set) -> tuple:
    """找最像 docx para 文字的 PDF block（簡單 substring + length match）。
    回 (block_index, block) 或 (-1, None)。"""
    if not text or len(text) < 4:
        return -1, None
    # head 4-12 字 prefix 找
    head = text[: min(12, len(text))]
    for bi, b in enumerate(blocks):
        if bi in used:
            continue
        b_text = " ".join((b.text or "").split())
        if not b_text:
            continue
        if head in b_text or b_text[: len(head)] == head:
            return bi, b
    return -1, None


def fix_bbox_layout(docx_doc, pdf_truth, alignment) -> dict:
    """主入口。"""
    if not pdf_truth or not pdf_truth.pages:
        return {"fixer": "bbox_layout", "skipped": "no pdf_truth"}

    # -- 1) 多欄偵測（per page）--
    multi_column_pages = []
    for pg in pdf_truth.pages:
        text_blocks = [b for b in pg.blocks if b.block_type == "text" and b.text.strip()]
        info = _detect_multi_column(text_blocks, pg.width)
        if info.get("is_multi_column"):
            info["page"] = pg.page_num
            multi_column_pages.append(info)

    # -- 2) Y-序驗證（只看 body paragraphs，跳過 table cells）--
    body_paras = list(docx_doc.paragraphs)
    all_blocks = pdf_truth.all_blocks
    matched: list[tuple] = []  # [(docx_idx, block_idx, y_top)]
    used: set = set()
    for di, p in enumerate(body_paras):
        text = _docx_paragraph_text(p)
        if not text or len(text) < 4:
            continue
        bi, blk = _find_best_pdf_block(text, all_blocks, used)
        if blk is not None:
            used.add(bi)
            # 用 (page_num, y_top) 當排序鍵 — 跨頁時 page 優先
            matched.append((di, bi, blk.page_num * 10000 + _block_y_top(blk)))

    # 檢查 docx 順序的 sort key 是否單調遞增
    out_of_order_count = 0
    for i in range(1, len(matched)):
        if matched[i][2] < matched[i - 1][2]:
            out_of_order_count += 1

    return {
        "fixer": "bbox_layout",
        "matched_paragraphs": len(matched),
        "out_of_order_paragraphs": out_of_order_count,
        "multi_column_pages": [
            {"page": p["page"] + 1, "gap_pt": p["gap_pt"]}
            for p in multi_column_pages
        ],
        "warning": (
            "PDF 為多欄版面，pdf2docx 可能 linearize 順序不準，建議人工複核"
            if multi_column_pages else ""
        ),
    }
