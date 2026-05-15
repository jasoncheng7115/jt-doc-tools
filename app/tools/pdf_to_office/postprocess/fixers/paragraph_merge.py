"""段落合併 fixer。

把 pdf2docx 因為 PDF 換行而產生的破碎段落重新接回去。

決策依據：
- 純規則：相鄰段落 P1, P2 — P1 結尾不是句末標點 + 字型字級對齊一致 + 不是標題 / 清單
- PDFTruth 強化（若 alignment 可用）：
  - P1, P2 對應同一 PDF block → 強烈合併
  - 不同 block 但 y 距離 < 1.2 行高 → 合併
  - 不同 block 且 y 距離 > 1.5 行高 → 不合併（PDF 視角是兩段）
"""
from __future__ import annotations

import logging

from ...pdf_truth.aligner import DocxToPdfAlignment
from ..config import PARAGRAPH_MERGE
from .font_normalize import _is_monospace

log = logging.getLogger(__name__)


def _is_listy_or_heading(p) -> bool:
    """偵測段落是不是 List / Heading style — 這類不該合併。"""
    style_name = (p.style.name if p.style else "") or ""
    return ("List" in style_name) or ("Heading" in style_name) or ("Title" in style_name)


def _ends_with_sentence_end(text: str) -> bool:
    if not text:
        return False
    return text.rstrip()[-1:] in PARAGRAPH_MERGE["sentence_end_chars"]


def _font_matches(p1, p2, tol: float = 0.5) -> bool:
    """相鄰段落是否相同字型字級（用第一個 run 當代表）。"""
    r1 = p1.runs[0] if p1.runs else None
    r2 = p2.runs[0] if p2.runs else None
    if not r1 or not r2:
        return True  # 沒 run 不擋
    n1 = (r1.font.name or "")
    n2 = (r2.font.name or "")
    if n1 and n2 and n1 != n2:
        return False
    s1 = r1.font.size.pt if r1.font.size else None
    s2 = r2.font.size.pt if r2.font.size else None
    if s1 and s2 and abs(s1 - s2) > tol:
        return False
    return True


def _is_code_paragraph(p) -> bool:
    """段落是不是「code 行」— 用 run 字型 + 內容判斷。

    code 行特性：
    - 字型 monospace (Courier / Menlo / Mono...)
    - 或內容像 shell / config 命令（含 `=`、`/`、起頭是 keyword）

    code 行絕不該跟下一行併（保留行尾換行）。
    """
    r = p.runs[0] if p.runs else None
    if r and r.font.name and _is_monospace(r.font.name):
        return True
    # 內容啟發式 — Markdown / config 命令常見起頭
    txt = (p.text or "").lstrip()
    if not txt:
        return False
    if any(txt.startswith(kw) for kw in (
        "auto ", "iface ", "bridge-", "ip ", "ifconfig ", "sudo ", "systemctl ",
        "$ ", "# ", "> ", "->", "+", "/", "\\", "*",
    )):
        # 但純粹 markdown bullet "- " "* " 等不算 code（list_detect 處理）
        if txt[:2] in ("# ", "- ", "* ", "> "):
            return False
        return True
    return False


def _merge_into(p1, p2):
    """把 p2 的 runs 接到 p1 後（保留 run 屬性），刪 p2 段落元素。"""
    for run in p2.runs:
        new_run = p1.add_run(run.text or "")
        # 複製基本屬性
        new_run.bold = run.bold
        new_run.italic = run.italic
        new_run.underline = run.underline
        if run.font.name:
            new_run.font.name = run.font.name
        if run.font.size:
            new_run.font.size = run.font.size
    # 刪掉 p2 element
    p2._element.getparent().remove(p2._element)


def fix_paragraph_merge(docx_doc, pdf_truth, alignment: DocxToPdfAlignment) -> dict:
    """掃整份 docx，相鄰段落該合併的合併。回 changelog。"""
    al_by_di = {a.docx_para_index: a for a in alignment.alignments}
    paragraphs = list(docx_doc.paragraphs)  # snapshot — 後續刪除不影響迭代
    merged_count = 0
    pdf_truth_used = 0

    i = 0
    while i < len(paragraphs) - 1:
        p1 = paragraphs[i]
        p2 = paragraphs[i + 1]
        t1 = (p1.text or "").rstrip()
        t2 = (p2.text or "").lstrip()
        if not t1 or not t2:
            i += 1
            continue
        # 規則排除
        if _is_listy_or_heading(p1) or _is_listy_or_heading(p2):
            i += 1
            continue
        if _ends_with_sentence_end(t1):
            i += 1
            continue
        if not _font_matches(p1, p2, PARAGRAPH_MERGE["font_size_tolerance_pt"]):
            i += 1
            continue
        if len(t1) < PARAGRAPH_MERGE["min_paragraph_chars_to_consider"]:
            # 段落太短可能是欄位標籤，謹慎處理 — 但若有 PDFTruth 同 block 確認就放行
            pass

        should_merge = True

        # PDFTruth 強化判斷
        a1 = al_by_di.get(i)
        a2 = al_by_di.get(i + 1)
        if a1 and a2 and a1.pdf_block_refs and a2.pdf_block_refs:
            pdf_truth_used += 1
            same_block = set(a1.pdf_block_refs) & set(a2.pdf_block_refs)
            if same_block:
                should_merge = True  # 強烈合併
            elif a1.page_num == a2.page_num:
                # 同頁不同 block — 看 y 距離 vs 行高
                bb1 = a1.pdf_bbox_union
                bb2 = a2.pdf_bbox_union
                if bb1 and bb2:
                    line_h = max(8.0, a1.pdf_dominant_size * 1.4 if a1.pdf_dominant_size else 14.0)
                    y_gap = bb2[1] - bb1[3]  # p2.y0 - p1.y1
                    if y_gap > line_h * PARAGRAPH_MERGE["y_distance_ratio_diff_para_min"]:
                        should_merge = False  # PDF 視角是兩段
            else:
                # 跨頁 — 大概率是 PDF 自然斷頁，仍視為一段（pdf2docx 切了）
                pass

        if should_merge:
            _merge_into(p1, p2)
            merged_count += 1
            # 刪除後 p2 不再有效；下個迭代用同一個 p1 跟新的 next
            paragraphs.pop(i + 1)
            # i 不前進 — 繼續看 p1 跟新的 next 是否也該合
        else:
            i += 1

    return {
        "fixer": "paragraph_merge",
        "merged": merged_count,
        "pdf_truth_used": pdf_truth_used,
    }
