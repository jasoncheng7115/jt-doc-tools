"""PDF 文字層補建 endpoints."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core import job_manager as _jm
from ...core import upload_owner as _uo
from ...core.safe_paths import is_uuid_hex, require_uuid_hex
from . import ocr_core

router = APIRouter()


def _work_dir() -> Path:
    p = settings.temp_dir / "pdf_ocr"
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.get("/", response_class=HTMLResponse)
async def page(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    # 提供前端：tesseract / LLM 可用狀態
    tess_ok = ocr_core.is_tesseract_available()
    langs = ocr_core.get_active_langs() if tess_ok else ""
    llm_ok = False
    llm_model = ""
    try:
        from app.core.llm_settings import llm_settings
        llm_ok = llm_settings.is_enabled()
        if llm_ok:
            llm_model = llm_settings.get_model_for("pdf-ocr")
    except Exception:
        pass
    return templates.TemplateResponse("pdf_ocr.html", {
        "request": request,
        "title": "PDF 文字層補建",
        "tesseract_ok": tess_ok,
        "ocr_langs": langs,
        "llm_ok": llm_ok,
        "llm_model": llm_model,
    })


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空檔案")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(400, "檔案超過 200 MB 上限")
    upload_id = uuid.uuid4().hex
    src = _work_dir() / f"po_{upload_id}_src.pdf"
    src.write_bytes(raw)
    _uo.record(upload_id, request)
    return {"upload_id": upload_id, "filename": file.filename or "input.pdf",
            "size": len(raw)}


@router.post("/run/{upload_id}")
async def run_ocr(upload_id: str, request: Request,
                   langs: str = Form(""),
                   dpi: int = Form(300),
                   skip_pages_with_text: bool = Form(True),
                   use_llm: bool = Form(False)):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _work_dir() / f"po_{upload_id}_src.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")
    out = _work_dir() / f"po_{upload_id}_out.pdf"
    active_langs = (langs or ocr_core.get_active_langs()).strip() or "eng"
    dpi = max(72, min(dpi, 600))

    # LLM 後處理 callback
    llm_cb = None
    llm_model_used = ""
    if use_llm:
        try:
            from app.core.llm_settings import llm_settings
            if llm_settings.is_enabled():
                client = llm_settings.make_client()
                llm_model_used = llm_settings.get_model_for("pdf-ocr")
                if client and llm_model_used:
                    def _llm_cb(raw_text: str) -> str:
                        prompt = (
                            "以下是 OCR 軟體（tesseract）對掃描文件的識別結果，"
                            "可能含 typo / 字元混淆 / 字符遺漏 / 排版錯誤。"
                            "請只修正明顯錯字（如 0/O、1/l、空白多餘、CJK 偏旁誤判），"
                            "**保持 word 數量完全一致**，**保持原語言（中翻中、英翻英）**，"
                            "**不要新增解釋 / 標點 / 段落**。\n\n"
                            f"原文：\n{raw_text}\n\n"
                            "只輸出修正後文字，不要任何前綴 / 後綴。"
                        )
                        try:
                            return client.text_query(prompt=prompt, model=llm_model_used,
                                                     temperature=0.0, max_tokens=2048,
                                                     think=False) or ""
                        except Exception:
                            return ""
                    llm_cb = _llm_cb
        except Exception:
            pass

    def _run(job: "_jm.Job") -> None:
        def _progress(cur, total, msg):
            job.progress = cur / max(total, 1) * 0.95
            job.message = msg
        try:
            stats = ocr_core.ocr_pdf_to_searchable(
                src, out,
                langs=active_langs, dpi=dpi,
                skip_pages_with_text=skip_pages_with_text,
                progress_cb=_progress,
                llm_postprocess=llm_cb,
            )
            job.message = (f"完成 — 處理 {stats['pages_ocrd']}/{stats['pages_total']} 頁，"
                           f"插入 {stats['words_inserted']} 字"
                           + (f"，LLM 後處理 ({llm_model_used})" if stats['llm_used'] else ""))
            job.meta = {"upload_id": upload_id, "stats": stats,
                        "langs": active_langs, "llm_model": llm_model_used,
                        "download_url": f"/tools/pdf-ocr/download/{upload_id}"}
            job.result_path = out
            job.result_filename = (Path(src).stem.replace("_src", "") + "_searchable.pdf")
        except Exception as e:
            job.error = str(e)
            raise

    job = _jm.job_manager.submit("pdf-ocr", _run, meta={"upload_id": upload_id})
    return {"job_id": job.id, "upload_id": upload_id}


@router.get("/download/{upload_id}")
async def download(upload_id: str, request: Request):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    out = _work_dir() / f"po_{upload_id}_out.pdf"
    if not out.exists():
        raise HTTPException(404, "尚未產生輸出（請先觸發檢核）")
    src = _work_dir() / f"po_{upload_id}_src.pdf"
    name = "searchable.pdf"
    if src.exists():
        name = src.stem.replace("po_", "").replace("_src", "") + "_searchable.pdf"
    from app.core.http_utils import content_disposition
    return FileResponse(out, media_type="application/pdf",
                          headers={"Content-Disposition": content_disposition(name)})
