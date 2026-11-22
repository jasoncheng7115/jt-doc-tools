"""pdf-to-slides FastAPI router — PDF 轉簡報（.odp / .pptx）。

端點：
  POST /tools/pdf-to-slides/upload   — 上傳 PDF，回 upload_id
  POST /tools/pdf-to-slides/submit   — 啟動轉換 job（output_format）
  POST /tools/pdf-to-slides/convert  — 對外 API：單次 upload + return job_id

與 pdf-to-office 的差別：**只有 jtdt-layout 一顆引擎**（簡報本來就是「頁面 + 絕對
定位物件」，不存在流動排版的取捨），所以沒有引擎選擇與後處理選項。
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...config import settings
from ...core import upload_owner as _uo
from ...core.job_manager import job_manager
from ...core.safe_paths import require_uuid_hex, safe_join
# 前後對照 PNG 的產生方式與 pdf-to-office 完全相同 → 直接共用，不重寫一份
from ..pdf_to_office.router import _generate_preview_pngs

logger = logging.getLogger("app.pdf_to_slides")
router = APIRouter()

_UPLOAD_PREFIX = "p2s"
_FORMATS = ("odp", "pptx")
_API_FORMAT_RE = re.compile(r"^(odp|pptx)$", re.IGNORECASE)


def _src_path(uid: str) -> Path:
    return settings.temp_dir / f"{_UPLOAD_PREFIX}_{uid}.pdf"


def _name_path(uid: str) -> Path:
    return settings.temp_dir / f"{_UPLOAD_PREFIX}_{uid}.name"


def _orig_name(uid: str) -> str:
    try:
        return _name_path(uid).read_text(encoding="utf-8").strip() or "document.pdf"
    except Exception:
        return "document.pdf"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "pdf_to_slides.html",
                                      {"request": request})


@router.get("/preview/{job_id}/{kind}")
@router.get("/preview/{job_id}/{kind}/{page}")
async def preview_png(request: Request, job_id: str, kind: str,
                      page: int | None = None):
    """前後對照 PNG。kind = 'orig' 或 'result'；page = 1-based（預設 1）。"""
    if kind not in ("orig", "result"):
        raise HTTPException(400, "kind must be orig or result")
    require_uuid_hex(job_id, "job_id")
    job = job_manager.get(job_id)
    # 只驗 id 格式不夠 —— 預覽圖是原稿與產出的前幾頁渲染，等於別人文件的內容。
    # 與 /api/jobs/* 同一套判斷（非擁有者一律 404，不確認 id 存在）。
    from app.main import _job_access
    if job and not _job_access(job, request):
        job = None
    if not job:
        raise HTTPException(404, "job 不存在")
    if not job.result_path:
        raise HTTPException(404, "結果未就緒")
    work_dir = Path(job.result_path).parent
    p_num = max(1, min(int(page or 1), 100000))
    png = safe_join(work_dir, f"preview_{kind}_{p_num}.png")
    if not png.exists():
        raise HTTPException(404, "preview PNG 不存在")
    from fastapi.responses import FileResponse
    return FileResponse(str(png), media_type="image/png")


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

    pages, landscape, has_text = 0, False, True
    try:
        import fitz
        d = fitz.open(str(src))
        pages = d.page_count
        if pages:
            r = d.load_page(0).rect
            landscape = r.width > r.height
        has_text = any(d.load_page(i).get_text("text").strip()
                       for i in range(min(3, pages)))
        d.close()
    except Exception:
        pass

    return {
        "upload_id": uid,
        "filename": file.filename,
        "size": len(data),
        "pages": pages,
        "landscape": landscape,
        "is_scanned_likely": (not has_text) and pages > 0,
    }


def _run_convert(job, uid: str, src: Path, stem: str, fmt: str) -> None:
    """共用的轉換流程（web /submit 與對外 API 都走這裡）。"""
    from .engines.slides_engine import convert_pdf_to_slides

    job.message = "簡報轉換中…（jtdt-layout 版面重現）"
    job.progress = 0.1
    work_dir = settings.temp_dir / f"{_UPLOAD_PREFIX}_{uid}_work"
    work_dir.mkdir(exist_ok=True)

    def _progress(msg: str, frac: float) -> None:
        if job.cancelled:
            return
        job.message = "%s（jtdt-layout 版面重現）" % msg
        job.progress = max(0.1, min(0.95, float(frac)))

    ext = ".odp" if fmt == "odp" else ".pptx"
    dst_name = f"{stem}{ext}"
    dst = work_dir / dst_name
    res = convert_pdf_to_slides(src, dst, fmt, timeout=300.0,
                                progress_cb=_progress)
    if not res.get("ok") or not dst.exists():
        raise RuntimeError(res.get("error") or "轉換失敗")
    if job.cancelled:
        return
    job.result_path = dst
    job.result_filename = dst_name
    try:
        preview = _generate_preview_pngs(src, dst, work_dir) or {}
    except Exception as e:  # noqa: BLE001 — 預覽失敗不該讓轉換整個失敗
        logger.warning("slides preview png generation failed: %s", e)
        preview = {}
    # meta 結構與 pdf-to-office 對齊（前端模板共用）：格式標籤與物件量提示都讀
    # meta.summary.* —— 只放 meta.stats 的話，UI 的「格式」欄位與前後對照標題會空白。
    job.meta = dict(job.meta or {})
    job.meta["preview"] = preview
    job.meta["stats"] = {k: res.get(k) for k in ("pages", "images", "objects")}
    job.meta["summary"] = {
        "engine": "jtdt-layout",
        "output_format": fmt,
        "report": {"native_stats": {k: res.get(k)
                                    for k in ("pages", "images", "objects")}},
    }
    job.progress = 1.0
    job.message = "完成"


@router.post("/submit")
async def submit(request: Request):
    body = await request.json()
    uid = (body.get("upload_id") or "").strip()
    require_uuid_hex(uid, "upload_id")
    _uo.require(uid, request)
    output_format: Literal["odp", "pptx"] = (body.get("output_format") or "pptx").lower()
    if output_format not in _FORMATS:
        raise HTTPException(400, "output_format 必須是 odp 或 pptx")
    src = _src_path(uid)
    if not src.exists():
        raise HTTPException(410, "上傳已過期，請重新上傳")
    orig_name = _orig_name(uid)
    stem = Path(orig_name).stem or "presentation"

    def run(job):
        _run_convert(job, uid, src, stem, output_format)

    job = job_manager.submit("pdf-to-slides", run,
                             meta={"filename": orig_name,
                                   "output_format": output_format})
    job.meta["view_url"] = f"/tools/pdf-to-slides/?job={job.id}"   # 「我的作業」的「開啟」回到這一頁、接回結果
    return {"job_id": job.id}


@router.post("/convert", include_in_schema=True)
async def api_convert(request: Request,
                      file: UploadFile = File(...),
                      output_format: str = Form("pptx")):
    """對外 API：單次上傳 PDF → 回 job_id。

    output_format: "pptx"（PowerPoint，預設）或 "odp"（OpenDocument 簡報）。
    只有一顆引擎（jtdt-layout 版面重現），因此沒有 engine 參數。
    """
    if not _API_FORMAT_RE.match(output_format or ""):
        raise HTTPException(400, "output_format 必須是 odp 或 pptx")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF 輸入")
    data = await file.read()
    if not data or data[:4] != b"%PDF":
        raise HTTPException(400, "不是有效的 PDF")
    uid = uuid.uuid4().hex
    _uo.record(uid, request)
    src = _src_path(uid)
    src.write_bytes(data)
    try:
        _name_path(uid).write_text(file.filename or "document.pdf", encoding="utf-8")
    except Exception:
        pass
    stem = Path(file.filename or "document.pdf").stem or "presentation"
    fmt = output_format.lower()

    def run(job):
        _run_convert(job, uid, src, stem, fmt)

    job = job_manager.submit("pdf-to-slides", run,
                             meta={"filename": file.filename,
                                   "output_format": fmt})
    job.meta["view_url"] = f"/tools/pdf-to-slides/?job={job.id}"   # 「我的作業」的「開啟」回到這一頁、接回結果
    return {"job_id": job.id, "download_url": f"/api/jobs/{job.id}/download"}
