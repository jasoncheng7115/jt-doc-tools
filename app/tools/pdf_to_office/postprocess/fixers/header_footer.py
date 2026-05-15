"""頁首頁尾識別 fixer。

把每頁重複出現的頁首頁尾從內文移到 Word 的 header/footer。

策略：
- 多頁 PDF：跨頁聚合 — ≥ 50% 頁面同 y 範圍出現相似文字 → 視為頁首頁尾
- 單頁 PDF（v1.8.42+ 加）：用 footer pattern 啟發式 — 最下方 (y > 0.85*page_h) 的
  block 若含 contact info pattern (電話 / Email / 統編 / Page X/Y / @ / .com / 頁次)
  → 視為頁尾移到 docx footer

保守策略：
- ≤ 1 個 candidate 不處理
- 文字過長 (> 200 字) 不視為頁首頁尾（誤判風險高）
"""
from __future__ import annotations

import logging
import re

from docx.shared import Pt

log = logging.getLogger(__name__)

HEADER_TOP_RATIO = 0.1
FOOTER_BOTTOM_RATIO = 0.9
MIN_PAGE_RATIO_FOR_DETECTION = 0.5

# 單頁 PDF footer 啟發式 — 文字 contains any of these → likely footer
_FOOTER_HINTS = (
    "電話", "Tel", "TEL", "Phone",
    "郵件", "Email", "@",
    "統編", "統一編號", "VAT",
    "頁:", "頁：", "Page", "Page ",
    "/1", "/2", "/3", "頁次",
    "傳真", "Fax", "FAX",
)
MAX_FOOTER_CHARS = 200


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


def _looks_like_footer(text: str) -> bool:
    """單頁 PDF 用 — 文字像 contact info / 頁碼 / 法律宣告 → 視為 footer。"""
    if not text or len(text) > MAX_FOOTER_CHARS:
        return False
    return any(h in text for h in _FOOTER_HINTS)


def fix_header_footer(docx_doc, pdf_truth, alignment) -> dict:
    if pdf_truth is None or pdf_truth.total_pages < 1:
        return {"fixer": "header_footer", "moved_to_header": 0,
                "moved_to_footer": 0, "skipped": "no pdf"}

    # 單頁 PDF — 用啟發式找底部 footer-like block
    if pdf_truth.total_pages == 1:
        page = pdf_truth.pages[0]
        candidates = []
        for b in page.blocks:
            if b.block_type != "text":
                continue
            x0, y0, x1, y1 = b.bbox
            if y0 < page.height * FOOTER_BOTTOM_RATIO:
                continue
            n = _normalize(b.text)
            if _looks_like_footer(n):
                candidates.append(n)
        moved_f = 0
        if candidates:
            footer_text = candidates[0]
            moved_f = _remove_paragraphs_with_text(docx_doc, {footer_text})
            # 找不到完全相同段落時，試 substring 移除（contact info 常被黏到別段內）
            if moved_f == 0:
                # 嘗試找含 footer pattern 的段落，把 footer 部分剝離
                for p in list(docx_doc.paragraphs):
                    txt = _normalize(p.text)
                    if footer_text in txt:
                        new_text = txt.replace(footer_text, "").strip()
                        if new_text:
                            for r in p.runs:
                                r.text = ""
                            p.runs[0].text = new_text if p.runs else None
                            if not p.runs:
                                p.add_run(new_text)
                        else:
                            p._element.getparent().remove(p._element)
                        moved_f = 1
                        break
            if moved_f:
                _set_section_footer(docx_doc.sections[0], footer_text)
        return {
            "fixer": "header_footer",
            "moved_to_header": 0,
            "moved_to_footer": moved_f,
            "single_page_heuristic": True,
            "footer_candidates": len(candidates),
        }

    # 多頁 PDF — 跨頁聚合
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
