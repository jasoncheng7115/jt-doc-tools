"""後處理管線。Sprint 1 順序：診斷 → font_normalize → paragraph_merge → cleanup → style_apply。"""
from __future__ import annotations

import logging
from pathlib import Path

from docx import Document

from ..pdf_truth import PDFTruth, extract_pdf_truth
from ..pdf_truth.aligner import align_docx_to_pdf
from .diagnose import diagnose
from .fixers import (
    fix_cjk_typography,
    fix_cleanup,
    fix_fake_table_remove,
    fix_font_normalize,
    fix_header_footer,
    fix_heading_detect,
    fix_list_detect,
    fix_paragraph_merge,
    fix_paragraph_split,
)
from .style_apply import apply_styles

log = logging.getLogger(__name__)


def run_postprocess(
    pdf_path: Path,
    docx_input: Path,
    docx_output: Path,
    *,
    enable_font_normalize: bool = True,
    enable_paragraph_merge: bool = True,
    enable_paragraph_split: bool = True,
    enable_heading_detect: bool = True,
    enable_list_detect: bool = True,
    enable_header_footer: bool = True,
    enable_cjk_typography: bool = True,
    enable_cleanup: bool = True,
    enable_style_apply: bool = True,
    enable_fake_table_remove: bool = True,
) -> dict:
    """主入口。讀 PDF + docx → 跑 fixer → 寫到 docx_output。

    回 report dict：含 diagnosis、fixer changelogs、style apply summary。
    任何 fixer 失敗單獨記錯，不影響其他 fixer。
    """
    docx_input = Path(docx_input)
    docx_output = Path(docx_output)
    docx_output.parent.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "pdf_truth": None,
        "alignment": None,
        "diagnosis": None,
        "fixers": [],
        "style_apply": None,
        "errors": [],
    }

    # ------ Step 1: PDF 真值解析 ------
    try:
        pdf_truth: PDFTruth | None = extract_pdf_truth(pdf_path)
        report["pdf_truth"] = {
            "pages": pdf_truth.total_pages,
            "language": pdf_truth.language_guess,
            "body_font": pdf_truth.body_font_name,
            "body_size": pdf_truth.body_font_size,
            "has_scanned": pdf_truth.has_scanned_pages,
            "fonts": [
                {"name": f.name, "embedded": f.is_embedded, "cmap": f.has_tounicode,
                 "cjk": f.is_cjk, "use": f.usage_count}
                for f in pdf_truth.fonts
            ],
        }
    except Exception as e:
        log.exception("PDF truth extraction failed")
        report["errors"].append(f"pdf_truth: {e}")
        pdf_truth = None

    # ------ Step 2: 開 docx ------
    try:
        docx_doc = Document(str(docx_input))
    except Exception as e:
        log.exception("docx open failed")
        report["errors"].append(f"docx_open: {e}")
        # 致命：直接 copy 原 docx 過去當輸出，不做後處理
        import shutil as _sh
        _sh.copy2(str(docx_input), str(docx_output))
        return report

    # ------ Step 3: alignment (只在有 PDFTruth 時) ------
    alignment = None
    if pdf_truth is not None:
        try:
            alignment = align_docx_to_pdf(docx_doc, pdf_truth)
            report["alignment"] = {
                "match_rate": alignment.overall_match_rate,
                "matched": len(alignment.alignments),
                "unmatched_docx": len(alignment.unmatched_docx_paras),
                "unmatched_pdf": len(alignment.unmatched_pdf_blocks),
            }
        except Exception as e:
            log.exception("alignment failed")
            report["errors"].append(f"alignment: {e}")

    # ------ Step 4: 診斷 ------
    if pdf_truth and alignment:
        try:
            report["diagnosis"] = diagnose(docx_doc, pdf_truth, alignment)
        except Exception as e:
            log.exception("diagnose failed")
            report["errors"].append(f"diagnose: {e}")

    # ------ Step 5: fixers ------
    fixer_specs = []
    # 順序重要 — fixer 之間有依賴：
    # 1. 假表格還原成段落（不然後續 fixer 看不到 cell 內段落）
    # 2. 字型正規化（讓 paragraph_merge 的 monospace 偵測能用對的字型名）
    # 3. 段落合併（先合併才看得出哪些是真正獨立段落）
    # 4. 段落拆分（合併完後若還有 1:N alignment 拆回）
    # 5. 標題識別（用乾淨段落 + 字級判斷）
    # 6. 清單識別（標題識別後剩下的看是不是清單）
    # 7. 頁首頁尾識別（先把可能的頁首頁尾從內文移走）
    # 8. 中文排版（用最終文字內容做空白清理）
    # 9. 雜訊清理（最後清空段落 / 過小圖片）
    if enable_fake_table_remove:
        fixer_specs.append(("fake_table_remove", fix_fake_table_remove))
    if enable_font_normalize:
        fixer_specs.append(("font_normalize", fix_font_normalize))
    if enable_paragraph_merge:
        fixer_specs.append(("paragraph_merge", fix_paragraph_merge))
    if enable_paragraph_split:
        fixer_specs.append(("paragraph_split", fix_paragraph_split))
    if enable_heading_detect:
        fixer_specs.append(("heading_detect", fix_heading_detect))
    if enable_list_detect:
        fixer_specs.append(("list_detect", fix_list_detect))
    if enable_header_footer:
        fixer_specs.append(("header_footer", fix_header_footer))
    if enable_cjk_typography:
        fixer_specs.append(("cjk_typography", fix_cjk_typography))
    if enable_cleanup:
        fixer_specs.append(("cleanup", fix_cleanup))

    for name, fn in fixer_specs:
        if pdf_truth is None or alignment is None:
            # 沒 PDFTruth 時 fixer 仍能跑（降級 — 不用 alignment 強化）— 但 paragraph_merge
            # 跟 font_normalize 都需要 alignment 物件。給空 alignment：
            from ..pdf_truth.aligner import DocxToPdfAlignment
            _alignment = DocxToPdfAlignment(alignments=[], unmatched_docx_paras=[],
                                             unmatched_pdf_blocks=[], overall_match_rate=0.0)
        else:
            _alignment = alignment
        try:
            log = logging.getLogger(__name__)
            ch = fn(docx_doc, pdf_truth, _alignment)
            report["fixers"].append(ch)
        except Exception as e:
            logging.getLogger(__name__).exception("%s fixer failed", name)
            report["errors"].append(f"{name}: {e}")

    # ------ Step 6: style_apply ------
    if enable_style_apply and pdf_truth is not None:
        try:
            report["style_apply"] = apply_styles(docx_doc, pdf_truth)
        except Exception as e:
            logging.getLogger(__name__).exception("style_apply failed")
            report["errors"].append(f"style_apply: {e}")

    # ------ Step 7: save ------
    try:
        docx_doc.save(str(docx_output))
    except Exception as e:
        logging.getLogger(__name__).exception("save docx failed")
        report["errors"].append(f"save: {e}")
        import shutil as _sh
        _sh.copy2(str(docx_input), str(docx_output))

    return report
