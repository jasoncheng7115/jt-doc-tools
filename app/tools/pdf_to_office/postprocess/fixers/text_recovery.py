"""補回 PDFTruth 內有但 docx 抽不到的 text（Sprint B v1.8.62 D1 保守化版）。

歷史脈絡：
- v1.8.59~60 (block-level)：保守但對 4-line block 第 4 行漏抓無能為力（「台灣」）
- v1.8.61 (line-level)：能補回但 anchor 找錯位 → 帶來 regression（「TEST」補到頁首、
  「台灣」黏到「2023 年 06 月 06 日」前）

D1 大保守化 — 只補回**位置可信**的兩種情境：

1) **頂部 title 區（PDF y_top < page_height × 0.15）**：補在 docx body 開頭
2) **底部 footer 區（PDF y_top > page_height × 0.6）**：append 到 docx body 末尾

中段（0.15 ≤ y/h ≤ 0.6）一律不補 — anchor 一定錯。

過濾規則（任一觸發 skip）：
- missing text normalize 後是任一 docx text (含 cell text) 的 substring → 跳過
  （避免「台灣」黏進「台灣 年 06 月 06 日」這類）
- 形似「填空線 / 留白模板」（`____+`, `年__月__日` 留白型）→ 跳過
- 整頁 line miss ratio ≥ 50% → 整頁 skip（pdf2docx 整頁出問題）
- 單頁補回上限 5 段（避免某頁灌爆）
"""
from __future__ import annotations

import logging
import re
from copy import deepcopy

from docx.oxml.ns import qn

log = logging.getLogger(__name__)


SHORT_LINE_MAX_CHARS = 100
PER_PAGE_MISS_RATIO_BAIL = 0.5
MAX_RECOVERED_PER_PAGE = 5
MAX_RECOVERED_TOTAL = 20
TOP_BAND_RATIO = 0.15      # y_top < height × 此值 → 頂部區
BOTTOM_BAND_RATIO = 0.6    # y_top > height × 此值 → 底部區

# 形似「填空線 / 留白模板」pattern — 不補
_FILL_LINE_RE = re.compile(r"^[_\s]+$|^[\s_]+$|^[年月日]+[_\s]*[年月日_\s]*$")
_PLACEHOLDER_DATE_RE = re.compile(r"^[\s_]*年[\s_]*月[\s_]*日[\s_]*$")


def _normalize(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", "", s).strip()


def _docx_para_text(p_el) -> str:
    parts = []
    for t in p_el.iter(qn("w:t")):
        if t.text:
            parts.append(t.text)
    return "".join(parts)


def _collect_docx_text_set(docx_doc) -> set[str]:
    """整份 docx 內 normalized 已存在文字 set（body + table cells）。
    用 etree iter 不靠 python-docx wrapper id（lxml Python wrapper 不穩）。"""
    out: set[str] = set()
    body = docx_doc.element.body
    for p_el in body.iter(qn("w:p")):
        whole = _normalize(_docx_para_text(p_el))
        if whole:
            out.add(whole)
    for tc_el in body.iter(qn("w:tc")):
        parts = []
        for t in tc_el.iter(qn("w:t")):
            if t.text:
                parts.append(t.text)
        whole_cell = _normalize("".join(parts))
        if whole_cell:
            out.add(whole_cell)
    return out


def _is_text_present(needle: str, hay: set[str]) -> bool:
    """needle normalized 後是否完全等於 / 是 haystack 內任一字串的 substring。"""
    nn = _normalize(needle)
    if not nn:
        return True
    if nn in hay:
        return True
    for h in hay:
        if nn in h:
            return True
    return False


def _is_template_placeholder(text: str) -> bool:
    """形似填空線 / 日期留白模板 → True。"""
    s = (text or "").strip()
    if not s:
        return True
    if _FILL_LINE_RE.match(s):
        return True
    if _PLACEHOLDER_DATE_RE.match(s):
        return True
    return False


def _make_paragraph_element(template_p_el, text: str):
    new_p = template_p_el.makeelement(qn("w:p"), {})
    new_r = new_p.makeelement(qn("w:r"), {})
    first_run = template_p_el.find(qn("w:r"))
    if first_run is not None:
        rPr = first_run.find(qn("w:rPr"))
        if rPr is not None:
            new_r.append(deepcopy(rPr))
    new_t = new_r.makeelement(qn("w:t"), {qn("xml:space"): "preserve"})
    new_t.text = text
    new_r.append(new_t)
    new_p.append(new_r)
    return new_p


def _band(y_top: float, page_height: float) -> str:
    if page_height <= 0:
        return "mid"
    r = y_top / page_height
    if r < TOP_BAND_RATIO:
        return "top"
    if r > BOTTOM_BAND_RATIO:
        return "bot"
    return "mid"


def _insert_top(body, text: str, anchor_template) -> object:
    """補在 body 開頭（第一個 w:p 之前）。"""
    new_p = _make_paragraph_element(anchor_template, text)
    # 找 body 的第一個 w:p
    first_p = None
    for child in body:
        if child.tag == qn("w:p"):
            first_p = child
            break
    if first_p is not None:
        first_p.addprevious(new_p)
    else:
        body.append(new_p)
    return new_p


def _append_to_body(body, text: str, anchor_template) -> object:
    """補在 body 末尾（sectPr 之前）。"""
    new_p = _make_paragraph_element(anchor_template, text)
    # 找 sectPr — body 最後通常是 sectPr
    sectPr = None
    for child in body:
        if child.tag == qn("w:sectPr"):
            sectPr = child
    if sectPr is not None:
        sectPr.addprevious(new_p)
    else:
        body.append(new_p)
    return new_p


def fix_text_recovery(docx_doc, pdf_truth, alignment) -> dict:
    if not pdf_truth or not pdf_truth.pages:
        return {"fixer": "text_recovery", "recovered": 0,
                "skipped": "no pdf_truth"}

    docx_text_set = _collect_docx_text_set(docx_doc)
    body = docx_doc.element.body

    # 取 body 內第一個 w:p 當 anchor template（拿來複製 rPr / pPr 樣式）
    anchor_template = None
    for child in body:
        if child.tag == qn("w:p"):
            anchor_template = child
            break
    if anchor_template is None:
        return {"fixer": "text_recovery", "recovered": 0,
                "skipped": "no body paragraph for template"}

    recovered = 0
    per_page_stats: list[dict] = []
    recovered_band: dict[str, int] = {"top": 0, "bot": 0, "mid_skipped": 0,
                                       "placeholder_skipped": 0,
                                       "substring_skipped": 0}

    for pg in pdf_truth.pages:
        if recovered >= MAX_RECOVERED_TOTAL:
            break
        text_blocks = [b for b in pg.blocks
                       if b.block_type == "text" and (b.text or "").strip()]
        if not text_blocks:
            continue

        # 蒐集本頁 lines
        all_lines: list = []
        for b in text_blocks:
            for ln in b.lines:
                if (ln.text or "").strip():
                    all_lines.append(ln)
        if not all_lines:
            continue

        # line-level miss ratio
        page_missing = sum(1 for ln in all_lines
                           if not _is_text_present(ln.text, docx_text_set))
        miss_ratio = page_missing / len(all_lines)
        page_stat = {"page": pg.page_num + 1, "miss_ratio": round(miss_ratio, 2),
                     "total_lines": len(all_lines)}
        per_page_stats.append(page_stat)
        if miss_ratio >= PER_PAGE_MISS_RATIO_BAIL:
            page_stat["bail_high_miss"] = True
            continue

        page_height = float(pg.height) if pg.height else 0.0
        page_recovered = 0

        for ln in sorted(all_lines, key=lambda l: float(l.bbox[1])):
            if page_recovered >= MAX_RECOVERED_PER_PAGE:
                break
            if recovered >= MAX_RECOVERED_TOTAL:
                break
            text = (ln.text or "").strip()
            if not text or len(text) > SHORT_LINE_MAX_CHARS:
                continue
            if _is_template_placeholder(text):
                recovered_band["placeholder_skipped"] += 1
                continue
            if _is_text_present(text, docx_text_set):
                recovered_band["substring_skipped"] += 1
                continue
            y_top = float(ln.bbox[1])
            band = _band(y_top, page_height)
            if band == "mid":
                recovered_band["mid_skipped"] += 1
                continue
            try:
                if band == "top":
                    new_p = _insert_top(body, text, anchor_template)
                else:  # bot
                    new_p = _append_to_body(body, text, anchor_template)
            except Exception as e:
                log.debug("recovery insert failed: %s", e)
                continue
            recovered += 1
            page_recovered += 1
            recovered_band[band] += 1
            docx_text_set.add(_normalize(text))

    return {
        "fixer": "text_recovery",
        "recovered": recovered,
        "per_page_stats": per_page_stats,
        "band_breakdown": recovered_band,
    }
