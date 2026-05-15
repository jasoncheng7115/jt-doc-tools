"""第一階段：診斷掃描。不修改 docx，只產出問題清單。

Sprint 1 範圍：基本指標 + 破碎段落 + 對齊率 + alignment 摘要。完整 issue 偵測
表（17 種）留給 Sprint 2 補。
"""
from __future__ import annotations

from typing import Any

from ..pdf_truth import PDFTruth
from ..pdf_truth.aligner import DocxToPdfAlignment


def diagnose(docx_doc, pdf_truth: PDFTruth, alignment: DocxToPdfAlignment) -> dict[str, Any]:
    """主入口。回傳 diagnosis_report dict（結構符合 spec §5.1.3）。"""
    docx_para_count = len(docx_doc.paragraphs)
    docx_table_count = len(docx_doc.tables)
    pdf_block_count = sum(
        1 for p in pdf_truth.pages for b in p.blocks if b.block_type == "text"
    )
    pdf_image_count = sum(len(p.images) for p in pdf_truth.pages)

    # 中文字元比例
    sample = "".join(p.text for p in docx_doc.paragraphs)[:5000]
    cjk_count = sum(1 for ch in sample if "㐀" <= ch <= "鿿")
    cjk_ratio = (cjk_count / len(sample)) if sample else 0.0

    # 對齊摘要
    high_conf = sum(1 for a in alignment.alignments if a.confidence >= 0.85)
    low_conf = sum(1 for a in alignment.alignments if a.confidence < 0.85 and a.confidence > 0)

    issues: list[dict[str, Any]] = []

    # === 破碎段落偵測（簡化版） ===
    # docx 段落很短（<10 字）+ 結尾無句號 + alignment 對應 PDF block 比較長 → 破碎
    sentence_end = "。．.！!？?：:；;"
    al_by_di = {a.docx_para_index: a for a in alignment.alignments}
    for di, p in enumerate(docx_doc.paragraphs):
        txt = (p.text or "").strip()
        if len(txt) < 10 and txt and not (txt[-1] in sentence_end):
            a = al_by_di.get(di)
            if a and a.pdf_block_refs:
                pdf_block = pdf_truth.all_blocks[a.pdf_block_refs[0]]
                if len(pdf_block.text) > len(txt) * 1.5:
                    issues.append({
                        "id": f"frag_p_{di}",
                        "type": "fragmented_paragraph",
                        "severity": "medium",
                        "location": {
                            "paragraph_index": di,
                            "page_hint": a.page_num,
                            "pdf_bbox": list(a.pdf_bbox_union) if a.pdf_bbox_union else None,
                            "context_snippet": txt[:50],
                        },
                        "evidence": f"docx {len(txt)} 字 vs PDF block {len(pdf_block.text)} 字",
                        "evidence_source": "pdf_truth_compared",
                        "suggested_fix": "與下一段合併",
                        "auto_fixable": True,
                    })

    return {
        "summary": {
            "total_paragraphs_docx": docx_para_count,
            "total_blocks_pdf": pdf_block_count,
            "total_tables_docx": docx_table_count,
            "total_images_docx": 0,  # TODO Sprint 2: 數 docx 圖片
            "total_images_pdf": pdf_image_count,
            "primary_body_font_size_pdf": pdf_truth.body_font_size,
            "primary_body_font_pdf": pdf_truth.body_font_name,
            "cjk_ratio": round(cjk_ratio, 3),
            "language_guess": pdf_truth.language_guess,
            "page_count": pdf_truth.total_pages,
            "has_scanned_pages": pdf_truth.has_scanned_pages,
        },
        "alignment": {
            "overall_match_rate": round(alignment.overall_match_rate, 3),
            "high_confidence_count": high_conf,
            "low_confidence_count": low_conf,
            "unmatched_docx_count": len(alignment.unmatched_docx_paras),
            "unmatched_pdf_count": len(alignment.unmatched_pdf_blocks),
        },
        "issues": issues,
    }
