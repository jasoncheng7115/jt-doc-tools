"""Sprint 1：套用基本樣式。

完整版（Sprint 2+）會載 reference.docx 模板套 Heading/List/Table style。
Sprint 1 先做：
- 預設 Normal 段距 (前 0、後 6pt)
- 全文預設 CJK fallback 字型（從 PDFTruth body_font_name 取，沒有就 Fallback）
- 頁面大小用 PDFTruth 真值（若可用）
"""
from __future__ import annotations

import logging

from docx.oxml.ns import qn
from docx.shared import Pt

from ..pdf_truth import PDFTruth
from .config import FALLBACK_ASCII_FONT, FALLBACK_CJK_FONT, FONT_MAPPING
from .fixers.font_normalize import _resolve_font

log = logging.getLogger(__name__)


def apply_styles(docx_doc, pdf_truth: PDFTruth) -> dict:
    """主入口。回 changelog。"""
    changes: dict = {"section": "style_apply", "items": []}

    # === 1. Normal 段距標準化 ===
    try:
        normal = docx_doc.styles["Normal"]
        if normal.paragraph_format:
            normal.paragraph_format.space_before = Pt(0)
            normal.paragraph_format.space_after = Pt(6)
            changes["items"].append("normal_spacing_before=0pt_after=6pt")
    except Exception as e:
        log.debug("set Normal spacing failed: %s", e)

    # === 2. 整份文件預設 CJK 字型 ===
    if pdf_truth.body_font_name:
        eastasia, ascii_font = _resolve_font(pdf_truth.body_font_name)
    else:
        eastasia, ascii_font = (FALLBACK_CJK_FONT, FALLBACK_ASCII_FONT)
    try:
        styles_element = docx_doc.styles.element
        # 找 docDefaults > rPrDefault > rPr > rFonts，沒有就建
        doc_defaults = styles_element.find(qn("w:docDefaults"))
        if doc_defaults is None:
            doc_defaults = styles_element.makeelement(qn("w:docDefaults"), {})
            styles_element.insert(0, doc_defaults)
        rPrDefault = doc_defaults.find(qn("w:rPrDefault"))
        if rPrDefault is None:
            rPrDefault = doc_defaults.makeelement(qn("w:rPrDefault"), {})
            doc_defaults.append(rPrDefault)
        rPr = rPrDefault.find(qn("w:rPr"))
        if rPr is None:
            rPr = rPrDefault.makeelement(qn("w:rPr"), {})
            rPrDefault.append(rPr)
        for old in rPr.findall(qn("w:rFonts")):
            rPr.remove(old)
        rFonts = rPr.makeelement(qn("w:rFonts"), {})
        rFonts.set(qn("w:ascii"), ascii_font)
        rFonts.set(qn("w:hAnsi"), ascii_font)
        rFonts.set(qn("w:eastAsia"), eastasia)
        rFonts.set(qn("w:cs"), ascii_font)
        rPr.insert(0, rFonts)
        changes["items"].append(f"doc_default_font=ea:{eastasia}/ascii:{ascii_font}")
    except Exception as e:
        log.warning("set doc default font failed: %s", e)

    # === 3. 頁面大小（用 PDFTruth 真值）+ 邊距 (clamp) ===
    if pdf_truth.pages:
        first_page = pdf_truth.pages[0]
        try:
            section = docx_doc.sections[0]
            # PDF pt → docx EMU (1 pt = 12700 EMU)
            section.page_width = int(first_page.width * 12700)
            section.page_height = int(first_page.height * 12700)
            # 邊距 clamp：extractor 從「文字 block bbox 集中分佈」估 margin，但若 PDF
            # 內容只佔頁面上方 1/3，bottom margin 會算成 600+pt → docx 可用內容區
            # 變超小，footer 被擠下頁、表格內文字被截。clamp 到 [18, 90] pt 區間
            # （0.25-1.25 inch — 涵蓋常見 PDF 邊距值，不會被異常 PDF 估算汙染）。
            def _clamp(v: float, lo: float = 18.0, hi: float = 90.0) -> float:
                if v < lo:
                    return lo
                if v > hi:
                    return hi
                return v
            section.left_margin = int(_clamp(first_page.margin_left) * 12700)
            section.right_margin = int(_clamp(first_page.margin_right) * 12700)
            section.top_margin = int(_clamp(first_page.margin_top) * 12700)
            section.bottom_margin = int(_clamp(first_page.margin_bottom) * 12700)
            changes["items"].append(
                f"page_size={first_page.width:.0f}x{first_page.height:.0f}pt"
                f" margins=L{_clamp(first_page.margin_left):.0f}/"
                f"R{_clamp(first_page.margin_right):.0f}/"
                f"T{_clamp(first_page.margin_top):.0f}/"
                f"B{_clamp(first_page.margin_bottom):.0f}"
            )
        except Exception as e:
            log.debug("set page size failed: %s", e)

    return changes
