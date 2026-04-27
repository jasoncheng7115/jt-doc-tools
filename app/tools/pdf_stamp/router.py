from __future__ import annotations

import asyncio
import json
import time
import uuid
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...config import settings
from ...core.asset_manager import asset_manager
from ...core.job_manager import job_manager
from ...core import pdf_preview
from . import service

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    # Accept any printable image asset (stamp / signature / logo). All three
    # render the same way as a transparent overlay, so users can pick any
    # of them from this tool — typical workflow is "stamp + signature on
    # the same form" without needing a separate signature tool.
    items = []
    for t in ("stamp", "signature", "logo"):
        items.extend(asset_manager.list(type=t))
    default = (
        asset_manager.get_default("stamp")
        or asset_manager.get_default("signature")
        or asset_manager.get_default("logo")
    )
    stamps_dict = [a.to_dict() for a in items]
    return templates.TemplateResponse(
        "pdf_stamp.html",
        {
            "request": request,
            "stamps": stamps_dict,
            "default_id": default.id if default else None,
        },
    )


@router.post("/submit")
async def submit(
    request: Request,
    stamp_id: str = Form(...),
    file: List[UploadFile] = File(...),
    override: Optional[str] = Form(None),
    page_mode: str = Form("all"),  # "all" | "first" | "last"
):
    """Stamp one or many PDFs. Single-file result → PDF; multi → ZIP."""
    asset = asset_manager.get(stamp_id)
    if not asset or asset.type not in ("stamp", "signature", "logo"):
        raise HTTPException(400, "stamp not found")

    files = file or []
    if not files:
        raise HTTPException(400, "沒有檔案")
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")

    # Save all uploads to temp now (the request stream can't be replayed
    # inside the background job).
    batch_id = uuid.uuid4().hex
    batch_dir = settings.temp_dir / f"stamp_batch_{batch_id}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []  # (src_path, orig_filename)
    for i, f in enumerate(files):
        data = await f.read()
        if not data:
            raise HTTPException(400, f"空檔：{f.filename}")
        safe = Path(f.filename).name or f"input_{i}.pdf"
        src_path = batch_dir / f"{i:03d}_{safe}"
        src_path.write_bytes(data)
        saved.append((src_path, safe))

    # Resolve placement params (shared by all files)
    p = asset.preset
    if override:
        try:
            ov = json.loads(override)
            p_x = float(ov.get("x_mm", p.x_mm))
            p_y = float(ov.get("y_mm", p.y_mm))
            p_w = float(ov.get("width_mm", p.width_mm))
            p_h = float(ov.get("height_mm", p.height_mm))
            p_rot = float(ov.get("rotation_deg", p.rotation_deg))
        except Exception:
            raise HTTPException(400, "override 格式錯誤")
    else:
        p_x, p_y, p_w, p_h = p.x_mm, p.y_mm, p.width_mm, p.height_mm
        p_rot = p.rotation_deg

    stamp_png = asset_manager.file_path(asset)

    def run(job):
        total = len(saved)
        stamped_paths: list[tuple[Path, str]] = []
        import fitz
        for i, (src_path, orig_name) in enumerate(saved):
            job.message = f"處理第 {i + 1}/{total} 份：{orig_name}"
            job.progress = (i / max(1, total)) * 0.95
            # Per-file page selection depends on that file's page count.
            pages: Optional[list[int]] = None
            if page_mode != "all":
                with fitz.open(str(src_path)) as doc:
                    n = doc.page_count
                pages = [0] if page_mode == "first" else [max(0, n - 1)]
            dst = batch_dir / f"{src_path.stem}_stamped.pdf"
            params = service.StampParams(
                x_mm=p_x, y_mm=p_y, width_mm=p_w, height_mm=p_h,
                rotation_deg=p_rot, pages=pages,
            )
            service.stamp(src_path, dst, stamp_png, params)
            stamped_paths.append((dst, _result_filename(orig_name)))

        if len(stamped_paths) == 1:
            result_path, result_name = stamped_paths[0]
        else:
            zip_name = f"stamped_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zip_path = batch_dir / zip_name
            # Disambiguate duplicate names by prefixing a sequence.
            used: dict[str, int] = {}
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for dst, name in stamped_paths:
                    k = used.get(name, 0) + 1
                    used[name] = k
                    arcname = name if k == 1 else f"{Path(name).stem}_{k}{Path(name).suffix}"
                    zf.write(dst, arcname=arcname)
            result_path = zip_path
            result_name = zip_name

        job.progress = 1.0
        job.message = f"完成（{total} 份）"
        job.result_path = result_path
        job.result_filename = result_name

        # ---- v1.1.0: archive into stamp_history ----
        # One entry per stamped source file (so admin / user can revisit).
        try:
            from ...core.history_manager import stamp_history
            for src_path, orig_name in saved:
                stem = Path(src_path).stem
                dst = batch_dir / f"{stem}_stamped.pdf"
                if dst.exists():
                    stamp_history.save(
                        original_path=src_path,
                        filled_path=dst,
                        preview_path=None,
                        original_filename=orig_name,
                        username=getattr(getattr(job, "_actor", None), "username", "") or "",
                        extra={"asset_id": stamp_id,
                               "x_mm": p_x, "y_mm": p_y,
                               "width_mm": p_w, "height_mm": p_h,
                               "rotation_deg": p_rot},
                    )
        except Exception:
            # History write is best-effort; never fail the user request.
            import logging as _lg
            _lg.getLogger(__name__).exception("stamp_history.save failed")

    job = job_manager.submit(
        "pdf-stamp", run,
        meta={"stamp_id": stamp_id, "count": len(saved)},
    )
    return {"job_id": job.id}


@router.get("/pdf-preview/{upload_id}")
async def tool_preview(upload_id: str):
    """Not currently used from the client; placeholder for future previewing uploaded files."""
    raise HTTPException(404, "not implemented")


@router.post("/preview")
async def preview(file: UploadFile = File(...)):
    """Render the first page of an uploaded PDF to PNG. Also returns per-page
    dimensions so the editor mode can offer page navigation (lazy-rendered via
    /preview-bg/{upload_id}/{page_idx})."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"{upload_id}.pdf"
    src.write_bytes(data)
    png = settings.temp_dir / f"{upload_id}_p1.png"
    await asyncio.to_thread(pdf_preview.render_page_png, src, png, 0, 110)

    import fitz
    from ...core.unit_convert import pt_to_mm
    with fitz.open(str(src)) as doc:
        page_count = doc.page_count
        pages_dims = [
            {"w_mm": round(pt_to_mm(doc[i].rect.width), 2),
             "h_mm": round(pt_to_mm(doc[i].rect.height), 2)}
            for i in range(page_count)
        ]

    return {
        "upload_id": upload_id,
        "preview_url": f"/tools/pdf-stamp/preview/{upload_id}_p1.png",
        "paper_w_mm": pages_dims[0]["w_mm"],
        "paper_h_mm": pages_dims[0]["h_mm"],
        "page_count": page_count,
        "pages_dims": pages_dims,
    }


@router.get("/preview-bg/{upload_id}/{page_idx}")
async def preview_bg(upload_id: str, page_idx: int):
    """Lazily render any page of a previously-uploaded PDF (from /preview)
    so the editor mode can switch its background between pages."""
    if not upload_id.replace("_", "").isalnum():
        raise HTTPException(400, "bad upload_id")
    src = settings.temp_dir / f"{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")
    if page_idx < 0:
        raise HTTPException(400, "bad page index")
    png = settings.temp_dir / f"{upload_id}_p{page_idx + 1}.png"
    if not png.exists():
        try:
            await asyncio.to_thread(pdf_preview.render_page_png, src, png, page_idx, 110)
        except IndexError:
            raise HTTPException(404, "page out of range")
    return {"preview_url": f"/tools/pdf-stamp/preview/{png.name}"}


@router.post("/preview-all-pages")
async def preview_all_pages(
    stamp_id: str = Form(...),
    file: UploadFile = File(...),
    override: Optional[str] = Form(None),
    page_mode: str = Form("all"),
):
    """Render every page of the uploaded PDF with the stamp applied at the
    given position; return one PNG URL per page so the UI can stack them."""
    asset = asset_manager.get(stamp_id)
    if not asset or asset.type not in ("stamp", "signature", "logo"):
        raise HTTPException(400, "stamp not found")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    stamped = settings.temp_dir / f"{upload_id}_stamped.pdf"
    src.write_bytes(data)

    p = asset.preset
    if override:
        try:
            ov = json.loads(override)
            p_x = float(ov.get("x_mm", p.x_mm))
            p_y = float(ov.get("y_mm", p.y_mm))
            p_w = float(ov.get("width_mm", p.width_mm))
            p_h = float(ov.get("height_mm", p.height_mm))
            p_rot = float(ov.get("rotation_deg", p.rotation_deg))
        except Exception:
            raise HTTPException(400, "override 格式錯誤")
    else:
        p_x, p_y, p_w, p_h = p.x_mm, p.y_mm, p.width_mm, p.height_mm
        p_rot = p.rotation_deg

    import fitz
    with fitz.open(str(src)) as doc:
        n = doc.page_count
    pages: Optional[list[int]] = None
    if page_mode == "first":
        pages = [0]
    elif page_mode == "last":
        pages = [max(0, n - 1)]

    from ...core import pdf_utils as pu
    pu.stamp_pdf(
        src_pdf=src, dst_pdf=stamped,
        stamp_png=asset_manager.file_path(asset),
        x_mm=p_x, y_mm=p_y, w_mm=p_w, h_mm=p_h,
        pages=pages, rotation_deg=p_rot,
    )

    # Render every page (or just the affected ones if a subset was picked) to
    # PNG so the front end can stack them.
    out_pages: list[dict] = []
    indices = pages if pages is not None else list(range(n))
    for i in range(n):
        png = settings.temp_dir / f"{upload_id}_p{i + 1}.png"
        pdf_preview.render_page_png(stamped, png, i, dpi=120)
        out_pages.append({
            "index": i,
            "stamped": i in indices,
            "preview_url": f"/tools/pdf-stamp/preview/{png.name}",
        })

    try:
        src.unlink(); stamped.unlink()
    except OSError:
        pass

    return {"page_count": n, "pages": out_pages}


@router.post("/preview-stamped")
async def preview_stamped(
    stamp_id: str = Form(...),
    file: UploadFile = File(...),
    override: Optional[str] = Form(None),
    page_mode: str = Form("all"),
):
    """Stamp the first applicable page of the PDF and return a PNG preview."""
    asset = asset_manager.get(stamp_id)
    if not asset or asset.type not in ("stamp", "signature", "logo"):
        raise HTTPException(400, "stamp not found")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    stamped = settings.temp_dir / f"{upload_id}_stamped.pdf"
    png = settings.temp_dir / f"{upload_id}_preview.png"
    src.write_bytes(data)

    # Resolve params (same rules as /submit)
    p = asset.preset
    if override:
        try:
            ov = json.loads(override)
            p_x = float(ov.get("x_mm", p.x_mm))
            p_y = float(ov.get("y_mm", p.y_mm))
            p_w = float(ov.get("width_mm", p.width_mm))
            p_h = float(ov.get("height_mm", p.height_mm))
            p_rot = float(ov.get("rotation_deg", p.rotation_deg))
        except Exception:
            raise HTTPException(400, "override 格式錯誤")
    else:
        p_x, p_y, p_w, p_h = p.x_mm, p.y_mm, p.width_mm, p.height_mm
        p_rot = p.rotation_deg

    # Pick the page to preview: first page that will receive a stamp
    import fitz
    with fitz.open(str(src)) as doc:
        n = doc.page_count
    if page_mode == "last":
        preview_page = max(0, n - 1)
        pages = [preview_page]
    elif page_mode == "first":
        preview_page = 0
        pages = [0]
    else:
        preview_page = 0
        pages = None

    from ...core import pdf_utils as pu
    pu.stamp_pdf(
        src_pdf=src,
        dst_pdf=stamped,
        stamp_png=asset_manager.file_path(asset),
        x_mm=p_x, y_mm=p_y, w_mm=p_w, h_mm=p_h,
        pages=pages, rotation_deg=p_rot,
    )
    pdf_preview.render_page_png(stamped, png, preview_page, dpi=120)

    # Clean up intermediates
    for fp in (src, stamped):
        try:
            fp.unlink()
        except OSError:
            pass

    return {
        "preview_url": f"/tools/pdf-stamp/preview/{png.name}",
        "preview_page": preview_page + 1,
        "page_count": n,
    }


@router.get("/preview/{name}")
async def serve_preview(name: str):
    from fastapi.responses import FileResponse
    p = settings.temp_dir / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type="image/png")


def _result_filename(orig: str) -> str:
    stem = Path(orig).stem
    return f"{stem}_stamped.pdf"
