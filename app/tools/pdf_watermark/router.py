from __future__ import annotations

import json
import time
import uuid
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core.asset_manager import asset_manager
from ...core.job_manager import job_manager
from ...core import pdf_preview
from . import service

router = APIRouter()


def _eligible_assets():
    # Only true 浮水印 assets — stamps / signatures / logos are excluded so
    # users don't accidentally print a stamp as a tiled watermark.
    return asset_manager.list(type="watermark")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    items = _eligible_assets()
    default = asset_manager.get_default("watermark") or (items[0] if items else None)
    return templates.TemplateResponse(
        "pdf_watermark.html",
        {
            "request": request,
            "assets": [a.to_dict() for a in items],
            "default_id": default.id if default else None,
        },
    )


def _parse_params(payload: str) -> service.WatermarkParams:
    try:
        d = json.loads(payload)
    except Exception:
        raise HTTPException(400, "params 格式錯誤")
    p = service.WatermarkParams(
        mode=str(d.get("mode") or "tile"),
        opacity=max(0.05, min(1.0, float(d.get("opacity", 0.25)))),
        rotation_deg=float(d.get("rotation_deg", 30.0)),
        x_mm=float(d.get("x_mm", 80.0)),
        y_mm=float(d.get("y_mm", 130.0)),
        width_mm=float(d.get("width_mm", 50.0)),
        height_mm=float(d.get("height_mm", 50.0)),
        tile_size_mm=float(d.get("tile_size_mm", d.get("tile_w_mm", 60.0))),
        tile_w_mm=float(d.get("tile_w_mm", 0.0)),
        tile_h_mm=float(d.get("tile_h_mm", 0.0)),
        gap_mm=max(0.0, float(d.get("gap_mm", 30.0))),
        text=str(d.get("text") or ""),
        text_color=str(d.get("text_color") or "#cc0000"),
        text_size_pt=float(d.get("text_size_pt", 48.0)),
        text_bold=bool(d.get("text_bold")),
        text_italic=bool(d.get("text_italic")),
        text_underline=bool(d.get("text_underline")),
    )
    if p.mode not in ("tile", "single"):
        p.mode = "tile"
    return p


@router.post("/preview")
async def preview(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"wm_{upload_id}.pdf"
    src.write_bytes(data)
    png = settings.temp_dir / f"wm_{upload_id}_p1.png"
    pdf_preview.render_page_png(src, png, 0, dpi=110)

    import fitz
    with fitz.open(str(src)) as doc:
        r = doc[0].rect
        from ...core.unit_convert import pt_to_mm
        w_mm = pt_to_mm(r.width); h_mm = pt_to_mm(r.height)
    return {
        "upload_id": upload_id,
        "preview_url": f"/tools/pdf-watermark/preview/{png.name}",
        "paper_w_mm": round(w_mm, 2),
        "paper_h_mm": round(h_mm, 2),
        "page_count": doc.page_count,
    }


@router.post("/preview-watermarked")
async def preview_watermarked(
    file: UploadFile = File(...),
    params: str = Form(...),
    asset_id: Optional[str] = Form(None),
):
    p = _parse_params(params)
    wm_path: Optional[Path] = None
    if not (p.text and p.text.strip()):
        if not asset_id:
            raise HTTPException(400, "需要 asset_id 或 text")
        asset = asset_manager.get(asset_id)
        if not asset:
            raise HTTPException(400, "asset not found")
        wm_path = asset_manager.file_path(asset)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"wm_{upload_id}_in.pdf"
    out = settings.temp_dir / f"wm_{upload_id}_marked.pdf"
    png = settings.temp_dir / f"wm_{upload_id}_preview.png"
    src.write_bytes(data)

    p.pages = [0]
    service.apply_watermark(src, out, wm_path, p)
    pdf_preview.render_page_png(out, png, 0, dpi=120)

    import fitz
    with fitz.open(str(src)) as doc:
        page_count = doc.page_count

    for f in (src, out):
        try: f.unlink()
        except OSError: pass

    return {
        "preview_url": f"/tools/pdf-watermark/preview/{png.name}",
        "page_count": page_count,
    }


@router.post("/submit")
async def submit(
    file: List[UploadFile] = File(...),
    params: str = Form(...),
    page_mode: str = Form("all"),
    asset_id: Optional[str] = Form(None),
):
    base_params = _parse_params(params)
    wm_png: Optional[Path] = None
    if not (base_params.text and base_params.text.strip()):
        if not asset_id:
            raise HTTPException(400, "需要 asset_id 或 text")
        asset = asset_manager.get(asset_id)
        if not asset:
            raise HTTPException(400, "asset not found")
        wm_png = asset_manager.file_path(asset)
    files = file or []
    if not files:
        raise HTTPException(400, "沒有檔案")
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")

    batch_id = uuid.uuid4().hex
    batch_dir = settings.temp_dir / f"wm_batch_{batch_id}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        data = await f.read()
        if not data:
            raise HTTPException(400, f"空檔：{f.filename}")
        safe = Path(f.filename).name or f"input_{i}.pdf"
        sp = batch_dir / f"{i:03d}_{safe}"
        sp.write_bytes(data)
        saved.append((sp, safe))

    def run(job):
        total = len(saved)
        results: list[tuple[Path, str]] = []
        import fitz
        for i, (sp, orig) in enumerate(saved):
            job.message = f"處理第 {i + 1}/{total} 份：{orig}"
            job.progress = (i / max(1, total)) * 0.95
            pages: Optional[list[int]] = None
            if page_mode != "all":
                with fitz.open(str(sp)) as d:
                    n = d.page_count
                pages = [0] if page_mode == "first" else [max(0, n - 1)]
            local = service.WatermarkParams(**{**base_params.__dict__, "pages": pages})
            dst = batch_dir / f"{sp.stem}_watermarked.pdf"
            service.apply_watermark(sp, dst, wm_png, local)
            results.append((dst, _result_filename(orig)))

        if len(results) == 1:
            result_path, result_name = results[0]
        else:
            zip_name = f"watermarked_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = batch_dir / zip_name
            used: dict[str, int] = {}
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for dst, name in results:
                    k = used.get(name, 0) + 1
                    used[name] = k
                    arc = name if k == 1 else f"{Path(name).stem}_{k}{Path(name).suffix}"
                    zf.write(dst, arcname=arc)
            result_path = zp; result_name = zip_name
        job.progress = 1.0
        job.message = f"完成（{total} 份）"
        job.result_path = result_path
        job.result_filename = result_name

        # ---- v1.1.0: archive into watermark_history ----
        try:
            from ...core.history_manager import watermark_history
            for sp, orig_name in saved:
                stem = Path(sp).stem
                dst = batch_dir / f"{stem}_watermarked.pdf"
                if dst.exists():
                    watermark_history.save(
                        original_path=sp,
                        filled_path=dst,
                        preview_path=None,
                        original_filename=orig_name,
                        username="",
                        extra={"asset_id": asset_id, "page_mode": page_mode},
                    )
        except Exception:
            import logging as _lg
            _lg.getLogger(__name__).exception("watermark_history.save failed")

    job = job_manager.submit(
        "pdf-watermark", run,
        meta={"asset_id": asset_id, "count": len(saved)},
    )
    return {"job_id": job.id}


@router.get("/preview/{name}")
async def serve_preview(name: str):
    p = settings.temp_dir / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type="image/png")


@router.get("/text-png")
async def text_png(
    text: str,
    color: str = "#cc0000",
    size: float = 48.0,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
):
    """Render the given text to a transparent PNG. Used by the position
    editor in single mode when source=text — the editor needs an image to
    display as the draggable element."""
    if not text.strip():
        raise HTTPException(400, "text required")
    from fastapi.responses import Response
    png_bytes, _w_mm, _h_mm = service._render_text_png(
        text, color, float(size), "",
        bold=bold, italic=italic, underline=underline,
    )
    return Response(
        content=png_bytes, media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


def _result_filename(orig: str) -> str:
    return f"{Path(orig).stem}_watermarked.pdf"
