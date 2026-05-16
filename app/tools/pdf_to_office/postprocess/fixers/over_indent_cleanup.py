"""Over-indent 清理 fixer (Sprint B 補強)。

pdf2docx 對某些 PDF 段落會錯算 indent，在標題（jc=center）+ 短段落同時加上
左右各 3000+ twips 的 ind，造成標題可用寬度被砸到剩 100pt 以內 → 中文標題
被迫折行 / 斷字（user 反映「鼎原科技股份有 / 限公司」這類）。

策略：
- 取 sectPr 的 pgSz + pgMar 算 content_width
- 對每個 body paragraph 看 `<w:pPr><w:ind>` 的 left + right
- 若 (left + right) > content_width * 0.4，且段落是 center align 或文字 < 30 字
  → 清掉 ind（把 left / right 設 0）
- 表格內 paragraph 不動（cell 寬度另有規則）
"""
from __future__ import annotations

import logging

from docx.oxml.ns import qn

log = logging.getLogger(__name__)


def _twips(s) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def _get_section_content_width_twips(body) -> int:
    """從 sectPr 算可用寬度 (twips)。沒讀到回 A4 預設 9180 (≈ 11906 - 2 × 1440)。"""
    sectPr = body.find(qn("w:sectPr"))
    if sectPr is None:
        return 9180
    pgSz = sectPr.find(qn("w:pgSz"))
    pgMar = sectPr.find(qn("w:pgMar"))
    if pgSz is None or pgMar is None:
        return 9180
    page_w = _twips(pgSz.get(qn("w:w"))) or 11906
    ml = _twips(pgMar.get(qn("w:left")))
    mr = _twips(pgMar.get(qn("w:right")))
    return max(2000, page_w - ml - mr)


def fix_over_indent_cleanup(docx_doc, pdf_truth, alignment) -> dict:
    body = docx_doc.element.body
    content_w = _get_section_content_width_twips(body)
    threshold = int(content_w * 0.4)  # left + right indent 合計超過此值算 over

    cleared = 0
    detail: list[str] = []

    para_tag = qn("w:p")
    for p_elem in body.iter(para_tag):
        # 表格內的 paragraph 不動
        parent = p_elem.getparent()
        anc = parent
        in_table = False
        while anc is not None:
            if anc.tag == qn("w:tbl"):
                in_table = True
                break
            anc = anc.getparent()
        if in_table:
            continue

        pPr = p_elem.find(qn("w:pPr"))
        if pPr is None:
            continue
        ind = pPr.find(qn("w:ind"))
        if ind is None:
            continue
        left = _twips(ind.get(qn("w:left")))
        right = _twips(ind.get(qn("w:right")))
        if left + right < threshold:
            continue
        # 看是否 center align 或短段落
        jc = pPr.find(qn("w:jc"))
        is_center = jc is not None and jc.get(qn("w:val")) in ("center", "centerGroup")
        # 文字長度
        text_chars = 0
        for r in p_elem.findall(qn("w:r")):
            for t in r.findall(qn("w:t")):
                text_chars += len(t.text or "")
        if not (is_center or text_chars < 30):
            continue
        # 清掉 left / right ind（保留 firstLine / hanging）
        ind.attrib.pop(qn("w:left"), None)
        ind.attrib.pop(qn("w:right"), None)
        # 若 ind 變空就 detach
        if not ind.attrib:
            pPr.remove(ind)
        cleared += 1

    return {
        "fixer": "over_indent_cleanup",
        "paragraphs_cleared": cleared,
        "content_width_twips": content_w,
        "threshold_twips": threshold,
    }
