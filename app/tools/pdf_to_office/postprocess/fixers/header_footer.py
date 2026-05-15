"""頁首頁尾識別 fixer。

把每頁重複出現的頁首頁尾從內文移到 Word 的 header/footer。

策略：
- 對每頁找 y < page_h * 0.1 的 block 當頁首候選
- 對每頁找 y > page_h * 0.9 的 block 當頁尾候選
- 跨頁聚合：≥ 50% 頁面同 y 範圍出現相似文字 → 視為頁首頁尾
- 在 docx 中找對應段落並移除，加進 Section.header / footer

保守策略：
- 單頁 PDF 不處理（無法判斷重複）
- ≤ 1 個 candidate 不處理
"""
from __future__ import annotations

import logging

from docx.shared import Pt

log = logging.getLogger(__name__)

HEADER_TOP_RATIO = 0.1
FOOTER_BOTTOM_RATIO = 0.9
MIN_PAGE_RATIO_FOR_DETECTION = 0.5


def _normalize(s: str) -> str:
    return " ".join((s or "").split())


def _candidates(pages, top: bool) -> list[str]:
    """收集所有頁的頁首 / 頁尾候選文字。"""
    out: list[str] = []
    for p in pages:
        for b in p.blocks:
            if b.block_type != "text":
                continue
            x0, y0, x1, y1 = b.bbox
            if top:
                if y1 <= p.height * HEADER_TOP_RATIO:
                    out.append(_normalize(b.text))
            else:
                if y0 >= p.height * FOOTER_BOTTOM_RATIO:
                    out.append(_normalize(b.text))
    return [t for t in out if t]


def _common_texts(candidates: list[str], page_count: int) -> list[str]:
    """找出在 ≥ 50% 頁面出現的文字 — 相同字串視為「同一個頁首」。"""
    if not candidates or page_count < 2:
        return []
    from collections import Counter
    c = Counter(candidates)
    threshold = max(2, int(page_count * MIN_PAGE_RATIO_FOR_DETECTION))
    return [text for text, n in c.items() if n >= threshold]


def _remove_paragraphs_with_text(docx_doc, texts: set[str]) -> int:
    if not texts:
        return 0
    removed = 0
    for p in list(docx_doc.paragraphs):
        if _normalize(p.text) in texts:
            try:
                p._element.getparent().remove(p._element)
                removed += 1
            except Exception:
                pass
    return removed


def _set_section_header(section, text: str) -> None:
    try:
        hdr = section.header
        # 清空現有段落內容
        for p in hdr.paragraphs:
            p.text = ""
        if hdr.paragraphs:
            hdr.paragraphs[0].text = text
        else:
            hdr.add_paragraph(text)
    except Exception:
        pass


def _set_section_footer(section, text: str) -> None:
    try:
        ftr = section.footer
        for p in ftr.paragraphs:
            p.text = ""
        if ftr.paragraphs:
            ftr.paragraphs[0].text = text
        else:
            ftr.add_paragraph(text)
    except Exception:
        pass


def fix_header_footer(docx_doc, pdf_truth, alignment) -> dict:
    if pdf_truth is None or pdf_truth.total_pages < 2:
        return {"fixer": "header_footer", "moved_to_header": 0,
                "moved_to_footer": 0, "skipped_single_page": True}

    headers = _common_texts(_candidates(pdf_truth.pages, top=True), pdf_truth.total_pages)
    footers = _common_texts(_candidates(pdf_truth.pages, top=False), pdf_truth.total_pages)

    moved_h = 0
    moved_f = 0

    if headers:
        # 取最常見的一個當主要頁首；其餘保留在內文
        header_text = headers[0]
        moved_h = _remove_paragraphs_with_text(docx_doc, {header_text})
        if moved_h:
            _set_section_header(docx_doc.sections[0], header_text)
    if footers:
        footer_text = footers[0]
        moved_f = _remove_paragraphs_with_text(docx_doc, {footer_text})
        if moved_f:
            _set_section_footer(docx_doc.sections[0], footer_text)

    return {
        "fixer": "header_footer",
        "moved_to_header": moved_h,
        "moved_to_footer": moved_f,
        "header_candidates": len(headers),
        "footer_candidates": len(footers),
    }
