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
    convert_via_jtdt_reform,
    convert_via_libreoffice,
    convert_via_pdf2docx,
    docx_to_odt,
)
from .engines.jtdt_reform import convert_via_jtdt_reform_to_odt
from .odt_to_docx import convert_odt_to_docx
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
    engine: Literal["pdf2docx-refine", "jtdt-reform", "jtdt-layout"] = "jtdt-reform",
) -> ConvertResult:
    """主入口。

    Args:
        pdf_path: 來源 PDF
        work_dir: 工作目錄（中間檔 + 最終輸出都放這）
        output_format: "docx" 或 "odt"
        enable_postprocess: 是否跑後處理（False = 純 pdf2docx 原始輸出）
        keep_intermediate: 是否保留 raw_docx 中間檔
        engine: 轉換引擎
          - "jtdt-reform" (預設，v1.8.72 起)：從 PDFTruth 重建 docx，不靠 pdf2docx
          - "pdf2docx-refine" (備用)：pdf2docx + jtdt-refine 後處理（25 fixer）

    Returns:
        ConvertResult
    """
    pdf_path = Path(pdf_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    raw_docx = work_dir / "raw.docx"
    final_docx = work_dir / "final.docx"

    # ----- 分流：jtdt-reform engine（v1.8.82+ 改 ODT-first 路線）-----
    if engine == "jtdt-reform":
        log.info("pdf-to-office: converting %s via jtdt-reform (ODT-first)", pdf_path.name)
        # Safety net 180s timeout
        import signal as _signal
        _orig_handler = None
        if hasattr(_signal, "SIGALRM"):
            def _to_handler(_sig, _frame):
                raise TimeoutError(
                    f"jtdt-reform 處理 {pdf_path.name} 超過 180s — 已中斷以保護機器"
                )
            try:
                _orig_handler = _signal.signal(_signal.SIGALRM, _to_handler)
                _signal.alarm(180)
            except (ValueError, OSError):
                _orig_handler = None
        final_odt = work_dir / "final.odt"
        try:
            res = convert_via_jtdt_reform_to_odt(pdf_path, final_odt)
        except TimeoutError as e:
            log.error("jtdt-reform timeout: %s", e)
            return ConvertResult(
                ok=False, output_path=None, output_format=output_format,
                engine_used="jtdt-reform", postprocess_done=False,
                report={"error": str(e), "primary_engine": "jtdt-reform"},
                error=str(e),
            )
        finally:
            if hasattr(_signal, "SIGALRM") and _orig_handler is not None:
                _signal.alarm(0)
                _signal.signal(_signal.SIGALRM, _orig_handler)
        if not res.get("ok") or not final_odt.exists():
            return ConvertResult(
                ok=False, output_path=None, output_format=output_format,
                engine_used="jtdt-reform", postprocess_done=False, report=res,
                error=res.get("error") or "jtdt-reform engine 失敗",
            )
        report = {
            "primary_engine": "jtdt-reform-odt",
            "postprocess_engine": "",
            "postprocess_engine_version": "",
            "postprocess_fixers_count": 0,
            "native_stats": res,
        }
        # ODT 是主輸出；docx 要的話用 soffice convert
        if output_format == "odt":
            output_path = final_odt
        elif output_format == "docx":
            output_path = work_dir / "final.docx"
            d_res = convert_odt_to_docx(final_odt, output_path)
            if not d_res["ok"]:
                # 失敗 fallback：直接給 ODT
                log.warning("ODT→docx 失敗，回傳 ODT: %s", d_res.get("error"))
                return ConvertResult(
                    ok=True, output_path=final_odt, output_format="odt",
                    engine_used="jtdt-reform", postprocess_done=False,
                    report=report,
                    error=f"ODT→docx 失敗，已改回 ODT 輸出：{d_res.get('error')}",
                )
        else:
            return ConvertResult(
                ok=False, output_path=None, output_format=output_format,
                engine_used="jtdt-reform", postprocess_done=False, report=report,
                error=f"不支援的輸出格式：{output_format}",
            )
        return ConvertResult(
            ok=True, output_path=output_path, output_format=output_format,
            engine_used="jtdt-reform", postprocess_done=False, report=report,
        )

    # ----- 分流：jtdt-layout engine（v1.12.83+ 版面重現，完全隔離不碰另兩顆）-----
    if engine == "jtdt-layout":
        from .engines.draw_engine import convert_via_draw
        log.info("pdf-to-office: converting %s via jtdt-layout (版面重現)", pdf_path.name)
        out = work_dir / ("final.odt" if output_format == "odt" else "final.docx")
        if output_format not in ("odt", "docx"):
            return ConvertResult(
                ok=False, output_path=None, output_format=output_format,
                engine_used="jtdt-layout", postprocess_done=False, report={},
                error=f"不支援的輸出格式：{output_format}",
            )
        res = convert_via_draw(pdf_path, out, output_format, timeout=180.0)
        if not res.get("ok") or not out.exists():
            return ConvertResult(
                ok=False, output_path=None, output_format=output_format,
                engine_used="jtdt-layout", postprocess_done=False, report=res,
                error=res.get("error") or "jtdt-layout engine 失敗",
            )
        report = {
            "primary_engine": "jtdt-layout",
            "postprocess_engine": "",
            "postprocess_engine_version": "",
            "postprocess_fixers_count": 0,
            "native_stats": res,
        }
        return ConvertResult(
            ok=True, output_path=out, output_format=output_format,
            engine_used="jtdt-layout", postprocess_done=False, report=report,
        )

    # ----- Step 1: pdf2docx (預設 engine) -----
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

    # ----- Step 2: postprocess (jtdt-refine 規則引擎) -----
    # 兩段引擎分離回報：
    #   primary_engine  = pdf2docx / libreoffice  (上游 raw 轉檔)
    #   postprocess_engine = jtdt-refine v<X>  (本專案 17+ 個 fixer / bbox 真值校正)
    JTDT_POSTPROC_ENGINE = "jtdt-refine"
    JTDT_POSTPROC_VERSION = "1.4"
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
    # 標記後處理引擎資訊（給 UI 顯示）
    report["primary_engine"] = engine_used
    report["postprocess_engine"] = JTDT_POSTPROC_ENGINE if enable_postprocess else ""
    report["postprocess_engine_version"] = JTDT_POSTPROC_VERSION if enable_postprocess else ""
    report["postprocess_fixers_count"] = len(report.get("fixers", []))

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
