"""頁面尺寸統一 —— 把混合尺寸的頁面統一成同一種紙張。

標案、工程文件常是 A3 圖說混 A4 內文。送印、裝訂、掃描歸檔之前都得統一，
不然印表機每換一種尺寸就停一次，裝訂完也會有幾頁凸出來。

上傳後**先分析有幾種尺寸**再讓使用者決定 —— 很多時候使用者根本不知道自己那份
文件是混的。
"""
from __future__ import annotations

import asyncio as _asyncio
import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from ...config import settings
from ...core import office_convert, pdf_preview, upload_owner as _uo
from ...core.job_manager import job_manager
from ...core.pdf_guard import ensure_readable_pdf
from ...core.safe_paths import require_uuid_hex
from . import resize_core as RC

router = APIRouter()

#: 暫存前綴。不可以含底線以外的分隔（`extract_upload_id` 靠它切 id）。
_PREFIX = "ps"


def _src(uid: str) -> Path:
    return settings.temp_dir / f"{_PREFIX}_{uid}.pdf"


def _out(uid: str) -> Path:
    return settings.temp_dir / f"{_PREFIX}_{uid}_out.pdf"


def _spec(**kw) -> RC.ResizeSpec:
    paper = (kw.get("paper") or "a4").lower()
    if paper != "custom" and paper not in RC.PAPERS:
        paper = "a4"
    return RC.ResizeSpec(
        paper=paper,
        custom_w_mm=max(20.0, min(2000.0, float(kw.get("custom_w_mm") or 210))),
        custom_h_mm=max(20.0, min(2000.0, float(kw.get("custom_h_mm") or 297))),
        orientation=(kw.get("orientation") if kw.get("orientation")
                     in ("auto", "portrait", "landscape") else "auto"),
        fit=(kw.get("fit") if kw.get("fit") in ("scale", "center", "crop")
             else "scale"),
        align="top-left" if kw.get("align") == "top-left" else "center",
        keep_same=bool(kw.get("keep_same", True)),
    )


@router.get("/", response_class=HTMLResponse)
async def page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "pdf_page_size.html",
        {"request": request, "title": "頁面尺寸統一",
         "papers": sorted(RC.PAPERS)})


@router.post("/load")
async def load(request: Request, file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "檔案是空的")
    name = file.filename or ""
    uid = uuid.uuid4().hex
    _uo.record(uid, request)
    dst = _src(uid)
    if data[:5] == b"%PDF-":
        dst.write_bytes(data)
    elif office_convert.is_office_file(name):
        raw = settings.temp_dir / f"{_PREFIX}raw_{uid}{Path(name).suffix}"
        raw.write_bytes(data)
        try:
            await office_convert.convert_to_pdf_async(raw, dst)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "文書檔轉換失敗，請確認檔案是否完整")
        finally:
            raw.unlink(missing_ok=True)
    else:
        raise HTTPException(400, "只支援 PDF 與文書檔")
    if not dst.exists():
        raise HTTPException(400, "檔案讀取失敗")
    # **先告訴使用者他的文件是不是混的** —— 多數人並不知道。
    # 毀損 / 加密的檔要回 400 不是 500（`analyze` 內部會 `fitz.open`，
    # 截斷的檔丟 FileDataError、加密的檔丟「document closed or encrypted」）。
    ensure_readable_pdf(dst)
    info = RC.analyze(dst)
    return {"upload_id": uid, "filename": name, **info,
            "pages": [{"page": i + 1,
                       "thumb": f"/tools/pdf-page-size/thumb/{uid}/{i + 1}"}
                      for i in range(min(info["total"], 400))]}


@router.get("/thumb/{upload_id}/{page_no}")
async def thumb(upload_id: str, page_no: int, request: Request):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    out = settings.temp_dir / f"{_PREFIX}_{upload_id}_t_{page_no}.png"
    if not out.exists():
        try:
            await pdf_preview.render_page_png_async(src, out, page_no - 1, dpi=70)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return FileResponse(str(out), media_type="image/png",
                        headers={"Cache-Control": "max-age=300"})


@router.post("/preview")
async def preview(request: Request, upload_id: str = Form(...),
                  page_no: int = Form(1), paper: str = Form("a4"),
                  custom_w_mm: float = Form(210), custom_h_mm: float = Form(297),
                  orientation: str = Form("auto"), fit: str = Form("scale"),
                  align: str = Form("center"), keep_same: bool = Form(True)):
    """**真的處理那一頁再算圖**（與送出走同一段程式）。"""
    def _work():
        # 這裡算的是預覽圖（純 CPU）。直接跑在事件迴圈上會讓**全站**
        # 在這段時間都不回應 —— 頁數多或 DPI 高時特別明顯。包成閉包
        # 丟到執行緒之後，慢的只有按下去的那個人自己。
        require_uuid_hex(upload_id, "upload_id")
        _uo.require(upload_id, request)
        src = _src(upload_id)
        if not src.exists():
            raise HTTPException(404, "檔案不存在（可能已過期）")
        spec = _spec(paper=paper, custom_w_mm=custom_w_mm, custom_h_mm=custom_h_mm,
                     orientation=orientation, fit=fit, align=align,
                     keep_same=keep_same)
        with fitz.open(str(src)) as doc:
            if page_no < 1 or page_no > doc.page_count:
                raise HTTPException(400, "頁碼超出範圍")
            r = doc[page_no - 1].rect
            w, h = RC.target_size(spec, r)
            one = fitz.open()
            np = one.new_page(width=w, height=h)
            np.draw_rect(fitz.Rect(0, 0, w, h), color=(0.85, 0.85, 0.85), width=0.6)
            np.show_pdf_page(RC.content_rect(r, w, h, spec), doc, page_no - 1)
            pix = np.get_pixmap(dpi=80, alpha=False)
            one.close()
            return Response(content=pix.tobytes("png"), media_type="image/png",
                            headers={"Cache-Control": "no-store"})

    return await _asyncio.to_thread(_work)


@router.post("/submit")
async def submit(request: Request, upload_id: str = Form(...),
                 paper: str = Form("a4"), custom_w_mm: float = Form(210),
                 custom_h_mm: float = Form(297), orientation: str = Form("auto"),
                 fit: str = Form("scale"), align: str = Form("center"),
                 keep_same: bool = Form(True),
                 filename: str = Form("resized.pdf")):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    spec = _spec(paper=paper, custom_w_mm=custom_w_mm, custom_h_mm=custom_h_mm,
                 orientation=orientation, fit=fit, align=align,
                 keep_same=keep_same)
    out = _out(upload_id)
    stem = Path(filename).stem or "resized"

    def run(job):
        job.message = "統一頁面尺寸…"
        job.progress = 0.2
        rep = RC.resize(src, out, spec)
        job.result_path = out
        job.result_filename = f"{stem}_resized.pdf"
        # 摘要放 meta —— `to_public()` 只送 meta 出去
        job.meta = dict(job.meta or {}, resize_result={
            "total": rep.total, "changed": rep.changed,
            "skipped_same": rep.skipped_same, "rotated": rep.rotated,
            "warnings": rep.warnings[:20],
        })
        job.progress = 1.0
        job.message = (f"完成（調整 {rep.changed} 頁"
                       + (f"、{rep.skipped_same} 頁原本就對" if rep.skipped_same
                          else "") + "）")

    job = job_manager.submit("pdf-page-size", run, request=request,
                             meta={"filename": f"{stem}.pdf"})
    job.meta["view_url"] = f"/tools/pdf-page-size/?job={job.id}"   # 「我的作業」的「開啟」回到這一頁、接回結果
    return {"job_id": job.id}


@router.get("/download/{upload_id}")
async def download(upload_id: str, request: Request):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    out = _out(upload_id)
    if not out.exists():
        raise HTTPException(404, "還沒產生結果")
    return FileResponse(str(out), media_type="application/pdf",
                        filename=f"resized_{upload_id[:8]}.pdf")


@router.post("/api/pdf-page-size")
async def api(request: Request, file: UploadFile = File(...),
              paper: str = Form("a4"), custom_w_mm: float = Form(210),
              custom_h_mm: float = Form(297), orientation: str = Form("auto"),
              fit: str = Form("scale"), align: str = Form("center"),
              keep_same: bool = Form(True)):
    """單次呼叫：上傳 → 直接回統一尺寸後的 PDF。"""
    data = await file.read()
    if not data:
        raise HTTPException(400, "請提供檔案")
    uid = uuid.uuid4().hex
    src, out = _src(uid), _out(uid)
    try:
        if data[:5] == b"%PDF-":
            src.write_bytes(data)
        elif office_convert.is_office_file(file.filename or ""):
            raw = settings.temp_dir / f"{_PREFIX}raw_{uid}"
            raw.write_bytes(data)
            try:
                await office_convert.convert_to_pdf_async(raw, src)
            finally:
                raw.unlink(missing_ok=True)
        else:
            raise HTTPException(400, "只支援 PDF 與文書檔")
        # **公開 API 也要驗** —— 這是與網頁 `/load` 不同的另一段程式碼。
        ensure_readable_pdf(src)
        RC.resize(src, out, _spec(
            paper=paper, custom_w_mm=custom_w_mm, custom_h_mm=custom_h_mm,
            orientation=orientation, fit=fit, align=align, keep_same=keep_same))
        return FileResponse(str(out), media_type="application/pdf",
                            filename="resized.pdf")
    finally:
        src.unlink(missing_ok=True)
