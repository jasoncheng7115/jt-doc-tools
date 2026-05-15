"""標題識別 fixer。

把字級明顯大於內文的段落套上對應的 Heading style。

決策依據：
- body_size 取自 PDFTruth.body_font_size（最常用字級）
- 字級分群：1.15-1.35x body → H3；1.35-1.6x → H2；>1.6x → H1
- 額外檢查：候選段落字數短、不在表格、alignment 對應 PDF block 字級也對得上

保守策略：
- PDFTruth 沒對應 → 不套
- alignment confidence < 0.5 → 不套
- 表格內段落不動
"""
from __future__ import annotations

import logging

from ...pdf_truth.aligner import DocxToPdfAlignment

log = logging.getLogger(__name__)

# 字級分群
H1_RATIO = 1.6
H2_RATIO = 1.35
H3_RATIO = 1.15

# 標題段落最大字數
MAX_HEADING_CHARS = 50
MAX_HEADING_CHARS_CJK = 30

# 台灣公文額外
_TW_DOC_HEADING_2 = ("主旨：", "說明：", "辦法：", "正本：", "副本：")


def _is_listy(p) -> bool:
    style_name = (p.style.name if p.style else "") or ""
    return ("List" in style_name) or ("Heading" in style_name) or ("Title" in style_name)


def _set_heading(p, level: int) -> bool:
    try:
        styles = p.part.document.styles
        target = f"Heading {level}"
        if target in [s.name for s in styles]:
            p.style = styles[target]
            return True
    except Exception:
        pass
    return False


def _has_cjk(text: str) -> bool:
    return any("㐀" <= ch <= "鿿" for ch in (text or ""))


def fix_heading_detect(docx_doc, pdf_truth, alignment: DocxToPdfAlignment) -> dict:
    body_size = pdf_truth.body_font_size if pdf_truth else 0.0
    if body_size <= 0:
        return {"fixer": "heading_detect", "promoted": 0, "skipped_no_body_size": True}

    al_by_di = {a.docx_para_index: a for a in alignment.alignments}
    promoted_by_level: dict[int, int] = {1: 0, 2: 0, 3: 0}

    for di, p in enumerate(docx_doc.paragraphs):
        if _is_listy(p):
            continue
        text = (p.text or "").strip()
        if not text:
            continue
        max_chars = MAX_HEADING_CHARS_CJK if _has_cjk(text) else MAX_HEADING_CHARS
        if len(text) > max_chars:
            continue

        # 台灣公文 keyword H2
        if any(text.startswith(k) for k in _TW_DOC_HEADING_2):
            if _set_heading(p, 2):
                promoted_by_level[2] += 1
            continue

        a = al_by_di.get(di)
        if not a or not a.pdf_block_refs:
            continue
        if a.confidence < 0.5:
            continue
        # 用 PDFTruth 字級
        pdf_size = a.pdf_dominant_size
        if pdf_size <= 0:
            continue
        ratio = pdf_size / body_size
        level = None
        if ratio >= H1_RATIO:
            level = 1
        elif ratio >= H2_RATIO:
            level = 2
        elif ratio >= H3_RATIO:
            level = 3
        if level and _set_heading(p, level):
            promoted_by_level[level] += 1

    total = sum(promoted_by_level.values())
    return {
        "fixer": "heading_detect",
        "promoted": total,
        "by_level": promoted_by_level,
        "body_size": body_size,
    }
