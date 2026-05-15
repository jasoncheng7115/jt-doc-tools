"""docx ↔ PDFTruth 對應器。

目的：建立 docx 段落 → PDF blocks 的對應關係，是所有「位置比對」校正的基礎。

演算法（Sprint 1 採三輪簡化版）：
1. 精確文字匹配（normalize 後完全相同）→ confidence 1.0
2. 模糊匹配（rapidfuzz ratio > 0.85）→ confidence = ratio
3. N-gram fallback：剩下 docx 段落用相對順序貼上

Sprint 1 只做基本 1:1 / N:1 對應，不處理 1:N（拆段檢查留給 Sprint 2）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from rapidfuzz import fuzz

from .models import PDFTruth

log = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    """去 BOM、多餘空白、零寬字 — 比對前的常規化。"""
    if not s:
        return ""
    s = s.replace("﻿", "").replace("​", "").replace("‌", "").replace("‍", "")
    s = _WS_RE.sub(" ", s).strip()
    return s


@dataclass
class Alignment:
    docx_para_index: int
    pdf_block_refs: list[int] = field(default_factory=list)  # 對 PDFTruth.all_blocks 的 index
    confidence: float = 0.0
    method: Literal["text_match", "fuzzy_match", "position_match", "unmatched"] = "unmatched"
    text_similarity: float = 0.0
    pdf_bbox_union: Optional[tuple[float, float, float, float]] = None
    pdf_dominant_font: str = ""
    pdf_dominant_size: float = 0.0
    page_num: int = -1  # 主要對應頁


@dataclass
class DocxToPdfAlignment:
    alignments: list[Alignment]
    unmatched_docx_paras: list[int] = field(default_factory=list)
    unmatched_pdf_blocks: list[int] = field(default_factory=list)
    overall_match_rate: float = 0.0


def _docx_paragraphs(doc) -> list[tuple[int, str]]:
    """回 [(index, normalized_text), ...] — 跳過全空段落。表格內段落也算（用 doc.paragraphs
    抓不到 table cell 段，這裡用 walk 的方式抓全部）。"""
    out: list[tuple[int, str]] = []
    idx = 0
    for p in doc.paragraphs:
        txt = _normalize(p.text)
        if txt:
            out.append((idx, txt))
        idx += 1
    # 表格 cell 段
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    txt = _normalize(p.text)
                    if txt:
                        out.append((idx, txt))
                    idx += 1
    return out


def align_docx_to_pdf(docx_doc, pdf_truth: PDFTruth) -> DocxToPdfAlignment:
    """主入口。docx_doc 為 python-docx Document 物件。"""
    pdf_blocks = pdf_truth.all_blocks
    pdf_indexed = [(i, _normalize(b.text)) for i, b in enumerate(pdf_blocks) if _normalize(b.text)]

    docx_paras = _docx_paragraphs(docx_doc)
    if not docx_paras or not pdf_indexed:
        return DocxToPdfAlignment(
            alignments=[],
            unmatched_docx_paras=[i for i, _ in docx_paras],
            unmatched_pdf_blocks=[i for i, _ in pdf_indexed],
            overall_match_rate=0.0,
        )

    pdf_pool: dict[int, str] = dict(pdf_indexed)
    alignments: list[Alignment] = []

    # ----- Round 1: 精確匹配 -----
    used_pdf: set[int] = set()
    matched_docx: set[int] = set()
    pdf_text_index: dict[str, list[int]] = {}
    for pi, ptxt in pdf_pool.items():
        pdf_text_index.setdefault(ptxt, []).append(pi)

    for di, dtxt in docx_paras:
        if dtxt in pdf_text_index:
            cand = [x for x in pdf_text_index[dtxt] if x not in used_pdf]
            if cand:
                pi = cand[0]
                b = pdf_blocks[pi]
                alignments.append(Alignment(
                    docx_para_index=di,
                    pdf_block_refs=[pi],
                    confidence=1.0,
                    method="text_match",
                    text_similarity=1.0,
                    pdf_bbox_union=b.bbox,
                    pdf_dominant_font=b.dominant_font,
                    pdf_dominant_size=b.dominant_size,
                    page_num=b.page_num,
                ))
                used_pdf.add(pi)
                matched_docx.add(di)

    # ----- Round 2: 模糊匹配（rapidfuzz token_set_ratio > 85） -----
    remaining_pdf = [(pi, ptxt) for pi, ptxt in pdf_pool.items() if pi not in used_pdf]
    for di, dtxt in docx_paras:
        if di in matched_docx:
            continue
        best_pi = -1
        best_score = 0.0
        for pi, ptxt in remaining_pdf:
            if pi in used_pdf:
                continue
            # 用 token_set_ratio 對中英混合都不錯（length 差異也容忍）
            score = fuzz.token_set_ratio(dtxt, ptxt) / 100.0
            if score > best_score:
                best_score = score
                best_pi = pi
        if best_pi >= 0 and best_score >= 0.85:
            b = pdf_blocks[best_pi]
            alignments.append(Alignment(
                docx_para_index=di,
                pdf_block_refs=[best_pi],
                confidence=best_score,
                method="fuzzy_match",
                text_similarity=best_score,
                pdf_bbox_union=b.bbox,
                pdf_dominant_font=b.dominant_font,
                pdf_dominant_size=b.dominant_size,
                page_num=b.page_num,
            ))
            used_pdf.add(best_pi)
            matched_docx.add(di)

    # ----- Round 3: 1:N concat match — docx 一段對應 PDF 多 block -----
    # pdf2docx 偶爾把 PDF 多個 block 黏成 docx 一段（中間沒換行）。比對方式：
    # 對每個 unmatched docx para，看 PDF 連續 N 個 unmatched blocks 的 text 拼起來
    # ≈ docx para 的程度。N 從 2 到 5 試（更大就算了）。
    for di, dtxt in docx_paras:
        if di in matched_docx:
            continue
        # 候選 — unmatched PDF block 依原順序
        ordered_pdf = [(pi, ptxt) for pi, ptxt in pdf_indexed
                       if pi not in used_pdf]
        if len(ordered_pdf) < 2:
            continue
        # 滑動視窗 size 2..min(5, 剩餘量)
        best_score = 0.0
        best_window: list[int] = []
        for size in range(2, min(6, len(ordered_pdf) + 1)):
            for start in range(len(ordered_pdf) - size + 1):
                window = ordered_pdf[start:start + size]
                concat = " ".join(t for _, t in window)
                # 兩種 concat：有空白 + 無空白（pdf2docx 黏東西時可能省空白）
                no_ws = "".join(t for _, t in window).replace(" ", "")
                d_no_ws = dtxt.replace(" ", "")
                score1 = fuzz.ratio(dtxt, concat) / 100.0
                score2 = fuzz.ratio(d_no_ws, no_ws) / 100.0
                score = max(score1, score2)
                if score > best_score:
                    best_score = score
                    best_window = [pi for pi, _ in window]
        if best_score >= 0.85 and best_window:
            # 1:N alignment 成立 — 多 block refs
            first_pi = best_window[0]
            b = pdf_blocks[first_pi]
            # union bbox of all blocks in window
            bbs = [pdf_blocks[pi].bbox for pi in best_window]
            union_bbox = (
                min(bb[0] for bb in bbs), min(bb[1] for bb in bbs),
                max(bb[2] for bb in bbs), max(bb[3] for bb in bbs),
            )
            alignments.append(Alignment(
                docx_para_index=di,
                pdf_block_refs=list(best_window),
                confidence=best_score,
                method="fuzzy_match",  # 1:N 也算 fuzzy
                text_similarity=best_score,
                pdf_bbox_union=union_bbox,
                pdf_dominant_font=b.dominant_font,
                pdf_dominant_size=b.dominant_size,
                page_num=b.page_num,
            ))
            for pi in best_window:
                used_pdf.add(pi)
            matched_docx.add(di)

    # ----- Round 4: 順序補位 — 用 PDF block 順序當提示 -----
    unmatched_docx = [(di, dtxt) for di, dtxt in docx_paras if di not in matched_docx]
    remaining_pdf_seq = [pi for pi, _ in pdf_indexed if pi not in used_pdf]
    if unmatched_docx and remaining_pdf_seq:
        # 對齊：把剩餘 docx 段照順序貼到剩餘 PDF blocks 上（一一對應，多者捨）
        for (di, dtxt), pi in zip(unmatched_docx, remaining_pdf_seq):
            b = pdf_blocks[pi]
            score = fuzz.partial_ratio(dtxt, _normalize(b.text)) / 100.0 if dtxt else 0.0
            alignments.append(Alignment(
                docx_para_index=di,
                pdf_block_refs=[pi],
                confidence=min(0.5, score),  # position match 信心上限 0.5
                method="position_match",
                text_similarity=score,
                pdf_bbox_union=b.bbox,
                pdf_dominant_font=b.dominant_font,
                pdf_dominant_size=b.dominant_size,
                page_num=b.page_num,
            ))
            used_pdf.add(pi)
            matched_docx.add(di)

    # 排序成 docx_para_index 順序
    alignments.sort(key=lambda a: a.docx_para_index)
    unmatched_docx_idx = [di for di, _ in docx_paras if di not in matched_docx]
    unmatched_pdf_idx = [pi for pi, _ in pdf_indexed if pi not in used_pdf]
    rate = len(matched_docx) / max(1, len(docx_paras))

    return DocxToPdfAlignment(
        alignments=alignments,
        unmatched_docx_paras=unmatched_docx_idx,
        unmatched_pdf_blocks=unmatched_pdf_idx,
        overall_match_rate=rate,
    )
