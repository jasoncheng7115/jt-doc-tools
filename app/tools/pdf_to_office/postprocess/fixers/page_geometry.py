"""Page size + margins from PDF (Sprint A 4)。

pdf2docx 預設給 A4 大小，遇到 A3 / B5 / Letter / 客製尺寸 PDF 轉完跑版。
本 fixer 從 PDFTruth 第一頁拿 width/height/margins，寫進 docx 的 `<w:sectPr>`。

座標單位：
- PDF: pt (1 pt = 1/72 in)
- docx: twips (1 twip = 1/20 pt) → 1 pt = 20 twips

策略：
- 只改 `<w:pgSz>` + `<w:pgMar>`（不動 column / 對齊等其他 sectPr 屬性）
- 多頁尺寸不一致時用「眾數」（最常見的 size），避免少數異常頁主導
- 偵測 landscape (width > height) 自動加 `<w:orient w:val="landscape"/>`
"""
from __future__ import annotations

import logging
from collections import Counter

from docx.oxml.ns import qn

log = logging.getLogger(__name__)


def _pt_to_twips(pt: float) -> int:
    return int(round(pt * 20))


def _dominant_page_size(pages) -> tuple[float, float]:
    """眾數頁面尺寸（避免少數異常頁主導）。"""
    sizes = Counter()
    for pg in pages:
        # 四捨五入到整數 pt 避免浮點抖動
        key = (int(round(pg.width)), int(round(pg.height)))
        sizes[key] += 1
    if not sizes:
        return (595.0, 842.0)  # A4 fallback
    (w, h), _ = sizes.most_common(1)[0]
    return (float(w), float(h))


def _avg_margins(pages) -> tuple[float, float, float, float]:
    """平均 margins (top, right, bottom, left) — 各頁可能略不同取平均。"""
    if not pages:
        return (72.0, 72.0, 72.0, 72.0)
    n = len(pages)
    return (
        sum(p.margin_top for p in pages) / n,
        sum(p.margin_right for p in pages) / n,
        sum(p.margin_bottom for p in pages) / n,
        sum(p.margin_left for p in pages) / n,
    )


def _set_or_replace(parent, tag: str, attrs: dict):
    """在 parent 內找/建 child element，attrs 套上去。"""
    el = parent.find(qn(tag))
    if el is None:
        el = parent.makeelement(qn(tag), {})
        parent.append(el)
    for k, v in attrs.items():
        el.set(qn(k), str(v))
    return el


def fix_page_geometry(docx_doc, pdf_truth, alignment) -> dict:
    if not pdf_truth or not pdf_truth.pages:
        return {"fixer": "page_geometry", "skipped": "no pdf_truth"}

    pw, ph = _dominant_page_size(pdf_truth.pages)
    mt, mr, mb, ml = _avg_margins(pdf_truth.pages)
    is_landscape = pw > ph

    # docx 的 sectPr 在 body 最後一個元素，或在某 paragraph 的 pPr 內（split section）。
    # 我們只改最後一個 body-level sectPr（單 section 文件最常見）。
    body = docx_doc.element.body
    sectPr = body.find(qn("w:sectPr"))
    if sectPr is None:
        # 罕見情況沒有 sectPr — 自己建一個
        sectPr = body.makeelement(qn("w:sectPr"), {})
        body.append(sectPr)

    pw_tw = _pt_to_twips(pw)
    ph_tw = _pt_to_twips(ph)
    pgsz_attrs = {"w:w": pw_tw, "w:h": ph_tw}
    if is_landscape:
        pgsz_attrs["w:orient"] = "landscape"
    _set_or_replace(sectPr, "w:pgSz", pgsz_attrs)

    _set_or_replace(sectPr, "w:pgMar", {
        "w:top": _pt_to_twips(mt),
        "w:right": _pt_to_twips(mr),
        "w:bottom": _pt_to_twips(mb),
        "w:left": _pt_to_twips(ml),
        "w:header": 720,   # 0.5"
        "w:footer": 720,
        "w:gutter": 0,
    })

    return {
        "fixer": "page_geometry",
        "page_width_pt": round(pw, 1),
        "page_height_pt": round(ph, 1),
        "margins_pt": (round(mt, 1), round(mr, 1), round(mb, 1), round(ml, 1)),
        "orientation": "landscape" if is_landscape else "portrait",
        "pages_total": len(pdf_truth.pages),
    }
