"""超連結文字補回（v1.8.62 D2）。

pdf2docx 對「link annotation 區域」抽文字有 bug：PyMuPDF 的 link 是獨立 annotation
（含 bbox + uri），其下方文字 spans 仍會被抽到，但 pdf2docx 偶會誤判 → 該區域
docx 內 cell / paragraph 變空白。

實際踩到（付款表）：
- PDF 內「<網址>」「<email>」「<email>」是超連結
- docx 同位置 cell 空白

修法：
1) 用 PyMuPDF 抓每頁 link annotation 的 bbox + uri
2) 對每個 link，從 PDFTruth 該 bbox 範圍取出 lines 內文字
3) 看 docx 內 normalized 文字是否含該 link text → 沒有 = 漏抓
4) 找 link bbox 對應的 docx cell（最接近的 empty cell）或 body para 插入
5) 補進去時用「藍色底線」run style 模擬超連結外觀（不真的設 w:hyperlink — 會
   需要新增 rel，複雜化；視覺上跟超連結等價即可）
"""
from __future__ import annotations

import logging
import re
from copy import deepcopy

import fitz
from docx.oxml.ns import qn

log = logging.getLogger(__name__)


HYPERLINK_COLOR = "0563C1"  # Office 預設 hyperlink 藍


def _normalize(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", "", s).strip()


def _extract_pdf_links(pdf_path) -> list[dict]:
    """回 [{page_num, bbox(pt), uri}, ...]。"""
    out: list[dict] = []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        log.warning("open pdf for links failed: %s", e)
        return out
    try:
        for pno in range(doc.page_count):
            try:
                page = doc.load_page(pno)
                links = page.get_links() or []
                for link in links:
                    uri = link.get("uri") or link.get("file") or ""
                    if not uri:
                        continue
                    rect = link.get("from")
                    if rect is None:
                        continue
                    try:
                        bbox = (float(rect.x0), float(rect.y0),
                                float(rect.x1), float(rect.y1))
                    except Exception:
                        continue
                    out.append({"page_num": pno, "bbox": bbox, "uri": uri})
            except Exception as e:
                log.debug("page %d get_links failed: %s", pno, e)
    finally:
        doc.close()
    return out


def _text_in_bbox(pdf_truth, page_num: int, bbox: tuple) -> str:
    """從 PDFTruth 指定頁取 bbox 重疊範圍內所有 lines 的串接文字。"""
    x0, y0, x1, y1 = bbox
    try:
        page = pdf_truth.pages[page_num]
    except IndexError:
        return ""
    out_parts: list[str] = []
    for b in page.blocks:
        if b.block_type != "text":
            continue
        for ln in b.lines:
            lx0, ly0, lx1, ly1 = ln.bbox
            # 重疊判定（line bbox 與 link bbox 有交集）
            if lx1 < x0 - 2 or lx0 > x1 + 2:
                continue
            if ly1 < y0 - 2 or ly0 > y1 + 2:
                continue
            t = (ln.text or "").strip()
            if t:
                out_parts.append(t)
    return " ".join(out_parts)


def _collect_docx_text_set(docx_doc) -> set[str]:
    out: set[str] = set()
    body = docx_doc.element.body
    for p_el in body.iter(qn("w:p")):
        parts = []
        for t in p_el.iter(qn("w:t")):
            if t.text:
                parts.append(t.text)
        n = _normalize("".join(parts))
        if n:
            out.add(n)
    for tc_el in body.iter(qn("w:tc")):
        parts = []
        for t in tc_el.iter(qn("w:t")):
            if t.text:
                parts.append(t.text)
        n = _normalize("".join(parts))
        if n:
            out.add(n)
    return out


def _is_text_present(needle: str, hay: set[str]) -> bool:
    nn = _normalize(needle)
    if not nn:
        return True
    if nn in hay:
        return True
    for h in hay:
        if nn in h:
            return True
    return False


def _find_best_empty_cell(docx_doc, link_bbox_y_center: float, pdf_truth) -> object | None:
    """找最接近 link bbox y 的 empty docx cell。
    用「docx table 對應 PDFTruth 哪頁哪 y」推估 — 但 docx cell 沒絕對 y，無法精確。
    fallback：回 first empty cell that's after the most-recent matched non-empty paragraph
    in y order — 太複雜，**這版簡化**：直接回所有 empty cells 第一個（依 body 順序）。
    """
    body = docx_doc.element.body
    for tc_el in body.iter(qn("w:tc")):
        parts = []
        for t in tc_el.iter(qn("w:t")):
            if t.text:
                parts.append(t.text)
        if not _normalize("".join(parts)):
            return tc_el
    return None


def _insert_link_into_cell(tc_el, text: str, uri: str) -> bool:
    """寫入 cell 第一個 paragraph 的第一個 run，並套 hyperlink 樣式 (藍色 + 底線)。"""
    p_el = tc_el.find(qn("w:p"))
    if p_el is None:
        p_el = tc_el.makeelement(qn("w:p"), {})
        tc_el.append(p_el)
    # 移除原 runs（cell 是空的應該本來就沒）
    for r in p_el.findall(qn("w:r")):
        p_el.remove(r)
    new_r = p_el.makeelement(qn("w:r"), {})
    rPr = new_r.makeelement(qn("w:rPr"), {})
    color = rPr.makeelement(qn("w:color"), {qn("w:val"): HYPERLINK_COLOR})
    rPr.append(color)
    u = rPr.makeelement(qn("w:u"), {qn("w:val"): "single"})
    rPr.append(u)
    new_r.append(rPr)
    new_t = new_r.makeelement(qn("w:t"), {qn("xml:space"): "preserve"})
    new_t.text = text
    new_r.append(new_t)
    p_el.append(new_r)
    return True


def fix_link_text_recovery(docx_doc, pdf_truth, alignment, *, pdf_path=None) -> dict:
    if not pdf_path:
        return {"fixer": "link_text_recovery", "recovered": 0,
                "skipped": "no pdf_path"}
    if not pdf_truth:
        return {"fixer": "link_text_recovery", "recovered": 0,
                "skipped": "no pdf_truth"}
    links = _extract_pdf_links(pdf_path)
    if not links:
        return {"fixer": "link_text_recovery", "recovered": 0, "pdf_links": 0}

    docx_hay = _collect_docx_text_set(docx_doc)
    recovered = 0
    skipped_already_present = 0
    skipped_no_text = 0
    skipped_no_target = 0

    for link in links:
        link_text = _text_in_bbox(pdf_truth, link["page_num"], link["bbox"])
        if not link_text:
            skipped_no_text += 1
            continue
        if _is_text_present(link_text, docx_hay):
            skipped_already_present += 1
            continue
        # 找空 cell 插入
        target = _find_best_empty_cell(docx_doc,
                                        (link["bbox"][1] + link["bbox"][3]) / 2,
                                        pdf_truth)
        if target is None:
            skipped_no_target += 1
            continue
        try:
            _insert_link_into_cell(target, link_text, link["uri"])
            recovered += 1
            docx_hay.add(_normalize(link_text))
        except Exception as e:
            log.debug("insert link failed: %s", e)

    return {
        "fixer": "link_text_recovery",
        "recovered": recovered,
        "pdf_links": len(links),
        "skipped_already_present": skipped_already_present,
        "skipped_no_text_in_bbox": skipped_no_text,
        "skipped_no_target_cell": skipped_no_target,
    }
