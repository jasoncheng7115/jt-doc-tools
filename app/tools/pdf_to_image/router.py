"""PDF → Image endpoints."""
from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core import pdf_preview
from ...core import office_convert


router = APIRouter()


def _work_dir() -> Path:
    return settings.temp_dir


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_to_image.html", {"request": request})


@router.post("/convert")
async def convert(
    request: Request,
    file: UploadFile = File(...),
    dpi: int = Form(200),
):
    """Convert PDF / Office doc to per-page PNG.

    `dpi` controls render resolution (and therefore file size + clarity):
        72   = screen draft, ~30-100 KB/page
        150  = readable on screen, ~150-400 KB/page
        200  = default (good for screen + light print)
        300  = print quality, ~600 KB-2 MB/page
        400  = high-DPI print, can be very large
    Clamped to [72, 600] to avoid runaway memory.
    """
    try:
        dpi = int(dpi)
    except (TypeError, ValueError):
        dpi = 200
    dpi = max(72, min(600, dpi))
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    orig_name = file.filename or "document"
    # Surface filename to the audit middleware (logged on response).
    request.state.upload_filename = orig_name
    ext = Path(orig_name).suffix.lower()
    is_pdf = ext == ".pdf"
    is_office = office_convert.is_office_file(orig_name)
    if not (is_pdf or is_office):
        raise HTTPException(
            400,
            f"不支援的檔案格式：{ext or '未知'}；支援 PDF 與 Office 檔（.docx/.xlsx/.pptx/.odt/.ods/.odp/.doc/.xls/.ppt/.rtf/.txt/.csv）",
        )

    upload_id = uuid.uuid4().hex
    work = _work_dir()
    work.mkdir(parents=True, exist_ok=True)

    # Heavy lifting (soffice convert + PyMuPDF page render loop) is sync and
    # blocks the asyncio event loop if run inline — same trap as v1.1.29
    # fixed in pdf-extract-text. Push to thread pool so the rest of the
    # site stays responsive while a big file converts.
    import asyncio as _asyncio

    def _do_convert():
        if is_pdf:
            src_p = work / f"p2i_{upload_id}_in.pdf"
            src_p.write_bytes(data)
        else:
            office_src = work / f"p2i_{upload_id}_in{ext}"
            office_src.write_bytes(data)
            src_p = work / f"p2i_{upload_id}_in.pdf"
            try:
                office_convert.convert_to_pdf(office_src, src_p, timeout=120.0)
            except RuntimeError:
                raise HTTPException(
                    500,
                    "找不到 Office 轉檔引擎（OxOffice / LibreOffice）。請到「轉檔設定」確認安裝路徑。",
                )
            except Exception as e:
                raise HTTPException(500, f"轉檔失敗：{e}")
            if not src_p.exists():
                raise HTTPException(500, "轉檔未產生 PDF。")
        try:
            (work / f"p2i_{upload_id}_name.txt").write_text(orig_name, encoding="utf-8")
        except Exception:
            pass
        pages_local = []
        with fitz.open(str(src_p)) as doc:
            for i in range(doc.page_count):
                out_png = work / f"p2i_{upload_id}_p{i+1}.png"
                w, h = pdf_preview.render_page_png(src_p, out_png, i, dpi=dpi)
                pages_local.append({
                    "index": i,
                    "width_px": w,
                    "height_px": h,
                    "size_bytes": out_png.stat().st_size,
                    "preview_url": f"/tools/pdf-to-image/preview/{out_png.name}",
                })
        return pages_local

    pages_info = await _asyncio.to_thread(_do_convert)
    total_bytes = sum(p["size_bytes"] for p in pages_info)

    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "page_count": len(pages_info),
        "dpi": dpi,
        "total_bytes": total_bytes,
        "pages": pages_info,
    }


@router.get("/preview/{filename}")
async def preview(filename: str):
    if not filename.startswith("p2i_") or ".." in filename or "/" in filename:
        raise HTTPException(400, "invalid filename")
    path = _work_dir() / filename
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(str(path), media_type="image/png")


@router.get("/download/{upload_id}")
async def download(upload_id: str):
    work = _work_dir()
    # Recover original filename
    orig = "document.pdf"
    name_file = work / f"p2i_{upload_id}_name.txt"
    try:
        if name_file.exists():
            orig = name_file.read_text(encoding="utf-8").strip() or orig
    except Exception:
        pass
    base = orig.rsplit(".", 1)[0]

    # Find all rendered page PNGs
    pages = sorted(work.glob(f"p2i_{upload_id}_p*.png"))
    if not pages:
        raise HTTPException(404, "沒有產生的圖片，請重新上傳")

    if len(pages) == 1:
        # Single page → direct PNG download
        return FileResponse(
            str(pages[0]), media_type="image/png",
            filename=f"{base}.png",
        )

    # Multi-page → ZIP bundle
    zip_path = work / f"p2i_{upload_id}.zip"
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as z:
        for i, p in enumerate(pages, start=1):
            z.write(p, arcname=f"{base}_p{i}.png")
    return FileResponse(
        str(zip_path), media_type="application/zip",
        filename=f"{base}.zip",
    )
