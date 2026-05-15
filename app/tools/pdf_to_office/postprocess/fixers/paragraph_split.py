"""段落拆分 fixer。

處理 pdf2docx 把 PDF 兩個獨立 block 錯誤合併成一段的情境。

完全依賴 PDFTruth alignment 1:N — 若 docx 一個段落對應 PDF 多個 block，
且這些 block 之間 y 距離 > 1.5 倍行高 → 拆回多段。

注意：v1.8.x aligner 還沒實作 1:N，所以這個 fixer 暫時 skip — 留下骨架等
aligner 強化後啟用。Sprint 2 階段先記 changelog，不執行。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def fix_paragraph_split(docx_doc, pdf_truth, alignment) -> dict:
    """目前 placeholder — 等 aligner 支援 1:N alignments 才能真正拆。

    要做 1:N，aligner 需要：
    - 對 docx 一個段落 P，找出 PDF blocks B1, B2 ... 連續對應
    - 判斷 P.text ≈ B1.text + B2.text (concat)
    - 若是，把 P 在對應位置拆成多段
    """
    return {"fixer": "paragraph_split", "split": 0, "note": "needs 1:N aligner (Sprint 3)"}
