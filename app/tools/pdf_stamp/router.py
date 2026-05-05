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


# 臨時資產 (#7, v1.3.16)
# 使用者可以在 pdf-stamp UI 「臨時上傳」一張圖，圖只放在瀏覽器 sessionStorage，
# 送出時才隨 request 上傳到 server，server 寫到 temp_dir 用一次就丟。
# 用 stamp_id == "__temp__" 作為哨兵 — 配合 multipart 內的 temp_asset_file。
_TEMP_STAMP_SENTINEL = "__temp__"
_TEMP_ASSET_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_TEMP_ASSET_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}


async def _resolve_stamp_source(
    stamp_id: str,
    temp_asset_file: Optional[UploadFile],
    request: Optional[Request] = None,
    actor_username: str = "",
) -> tuple[Path, dict]:
    """Return (stamp_image_path_on_disk, preset_dict_or_empty).

    - 一般 asset：查 asset_manager，回路徑 + asset.preset (轉 dict)
    - 臨時資產：把上傳檔案落地到 temp_dir，回路徑 + 空 preset（preset 由 client
      傳的 override 決定，所以 service 層只認 override 即可）

    任何錯誤情境一律 raise HTTPException(400)。臨時資產情境會寫一筆 audit。
    """
    if stamp_id != _TEMP_STAMP_SENTINEL:
        asset = asset_manager.get(stamp_id)
        if not asset or asset.type not in ("stamp", "signature", "logo"):
            raise HTTPException(400, "stamp not found")
        # asset.preset is a PositionPreset dataclass; service layer reads via
        # PositionPreset / dict transparently. Caller treats this as opaque.
        return asset_manager.file_path(asset), {
            "x_mm": asset.preset.x_mm, "y_mm": asset.preset.y_mm,
            "width_mm": asset.preset.width_mm, "height_mm": asset.preset.height_mm,
            "rotation_deg": asset.preset.rotation_deg,
            "paper_w_mm": asset.preset.paper_w_mm,
            "paper_h_mm": asset.preset.paper_h_mm,
        }
    if not temp_asset_file:
        raise HTTPException(400, "temp asset selected but no file uploaded")
    # validate filename + size
    fname = (temp_asset_file.filename or "").strip()
    ext = Path(fname).suffix.lower()
    if ext and ext not in _TEMP_ASSET_ALLOWED_EXT:
        raise HTTPException(400, f"unsupported temp asset extension: {ext}")
    data = await temp_asset_file.read()
    if not data:
        raise HTTPException(400, "empty temp asset")
    if len(data) > _TEMP_ASSET_MAX_BYTES:
        raise HTTPException(
            400, f"temp asset too large: {len(data)/1024/1024:.1f} MB > 5 MB")
    # validate it's actually an image (not just an .png-named .exe)
    try:
        from PIL import Image as _PILImage
        from io import BytesIO as _BytesIO
        with _PILImage.open(_BytesIO(data)) as im:
            im.verify()
    except Exception as e:
        raise HTTPException(400, f"temp asset is not a valid image: {e}")
    # Save to temp_dir under a unique name (this stamp_dir gets garbage
    # collected by the 2-hour temp cleanup task; not stored as a real asset)
    out = settings.temp_dir / f"stamp_temp_{uuid.uuid4().hex}{ext or '.png'}"
    out.write_bytes(data)
    # Audit (best-effort; never fail the user request because audit failed)
    try:
        from ...core import audit_db as _audit
        import hashlib as _hl
        ip = ""
        if request is not None:
            ip = (request.client.host if request.client else "") or ""
        _audit.log_event(
            event_type="temp_asset_used",
            username=actor_username or "",
            ip=ip,
            target="pdf-stamp",
            details={
                "filename": fname or "(unnamed)",
                "size_bytes": len(data),
                "sha256_8": _hl.sha256(data).hexdigest()[:16],
                "tool": "pdf-stamp",
            },
        )
    except Exception:
        import logging as _lg
        _lg.getLogger(__name__).debug("temp_asset_used audit write failed",
                                     exc_info=True)
    return out, {}


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
    temp_asset_file: Optional[UploadFile] = File(None),
):
    """Stamp one or many PDFs. Single-file result → PDF; multi → ZIP."""
    actor = getattr(getattr(request.state, "user", None), "username", "") or ""
    stamp_png, preset_dict = await _resolve_stamp_source(
        stamp_id, temp_asset_file, request=request, actor_username=actor)
    asset = asset_manager.get(stamp_id) if stamp_id != _TEMP_STAMP_SENTINEL else None

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

    # Resolve placement params (shared by all files). preset_dict comes
    # from _resolve_stamp_source — empty dict for temp assets means
    # override IS the only source (UI sends a default sensible preset).
    if override:
        try:
            ov = json.loads(override)
            p_x = float(ov.get("x_mm", preset_dict.get("x_mm", 105)))
            p_y = float(ov.get("y_mm", preset_dict.get("y_mm", 250)))
            p_w = float(ov.get("width_mm", preset_dict.get("width_mm", 30)))
            p_h = float(ov.get("height_mm", preset_dict.get("height_mm", 30)))
            p_rot = float(ov.get("rotation_deg", preset_dict.get("rotation_deg", 0)))
        except Exception:
            raise HTTPException(400, "override 格式錯誤")
    elif preset_dict:
        p_x = preset_dict["x_mm"]; p_y = preset_dict["y_mm"]
        p_w = preset_dict["width_mm"]; p_h = preset_dict["height_mm"]
        p_rot = preset_dict["rotation_deg"]
    else:
        # Temp asset, no override — use sensible default (centered low)
        p_x, p_y, p_w, p_h, p_rot = 105.0, 250.0, 30.0, 30.0, 0.0

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
                        username=actor or "",
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
    request: Request,
    stamp_id: str = Form(...),
    file: UploadFile = File(...),
    override: Optional[str] = Form(None),
    page_mode: str = Form("all"),
    temp_asset_file: Optional[UploadFile] = File(None),
):
    """Render every page of the uploaded PDF with the stamp applied at the
    given position; return one PNG URL per page so the UI can stack them."""
    actor = getattr(getattr(request.state, "user", None), "username", "") or ""
    stamp_png_path, preset_dict = await _resolve_stamp_source(
        stamp_id, temp_asset_file, request=request, actor_username=actor)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    stamped = settings.temp_dir / f"{upload_id}_stamped.pdf"
    src.write_bytes(data)

    if override:
        try:
            ov = json.loads(override)
            p_x = float(ov.get("x_mm", preset_dict.get("x_mm", 105)))
            p_y = float(ov.get("y_mm", preset_dict.get("y_mm", 250)))
            p_w = float(ov.get("width_mm", preset_dict.get("width_mm", 30)))
            p_h = float(ov.get("height_mm", preset_dict.get("height_mm", 30)))
            p_rot = float(ov.get("rotation_deg", preset_dict.get("rotation_deg", 0)))
        except Exception:
            raise HTTPException(400, "override 格式錯誤")
    elif preset_dict:
        p_x = preset_dict["x_mm"]; p_y = preset_dict["y_mm"]
        p_w = preset_dict["width_mm"]; p_h = preset_dict["height_mm"]
        p_rot = preset_dict["rotation_deg"]
    else:
        p_x, p_y, p_w, p_h, p_rot = 105.0, 250.0, 30.0, 30.0, 0.0

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
        stamp_png=stamp_png_path,
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
    request: Request,
    stamp_id: str = Form(...),
    file: UploadFile = File(...),
    override: Optional[str] = Form(None),
    page_mode: str = Form("all"),
    temp_asset_file: Optional[UploadFile] = File(None),
):
    """Stamp the first applicable page of the PDF and return a PNG preview."""
    actor = getattr(getattr(request.state, "user", None), "username", "") or ""
    stamp_png_path, preset_dict = await _resolve_stamp_source(
        stamp_id, temp_asset_file, request=request, actor_username=actor)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    stamped = settings.temp_dir / f"{upload_id}_stamped.pdf"
    png = settings.temp_dir / f"{upload_id}_preview.png"
    src.write_bytes(data)

    # Resolve params (same rules as /submit)
    if override:
        try:
            ov = json.loads(override)
            p_x = float(ov.get("x_mm", preset_dict.get("x_mm", 105)))
            p_y = float(ov.get("y_mm", preset_dict.get("y_mm", 250)))
            p_w = float(ov.get("width_mm", preset_dict.get("width_mm", 30)))
            p_h = float(ov.get("height_mm", preset_dict.get("height_mm", 30)))
            p_rot = float(ov.get("rotation_deg", preset_dict.get("rotation_deg", 0)))
        except Exception:
            raise HTTPException(400, "override 格式錯誤")
    elif preset_dict:
        p_x = preset_dict["x_mm"]; p_y = preset_dict["y_mm"]
        p_w = preset_dict["width_mm"]; p_h = preset_dict["height_mm"]
        p_rot = preset_dict["rotation_deg"]
    else:
        p_x, p_y, p_w, p_h, p_rot = 105.0, 250.0, 30.0, 30.0, 0.0

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
        stamp_png=stamp_png_path,
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
