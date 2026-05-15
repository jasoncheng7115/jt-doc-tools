"""pdf-to-office FastAPI router — Sprint 1。

端點：
  POST /tools/pdf-to-office/upload    — 上傳 PDF，回 upload_id
  POST /tools/pdf-to-office/submit    — 啟動轉換 job (output_format / postprocess)
  POST /api/pdf-to-office/convert     — 對外 API：單次 upload + return job_id
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse

from ...config import settings
from ...core import upload_owner as _uo
from ...core.job_manager import job_manager
from ...core.safe_paths import require_uuid_hex

logger = logging.getLogger("app.pdf_to_office")
router = APIRouter()

_UPLOAD_PREFIX = "p2o"


def _src_path(uid: str) -> Path:
    return settings.temp_dir / f"{_UPLOAD_PREFIX}_{uid}_in.pdf"


def _name_path(uid: str) -> Path:
    return settings.temp_dir / f"{_UPLOAD_PREFIX}_{uid}_name.txt"


def _orig_name(uid: str) -> str:
    p = _name_path(uid)
    if p.exists():
        try:
            return p.read_text(encoding="utf-8").strip() or "document.pdf"
        except Exception:
            return "document.pdf"
    return "document.pdf"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "pdf_to_office.html",
        {"request": request},
    )


@router.get("/report/{job_id}")
async def download_report(request: Request, job_id: str):
    """下載某 job 的 Markdown 改善報告。"""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "job 不存在")
    summary = (job.meta or {}).get("summary") or {}
    rep = summary.get("report") or {}
    if not rep:
        raise HTTPException(404, "找不到報告（job 還沒完成或非後處理過）")
    from .postprocess.report import render_markdown_report
    src_filename = (job.meta or {}).get("filename", "")
    md = render_markdown_report(rep, src_filename=src_filename)
    headers = {"content-disposition": f'attachment; filename="pdf-to-office-report-{job_id}.md"'}
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8", headers=headers)


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    """收 PDF，回 upload_id + 基本資訊。"""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF 輸入")
    data = await file.read()
    if not data:
        raise HTTPException(400, "空檔")
    if data[:4] != b"%PDF":
        raise HTTPException(400, "不是有效的 PDF（缺少 %PDF magic）")
    uid = uuid.uuid4().hex
    _uo.record(uid, request)
    src = _src_path(uid)
    src.write_bytes(data)
    try:
        _name_path(uid).write_text(file.filename or "document.pdf", encoding="utf-8")
    except Exception:
        pass

    # 估算頁數 + 是否掃描檔
    try:
        import fitz
        d = fitz.open(str(src))
        pages = d.page_count
        has_text = any(d.load_page(i).get_text("text").strip() for i in range(min(3, pages)))
        d.close()
    except Exception:
        pages = 0
        has_text = True

    return {
        "upload_id": uid,
        "filename": file.filename,
        "size": len(data),
        "pages": pages,
        "is_scanned_likely": (not has_text) and pages > 0,
    }


@router.post("/submit")
async def submit(request: Request):
    body = await request.json()
    uid = (body.get("upload_id") or "").strip()
    require_uuid_hex(uid, "upload_id")
    _uo.require(uid, request)
    output_format: Literal["docx", "odt"] = (body.get("output_format") or "docx").lower()
    if output_format not in ("docx", "odt"):
        raise HTTPException(400, "output_format 必須是 docx 或 odt")
    enable_postprocess = bool(body.get("enable_postprocess", True))
    # Per-fixer toggle dict（key 例：enable_font_normalize）— 白名單過濾，不接 unknown
    raw_opts = body.get("fixer_opts") or {}
    _ALLOWED_FIXER_KEYS = {
        "enable_font_normalize", "enable_paragraph_merge", "enable_paragraph_split",
        "enable_heading_detect", "enable_list_detect", "enable_header_footer",
        "enable_image_position_fix", "enable_cjk_typography", "enable_cleanup",
        "enable_fake_table_remove", "enable_table_autofit",
        "enable_table_normalize", "enable_style_apply",
    }
    fixer_opts = {k: bool(v) for k, v in raw_opts.items() if k in _ALLOWED_FIXER_KEYS}

    src = _src_path(uid)
    if not src.exists():
        raise HTTPException(410, "上傳已過期，請重新上傳")
    orig_name = _orig_name(uid)
    stem = Path(orig_name).stem or "document"

    def run(job):
        from .service import convert_pdf_to_office

        job.message = "PDF 轉換中…（pdf2docx + 後處理）"
        job.progress = 0.1
        work_dir = settings.temp_dir / f"{_UPLOAD_PREFIX}_{uid}_work"
        work_dir.mkdir(exist_ok=True)
        result = convert_pdf_to_office(
            src, work_dir, output_format,
            enable_postprocess=enable_postprocess,
            keep_intermediate=False,
            fixer_opts=fixer_opts,
        )
        if not result.ok:
            raise RuntimeError(result.error or "轉換失敗")

        # 結果搬到 stable 名稱
        ext = ".odt" if output_format == "odt" else ".docx"
        dst_name = f"{stem}{ext}"
        dst = work_dir / dst_name
        if result.output_path and result.output_path != dst:
            import shutil
            shutil.move(str(result.output_path), str(dst))

        job.result_path = dst
        job.result_filename = dst_name
        job.progress = 1.0
        report = result.report or {}
        msg_parts = [f"完成：{dst.stat().st_size // 1024} KB"]
        if report.get("alignment"):
            mr = report["alignment"]["match_rate"]
            msg_parts.append(f"對齊率 {mr*100:.0f}%")
        if report.get("fixers"):
            for f in report["fixers"]:
                if f.get("fixer") == "paragraph_merge" and f.get("merged"):
                    msg_parts.append(f"合併段落 {f['merged']}")
                if f.get("fixer") == "cleanup":
                    if f.get("removed_empty_paragraphs"):
                        msg_parts.append(f"清空段 {f['removed_empty_paragraphs']}")
        job.message = "、".join(msg_parts)
        job.meta = dict(job.meta or {})
        job.meta["summary"] = {
            "engine": result.engine_used,
            "output_format": output_format,
            "postprocess_done": result.postprocess_done,
            "report": report,
        }

    job = job_manager.submit("pdf-to-office", run,
                              meta={"filename": orig_name, "output_format": output_format})
    return {"job_id": job.id}


# ---- 對外 API：單次 upload + return job_id ----
_API_FORMAT_RE = re.compile(r"^(docx|odt)$", re.IGNORECASE)


@router.post("/convert", include_in_schema=True)
async def api_convert(request: Request,
                      file: UploadFile = File(...),
                      output_format: str = "docx",
                      enable_postprocess: bool = True):
    """對外 API：單次上傳 PDF + return job_id。"""
    if not _API_FORMAT_RE.match(output_format or ""):
        raise HTTPException(400, "output_format 必須是 docx 或 odt")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF 輸入")
    data = await file.read()
    if not data or data[:4] != b"%PDF":
        raise HTTPException(400, "不是有效的 PDF")
    uid = uuid.uuid4().hex
    _uo.record(uid, request)
    src = _src_path(uid)
    src.write_bytes(data)
    _name_path(uid).write_text(file.filename or "document.pdf", encoding="utf-8")
    stem = Path(file.filename or "document.pdf").stem or "document"
    fmt = output_format.lower()

    def run(job):
        from .service import convert_pdf_to_office
        job.message = "轉換中…"
        job.progress = 0.1
        work_dir = settings.temp_dir / f"{_UPLOAD_PREFIX}_{uid}_work"
        work_dir.mkdir(exist_ok=True)
        result = convert_pdf_to_office(
            src, work_dir, fmt,
            enable_postprocess=enable_postprocess,
        )
        if not result.ok:
            raise RuntimeError(result.error or "轉換失敗")
        ext = ".odt" if fmt == "odt" else ".docx"
        dst_name = f"{stem}{ext}"
        dst = work_dir / dst_name
        if result.output_path and result.output_path != dst:
            import shutil
            shutil.move(str(result.output_path), str(dst))
        job.result_path = dst
        job.result_filename = dst_name
        job.progress = 1.0
        job.message = "完成"

    job = job_manager.submit("pdf-to-office", run,
                              meta={"filename": file.filename, "output_format": fmt})
    return {"job_id": job.id, "download_url": f"/api/jobs/{job.id}/download"}
