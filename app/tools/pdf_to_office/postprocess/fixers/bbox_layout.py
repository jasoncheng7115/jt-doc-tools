"""bbox / 位置感知 layout fixer（Sprint B #1 + #8）。

依 user 反饋「重點不是拆什麼資料文，是排版是位置」，本 fixer 用 PDFTruth bbox
真值座標分析 + 修正版面：

1) **多欄偵測**：blocks 的 bbox X 中心點若呈雙峰（左半 + 右半中間有 gap）→
   視為 2-column layout。

2) **form-vs-article 啟發式**（Sprint B 新加）：對每頁判定是「表單 / 表格類」
   還是「文章類」。表單類即使被誤判為多欄也不重排，因為表單裡同列左右兩欄
   是同筆資料（標籤 + 值），重排會把整份排版搞錯。

   form 訊號（任一觸發即判 form）：
   - drawings 數量 > 60（線條 / 矩形密度高 → 真實表格）
   - 80% 以上 text block 是「≤ 2 行 + 文字 < 30 字元」短欄位（典型表單標籤 / 值）
   - 真實有表格畫格（矩形 drawings 總面積 > 頁面 25%）
   - 多欄偵測時，任一 text block 橫跨 boundary_x 且寬度 > 頁寬 60%（橫貫整頁
     的標題 / 段落出現在「多欄」頁，幾乎一定不是真的多欄）

3) **多欄 linearize 修正**：對 article-like 多欄頁，重排 docx 段落讓「左欄全部
   由上而下 → 右欄全部由上而下」（正常閱讀順序）。

4) **Y-序段落 reorder**：對 article-like 頁，docx paragraphs 對應 PDF block 的
   (page, y_top) 鍵不單調遞增 → 重排。

**安全 reorder 規則**：
- 只重排「連續 matched paragraphs」run — 中間若插入表格 / 未 match 段落就斷
  run，run 內各自獨立排序，不跨 run 搬。降低破壞性
- 表格 element 永不動
- match_rate < 0.5 整支 skip
- form-classified 頁的段落不參與 reorder
"""
from __future__ import annotations

import logging

from docx.oxml.ns import qn

log = logging.getLogger(__name__)


# --- helpers -----------------------------------------------------------------

def _block_x_center(block) -> float:
    x0, _, x1, _ = block.bbox
    return (x0 + x1) / 2.0


def _block_y_top(block) -> float:
    return block.bbox[1]


def _block_width(block) -> float:
    x0, _, x1, _ = block.bbox
    return max(0.0, x1 - x0)


def _detect_multi_column(blocks, page_width: float,
                         gap_threshold_ratio: float = 0.15) -> dict:
    """偵測 2-column layout — block X 中心若分兩峰 + 中間 gap > 頁寬 15%。"""
    if len(blocks) < 6 or page_width <= 0:
        return {"is_multi_column": False, "column_count": 1}
    centers = sorted(_block_x_center(b) for b in blocks)
    if len(centers) < 4:
        return {"is_multi_column": False, "column_count": 1}
    diffs = [(centers[i + 1] - centers[i], i) for i in range(len(centers) - 1)]
    max_gap, idx = max(diffs)
    if max_gap < page_width * gap_threshold_ratio:
        return {"is_multi_column": False, "column_count": 1,
                "max_gap_pt": max_gap}
    left = centers[: idx + 1]
    right = centers[idx + 1:]
    if len(left) < 2 or len(right) < 2:
        return {"is_multi_column": False, "column_count": 1}
    boundary_x = (max(left) + min(right)) / 2.0
    return {
        "is_multi_column": True,
        "column_count": 2,
        "left_band": (round(min(left), 1), round(max(left), 1)),
        "right_band": (round(min(right), 1), round(max(right), 1)),
        "gap_pt": round(max_gap, 1),
        "boundary_x": boundary_x,
    }


def _classify_form_or_article(page, text_blocks, mc_info: dict) -> dict:
    """form-vs-article 啟發式。回 {is_form, reasons[]}。"""
    reasons: list[str] = []
    page_w = float(page.width) if page.width else 0.0
    page_h = float(page.height) if page.height else 0.0
    page_area = page_w * page_h
    is_form = False

    # 訊號 A：drawings 密度高
    n_drawings = len(page.drawings or [])
    if n_drawings > 60:
        reasons.append(f"drawings>{60}({n_drawings})")
        is_form = True

    # 訊號 B：短欄位 block 比例高（典型表單）
    if text_blocks:
        short_count = 0
        for b in text_blocks:
            n_lines = len(b.lines)
            n_chars = len((b.text or "").strip())
            if n_lines <= 2 and n_chars < 30:
                short_count += 1
        short_ratio = short_count / len(text_blocks)
        if short_ratio >= 0.8 and len(text_blocks) >= 5:
            reasons.append(f"short_blocks={short_ratio:.2f}")
            is_form = True

    # 訊號 C：矩形 drawing 總面積佔頁 > 25%（畫了大表格框）
    if page_area > 0:
        rect_area = 0.0
        for drw in page.drawings or []:
            if drw.type == "rect":
                x0, y0, x1, y1 = drw.bbox
                w = max(0.0, x1 - x0)
                h = max(0.0, y1 - y0)
                rect_area += w * h
        rect_ratio = rect_area / page_area
        if rect_ratio > 0.25:
            reasons.append(f"rect_area={rect_ratio:.2f}")
            is_form = True

    # 訊號 D：當宣稱多欄時，任一 block 橫跨 boundary 且寬度 > 頁寬 60%
    # → 是橫貫整頁的標題/段落，假多欄
    if mc_info.get("is_multi_column"):
        boundary = mc_info.get("boundary_x", 0)
        if boundary > 0 and page_w > 0:
            for b in text_blocks:
                x0, _, x1, _ = b.bbox
                if x0 < boundary - 5 and x1 > boundary + 5:  # 真的跨界
                    if (x1 - x0) > page_w * 0.6:
                        reasons.append(f"wide_block_crosses_boundary")
                        is_form = True
                        break

    return {"is_form": is_form, "reasons": reasons}


def _docx_para_text(child) -> str:
    texts = []
    for r in child.findall(qn("w:r")):
        for t in r.findall(qn("w:t")):
            if t.text:
                texts.append(t.text)
    return " ".join(" ".join(texts).split())


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
    """產生「閱讀順序」排序鍵。多欄頁優先「欄序 → Y」；單欄純 Y。
    回 (page_num, column_index, y_top)。column_index 0=left, 1=right。"""
    page_num = block.page_num
    col_idx = 0
    if page_info.get("is_multi_column") and not page_info.get("is_form"):
        boundary = page_info.get("boundary_x", 0)
        if _block_x_center(block) > boundary:
            col_idx = 1
    return (page_num, col_idx, _block_y_top(block))


def _group_consecutive_runs(matched: list[tuple],
                             matched_idx_set: set) -> list[list[tuple]]:
    """把 matched paragraphs（依 child_idx 升序）切成「連續 run」list。
    run 定義：相鄰 matched item 之間，所有 child idx 都在 matched_idx_set 內
    （亦即中間沒有 table / 未 match paragraph）。"""
    runs: list[list[tuple]] = []
    cur: list[tuple] = []
    last_ci = None
    for m in sorted(matched, key=lambda x: x[0]):
        ci = m[0]
        if last_ci is None:
            cur = [m]
        else:
            gap_ok = all((j in matched_idx_set) for j in range(last_ci + 1, ci))
            if gap_ok:
                cur.append(m)
            else:
                if len(cur) >= 2:
                    runs.append(cur)
                cur = [m]
        last_ci = ci
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def _safe_reorder_runs(body, children: list, matched: list[tuple]) -> int:
    """對連續 matched paragraphs 的每個 run 依 sort_key 排序，回實際被移動的段落數。

    用 list-of-children 重排，避免邊 remove 邊 insert 的 index shift。"""
    if not matched:
        return 0
    matched_idx_set = {m[0] for m in matched}
    runs = _group_consecutive_runs(matched, matched_idx_set)
    moved = 0
    # 直接在 children list 上 swap，最後用 children list 重設 body 順序
    children_mut = list(children)
    any_changed = False
    for run in runs:
        positions = sorted(m[0] for m in run)
        sorted_run = sorted(run, key=lambda x: x[2])
        new_elements = [children[m[0]] for m in sorted_run]
        if [m[0] for m in run] == [m[0] for m in sorted_run]:
            continue  # 已是排好的
        for pos, el in zip(positions, new_elements):
            if children_mut[pos] is not el:
                moved += 1
            children_mut[pos] = el
        any_changed = True
    if not any_changed:
        return 0
    # 把 body 內的 child 全 detach，按 children_mut 順序重 append
    for el in list(body):
        body.remove(el)
    for el in children_mut:
        body.append(el)
    return moved


# --- 主入口 ------------------------------------------------------------------

def fix_bbox_layout(docx_doc, pdf_truth, alignment) -> dict:
    if not pdf_truth or not pdf_truth.pages:
        return {"fixer": "bbox_layout", "skipped": "no pdf_truth"}

    # -- 1) 多欄偵測 + form/article 分類（per page）--
    page_layout: dict[int, dict] = {}
    multi_column_pages: list[dict] = []
    form_pages: list[int] = []
    for pg in pdf_truth.pages:
        text_blocks = [b for b in pg.blocks
                       if b.block_type == "text" and (b.text or "").strip()]
        mc = _detect_multi_column(text_blocks, pg.width)
        cls = _classify_form_or_article(pg, text_blocks, mc)
        info = dict(mc)
        info["is_form"] = cls["is_form"]
        info["form_reasons"] = cls["reasons"]
        page_layout[pg.page_num] = info
        if cls["is_form"]:
            form_pages.append(pg.page_num + 1)
        if mc.get("is_multi_column") and not cls["is_form"]:
            multi_column_pages.append({
                "page": pg.page_num + 1,
                "gap_pt": mc["gap_pt"],
            })

    # -- 2) docx body paragraph 對 PDF block 比對 + 計算排序鍵 --
    body = docx_doc.element.body
    children = list(body)
    para_tag = qn("w:p")

    all_blocks = pdf_truth.all_blocks
    used: set = set()
    matched: list[tuple] = []  # (child_idx, block, sort_key)
    n_eligible = 0
    for ci, child in enumerate(children):
        if child.tag != para_tag:
            continue
        text = _docx_para_text(child)
        if not text or len(text) < 4:
            continue
        n_eligible += 1
        bi, blk = _find_best_pdf_block(text, all_blocks, used)
        if blk is None:
            continue
        page_info = page_layout.get(blk.page_num, {})
        # form-classified 頁的段落不參與 reorder（標 form_skip）
        if page_info.get("is_form"):
            continue
        used.add(bi)
        sort_key = _compute_reading_order_key(blk, page_info)
        matched.append((ci, blk, sort_key))

    # -- 3) Out-of-order 計數（排序前）--
    out_of_order = 0
    for i in range(1, len(matched)):
        if matched[i][2] < matched[i - 1][2]:
            out_of_order += 1

    match_rate = (len(matched) / n_eligible) if n_eligible else 0.0

    # -- 4) Reorder 條件：match_rate >= 0.5 + 有亂序 + matched >= 3 --
    reordered = 0
    if out_of_order > 0 and match_rate >= 0.5 and len(matched) >= 3:
        try:
            reordered = _safe_reorder_runs(body, children, matched)
        except Exception as e:
            log.warning("safe_reorder failed: %s", e)
            reordered = 0

    warnings: list[str] = []
    if multi_column_pages:
        warnings.append(f"多欄頁 {len(multi_column_pages)} 個已嘗試 linearize")
    if form_pages:
        warnings.append(f"form-class 頁 {len(form_pages)} 個未參與 reorder（保留原序）")

    return {
        "fixer": "bbox_layout",
        "matched_paragraphs": len(matched),
        "out_of_order_paragraphs": out_of_order,
        "match_rate": round(match_rate, 2),
        "reordered_paragraphs": reordered,
        "multi_column_pages": multi_column_pages,
        "form_pages": form_pages,
        "warning": "；".join(warnings),
    }
