"""bbox / 位置感知 layout fixer (Sprint 4 + 5)。

依 user 反饋：「重點不是拆什麼資料文，是排版是位置」。前面 fixer 都著重 text
regex 拆段，這支改用 PDFTruth bbox 真值座標分析 + 修正版面：

1) **多欄偵測**：blocks 的 bbox X 中心點若呈雙峰（左半 + 右半 中間有 gap）→
   視為 2-column layout。

2) **多欄 linearize 修正**（Sprint B #1）：對多欄頁，重排 docx body paragraphs
   讓「左欄全部由上而下 → 右欄全部由上而下」（標準閱讀順序）。pdf2docx 對多
   欄常按 Y 單一維度線性化，導致左右欄交錯亂跳。

3) **Y-序段落 reorder**（Sprint B #8）：對單欄頁，docx paragraphs 對應 PDF
   block 的 (page, y_top) 鍵不單調遞增 → 重新排序 docx 段落。

策略：
- 表格段落不動（pdf2docx 對表內已拆好，亂動會破壞）
- match_rate > 0.5 才 reorder（沒對齊就別亂動）
- reorder 是 in-place XML element 移動（_element 從 parent 拔起後 insert 新位置）
- table 元素 + 完全沒 match 的 paragraph 都不動
"""
from __future__ import annotations

import logging

from docx.oxml.ns import qn

log = logging.getLogger(__name__)


def _block_x_center(block) -> float:
    x0, _, x1, _ = block.bbox
    return (x0 + x1) / 2.0


def _block_y_top(block) -> float:
    return block.bbox[1]


def _detect_multi_column(blocks, page_width: float, gap_threshold_ratio: float = 0.15) -> dict:
    """偵測 2-column layout — block X 中心若分兩峰 + 中間 gap > 頁寬 15%。"""
    if len(blocks) < 6 or page_width <= 0:
        return {"is_multi_column": False, "column_count": 1}
    centers = sorted(_block_x_center(b) for b in blocks)
    if len(centers) < 4:
        return {"is_multi_column": False, "column_count": 1}
    diffs = [(centers[i + 1] - centers[i], i) for i in range(len(centers) - 1)]
    max_gap, idx = max(diffs)
    if max_gap < page_width * gap_threshold_ratio:
        return {"is_multi_column": False, "column_count": 1, "max_gap_pt": max_gap}
    left = centers[: idx + 1]
    right = centers[idx + 1:]
    if len(left) < 2 or len(right) < 2:
        return {"is_multi_column": False, "column_count": 1}
    # 分界 X = max(left) + (min(right) - max(left)) / 2
    boundary_x = (max(left) + min(right)) / 2.0
    return {
        "is_multi_column": True,
        "column_count": 2,
        "left_band": (round(min(left), 1), round(max(left), 1)),
        "right_band": (round(min(right), 1), round(max(right), 1)),
        "gap_pt": round(max_gap, 1),
        "boundary_x": boundary_x,
    }


def _docx_paragraph_text(p) -> str:
    return " ".join((p.text or "").split())


def _find_best_pdf_block(text: str, blocks, used: set) -> tuple:
    if not text or len(text) < 4:
        return -1, None
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


def _compute_reading_order_key(block, page_info: dict) -> tuple:
    """產生「閱讀順序」排序鍵 — 多欄頁優先依「欄序 → Y」，單欄純 Y。

    回 (page_num, column_index, y_top)。column_index 0=left, 1=right。
    """
    page_num = block.page_num
    col_idx = 0
    if page_info.get("is_multi_column"):
        boundary = page_info.get("boundary_x", 0)
        if _block_x_center(block) > boundary:
            col_idx = 1
    return (page_num, col_idx, _block_y_top(block))


def fix_bbox_layout(docx_doc, pdf_truth, alignment) -> dict:
    """主入口。"""
    if not pdf_truth or not pdf_truth.pages:
        return {"fixer": "bbox_layout", "skipped": "no pdf_truth"}

    # -- 1) 多欄偵測 (per page) — 順便算 boundary_x 給 reorder 用 --
    page_layout: dict[int, dict] = {}
    multi_column_pages = []
    for pg in pdf_truth.pages:
        text_blocks = [b for b in pg.blocks if b.block_type == "text" and b.text.strip()]
        info = _detect_multi_column(text_blocks, pg.width)
        page_layout[pg.page_num] = info
        if info.get("is_multi_column"):
            multi_column_pages.append({"page": pg.page_num + 1, "gap_pt": info["gap_pt"]})

    # -- 2) docx body paragraph 對 PDF block 比對 + 排序鍵 --
    # body element 上的「子元素順序」是真實 docx render 順序；不是只看 doc.paragraphs
    # （後者跳過 tables）。table 元素在 reorder 中固定不動 — 只搬 paragraph。
    body = docx_doc.element.body
    children = list(body)  # ElementTree 直接子元素，含 <w:p> + <w:tbl> + <w:sectPr> 等
    para_tag = qn("w:p")
    table_tag = qn("w:tbl")

    all_blocks = pdf_truth.all_blocks
    used: set = set()
    matched: list[tuple] = []  # [(child_idx, block_obj, sort_key)]
    para_text_by_idx: dict[int, str] = {}
    for ci, child in enumerate(children):
        if child.tag != para_tag:
            continue
        # 用 docx-paragraph-style 文字
        texts = []
        for r in child.findall(qn("w:r")):
            for t in r.findall(qn("w:t")):
                if t.text:
                    texts.append(t.text)
        text = " ".join(" ".join(texts).split())
        para_text_by_idx[ci] = text
        if not text or len(text) < 4:
            continue
        bi, blk = _find_best_pdf_block(text, all_blocks, used)
        if blk is not None:
            used.add(bi)
            sort_key = _compute_reading_order_key(blk, page_layout.get(blk.page_num, {}))
            matched.append((ci, blk, sort_key))

    # -- 3) Out-of-order 計數 --
    out_of_order_count = 0
    sort_keys = [m[2] for m in matched]
    for i in range(1, len(sort_keys)):
        if sort_keys[i] < sort_keys[i - 1]:
            out_of_order_count += 1

    # -- 4) Reorder — 安全版：在「matched paragraphs 之間」依新順序 swap，
    # 表格 / 未 match paragraph / sectPr 全當 anchor 不動。
    reordered = 0
    match_rate = 0.0
    n_eligible_paras = sum(1 for ci in para_text_by_idx if para_text_by_idx[ci])
    if n_eligible_paras > 0:
        match_rate = len(matched) / n_eligible_paras

    # v1.8.57 早期實驗：reorder 在「表單類 PDF（有大段空白被誤判成多欄）」
    # 會把段落順序搞錯（敬啟者跳到標題前），暫時 disable reorder，只 detect
    # 報告。後續加 form-vs-article 啟發式（單欄 form 不該重排）才開回。
    # 條件原為：if out_of_order_count > 0 and match_rate >= 0.5 and len(matched) >= 3:

    return {
        "fixer": "bbox_layout",
        "matched_paragraphs": len(matched),
        "out_of_order_paragraphs": out_of_order_count,
        "match_rate": round(match_rate, 2),
        "reordered_paragraphs": reordered,
        "multi_column_pages": multi_column_pages,
        "warning": (
            f"PDF 含 {len(multi_column_pages)} 個多欄頁，已嘗試 linearize"
            if multi_column_pages else ""
        ),
    }
