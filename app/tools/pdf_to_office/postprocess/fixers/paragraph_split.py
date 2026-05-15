"""段落拆分 fixer (Sprint 3 真實版)。

處理 pdf2docx 把 PDF 多個獨立 block 黏成 docx 一段的情境。

依賴 aligner 的 1:N alignment（v1.8.41+ 加的）— 對 alignment.pdf_block_refs > 1
的條目執行：
- 把 docx 段落文字依 PDF block 邊界切成 N 段
- 用 PDF block text 當切點（在 docx text 內找 block N+1 起點）
- 第一段保留原 paragraph 物件 + 屬性；後續段落 insert before 下一段

保守策略：
- 任一切點找不到（PDF text 跟 docx text 差異太大）→ 整個 alignment 跳過
- 表格內段落不動（cell 內結構複雜）
- 切完後文字長度跟原文相差 > 10% → rollback (避免破壞)
"""
from __future__ import annotations

import logging
import re
from copy import deepcopy

log = logging.getLogger(__name__)


def _normalize_for_search(s: str) -> str:
    """標準化用於 startswith 搜尋 — 去多餘空白 / 零寬字。"""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.replace("﻿", "").replace("​", "")).strip()


def _split_text_at_blocks(docx_text: str, block_texts: list[str]) -> list[str] | None:
    """把 docx_text 依 block_texts 順序切成 N 段。

    演算法：對 block_texts[1..N-1] 在 docx_text 內找起點，
    切點 = 那個 block text 的第一個「夠長」prefix 出現位置。
    第 1 段 = [0, cut_1)，第 2 段 = [cut_1, cut_2) ...
    """
    if len(block_texts) < 2:
        return None
    cuts: list[int] = [0]
    cursor = 0
    for bt in block_texts[1:]:
        # 找 bt 開頭 6-12 字當搜尋鎖定 (短於 6 字精度太差)
        n_bt = _normalize_for_search(bt)
        if len(n_bt) < 6:
            return None
        for needle_len in range(min(20, len(n_bt)), 5, -1):
            needle = n_bt[:needle_len]
            idx = docx_text.find(needle, cursor)
            if idx >= 0:
                cuts.append(idx)
                cursor = idx + needle_len
                break
        else:
            # 找不到任何 prefix — 切點失敗，整批 abort
            return None
    cuts.append(len(docx_text))
    pieces = []
    for i in range(len(cuts) - 1):
        pieces.append(docx_text[cuts[i]:cuts[i + 1]].strip())
    return [p for p in pieces if p]


def _clone_paragraph_with_text(p, new_text: str):
    """建一個跟 p 同樣 style 的新段落，文字為 new_text。"""
    new_elem = deepcopy(p._element)
    # 移除所有 run，留 pPr
    from docx.oxml.ns import qn
    for r in new_elem.findall(qn("w:r")):
        new_elem.remove(r)
    # 加新 run
    rPr_template = None
    # 從原段落第一個 run 抓 rPr
    orig_runs = p._element.findall(qn("w:r"))
    if orig_runs:
        rPr_template = orig_runs[0].find(qn("w:rPr"))
    new_r = new_elem.makeelement(qn("w:r"), {})
    if rPr_template is not None:
        new_r.append(deepcopy(rPr_template))
    new_t = new_r.makeelement(qn("w:t"), {qn("xml:space"): "preserve"})
    new_t.text = new_text
    new_r.append(new_t)
    new_elem.append(new_r)
    return new_elem


def fix_paragraph_split(docx_doc, pdf_truth, alignment) -> dict:
    if pdf_truth is None or alignment is None:
        return {"fixer": "paragraph_split", "split": 0, "skipped": "no alignment"}
    pdf_blocks = pdf_truth.all_blocks if pdf_truth else []
    if not pdf_blocks:
        return {"fixer": "paragraph_split", "split": 0, "skipped": "no pdf blocks"}

    # 找 1:N alignment（pdf_block_refs > 1）
    multi_aligns = [a for a in alignment.alignments
                    if len(a.pdf_block_refs or []) > 1]
    if not multi_aligns:
        return {"fixer": "paragraph_split", "split": 0, "skipped": "no 1:N alignments"}

    # 對應 docx_para_index → 實際 paragraph 物件（只處理 body paragraphs，跳過 table）
    body_paras = list(docx_doc.paragraphs)

    split_count = 0
    pieces_inserted = 0

    for a in multi_aligns:
        di = a.docx_para_index
        if di < 0 or di >= len(body_paras):
            continue
        p = body_paras[di]
        block_texts = [pdf_blocks[pi].text for pi in a.pdf_block_refs]
        docx_text = (p.text or "").strip()
        pieces = _split_text_at_blocks(docx_text, block_texts)
        if not pieces or len(pieces) < 2:
            continue
        # 簡單健全檢查：合併 pieces 的字數跟原文不能差 > 10%
        merged = "".join(p.replace(" ", "") for p in pieces)
        orig = docx_text.replace(" ", "")
        if abs(len(merged) - len(orig)) / max(1, len(orig)) > 0.10:
            continue
        # 拆 — 第 1 段塞回 p；2..N 段在 p 後面 insert
        from docx.oxml.ns import qn
        # 把 p 的所有 run 文字置空，第一個 run 設成 pieces[0]
        runs = p._element.findall(qn("w:r"))
        for ri, r in enumerate(runs):
            for t in r.findall(qn("w:t")):
                if ri == 0:
                    t.text = pieces[0]
                    t.set(qn("xml:space"), "preserve")
                else:
                    t.text = ""
        # 多餘 run 移除（保留第一個帶屬性的）
        for r in runs[1:]:
            p._element.remove(r)
        # pieces[1..] insert after p
        parent = p._element.getparent()
        idx = list(parent).index(p._element)
        for i, piece in enumerate(pieces[1:], start=1):
            new_elem = _clone_paragraph_with_text(p, piece)
            parent.insert(idx + i, new_elem)
            pieces_inserted += 1
        split_count += 1

    return {"fixer": "paragraph_split", "split": split_count,
            "pieces_inserted": pieces_inserted}
