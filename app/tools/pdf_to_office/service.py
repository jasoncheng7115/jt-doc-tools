"""pdf-to-office 主轉換邏輯。

流程：
1. pdf2docx 轉 PDF → 中間 docx (raw_docx)
2. 後處理管線：raw_docx → 改善 docx (final_docx)
3. 若 output_format == "odt"：用 LibreOffice 把 final_docx 轉 odt
4. 失敗安全：pdf2docx 失敗 fallback LibreOffice 直轉
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .engines import (
    convert_via_libreoffice,
    convert_via_pdf2docx,
    docx_to_odt,
)
from .postprocess import run_postprocess

log = logging.getLogger(__name__)


@dataclass
class ConvertResult:
    ok: bool
    output_path: Path | None
    output_format: str
    engine_used: str  # "pdf2docx" | "libreoffice"
    postprocess_done: bool
    report: dict
    error: str = ""


def convert_pdf_to_office(
    pdf_path: Path,
    work_dir: Path,
    output_format: Literal["docx", "odt"] = "docx",
    *,
    enable_postprocess: bool = True,
    keep_intermediate: bool = False,
    fixer_opts: dict | None = None,
) -> ConvertResult:
    """主入口。

    Args:
        pdf_path: 來源 PDF
        work_dir: 工作目錄（中間檔 + 最終輸出都放這）
        output_format: "docx" 或 "odt"
        enable_postprocess: 是否跑後處理（False = 純 pdf2docx 原始輸出）
        keep_intermediate: 是否保留 raw_docx 中間檔

    Returns:
        ConvertResult
    """
    pdf_path = Path(pdf_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    raw_docx = work_dir / "raw.docx"
    final_docx = work_dir / "final.docx"

    # ----- Step 1: pdf2docx -----
    log.info("pdf-to-office: converting %s via pdf2docx", pdf_path.name)
    res = convert_via_pdf2docx(pdf_path, raw_docx)
    engine_used = "pdf2docx"
    if not res["ok"] or not raw_docx.exists():
        log.warning("pdf2docx failed (%s), trying LibreOffice fallback", res.get("error"))
        res2 = convert_via_libreoffice(pdf_path, raw_docx)
        if not res2["ok"] or not raw_docx.exists():
            return ConvertResult(
                ok=False, output_path=None, output_format=output_format,
                engine_used="none", postprocess_done=False, report={},
                error=f"pdf2docx: {res.get('error')} | libreoffice: {res2.get('error')}",
            )
        engine_used = "libreoffice"

    # ----- Step 2: postprocess -----
    report: dict = {}
    if enable_postprocess:
        try:
            report = run_postprocess(pdf_path, raw_docx, final_docx, **(fixer_opts or {}))
        except Exception as e:
            log.exception("postprocess failed — using raw output")
            report = {"errors": [f"postprocess: {e}"]}
            import shutil
            shutil.copy2(str(raw_docx), str(final_docx))
    else:
        import shutil
        shutil.copy2(str(raw_docx), str(final_docx))

    # ----- Step 3: format conversion -----
    if output_format == "docx":
        output_path = final_docx
    elif output_format == "odt":
        output_path = work_dir / "final.odt"
        odt_res = docx_to_odt(final_docx, output_path)
        if not odt_res["ok"]:
            return ConvertResult(
                ok=False, output_path=final_docx, output_format="docx",
                engine_used=engine_used, postprocess_done=enable_postprocess, report=report,
                error=f"docx→odt 失敗，已輸出 .docx 替代：{odt_res.get('error')}",
            )
    else:
        return ConvertResult(
            ok=False, output_path=None, output_format=output_format,
            engine_used=engine_used, postprocess_done=enable_postprocess, report=report,
            error=f"不支援的輸出格式：{output_format}",
        )

    # ----- 清理中間檔 -----
    if not keep_intermediate and raw_docx != output_path:
        raw_docx.unlink(missing_ok=True)
    if output_format == "odt" and not keep_intermediate:
        final_docx.unlink(missing_ok=True)

    return ConvertResult(
        ok=True, output_path=output_path, output_format=output_format,
        engine_used=engine_used, postprocess_done=enable_postprocess, report=report,
    )
