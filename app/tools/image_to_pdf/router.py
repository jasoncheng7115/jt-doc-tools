"""Image → PDF tool.

Workflow:
  1. POST /tools/image-to-pdf/upload      → accept ONE image, return {file_id, thumb_url, w, h}
  2. GET  /tools/image-to-pdf/thumb/{fid} → serve thumbnail PNG
  3. GET  /tools/image-to-pdf/full/{fid}  → serve full-size preview (lightbox)
  4. POST /tools/image-to-pdf/generate    → accept ordered list of {file_id, rotation}
                                            + page size config → produce PDF, return job_id

We use one upload-per-image so the user can drag in more images at any time
without re-uploading the whole batch (matches pdf-pages「上傳一次後可再加」UX).
Per-image rotation is a CSS transform on the thumbnail and a PIL transpose at
PDF generation time, so the image bytes themselves are never modified on disk.
"""
from __future__ import annotations

import io
import re
import time
import uuid
from pathlib import Path
from typing import List, Optional

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from PIL import Image, ImageOps

from ...config import settings
from ...core.job_manager import job_manager

router = APIRouter()


# Strict regex to avoid path traversal via file_id in URL params.
_FID_RE = re.compile(r"^[a-f0-9]{32}$")
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".heif"}
_THUMB_MAX = 320  # px on the long edge
_FULL_MAX = 1600  # px on the long edge for lightbox preview


# ISO + common page sizes in **points** (1 pt = 1/72 in). PyMuPDF uses points.
PAGE_SIZES_PT = {
    # ISO A series
    "a3": (841.89, 1190.55),
    "a4": (595.28, 841.89),
    "a5": (419.53, 595.28),
    "a6": (297.64, 419.53),
    # US Letter / Legal
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
    "tabloid": (792.0, 1224.0),
    # Square / sticker
    "b5": (498.90, 708.66),
}


def _work_dir() -> Path:
    d = settings.temp_dir / "image_to_pdf"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _img_path(fid: str, original_ext: str = "") -> Path:
    """Path of the saved original image (we always write as PNG to normalise
    JPEG / GIF / TIFF / etc into a single render path)."""
    return _work_dir() / f"img_{fid}.png"


def _thumb_path(fid: str) -> Path:
    return _work_dir() / f"img_{fid}_thumb.png"


def _full_path(fid: str) -> Path:
    return _work_dir() / f"img_{fid}_full.png"


def _validate_fid(fid: str) -> str:
    if not _FID_RE.match(fid or ""):
        raise HTTPException(400, "invalid file id")
    return fid


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("image_to_pdf.html", {"request": request})


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    """Save one image, generate thumbnail + full preview, return metadata."""
    name = (file.filename or "").strip()
    ext = Path(name).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(400, f"不支援的圖片格式：{ext}")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空檔")
    # Cap upload size at 50 MB per image so a malicious / mistake upload
    # can't DoS the server. Adjust if customers complain.
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(413, "圖片過大（單檔上限 50 MB）")

    try:
        opened = Image.open(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(400, f"無法解析圖片：{e}")

    # 多頁處理 — TIFF / GIF / animated WEBP 等格式可能含多 frame，要逐頁拆。
    # 之前只開第一張，使用者反映 .tiff 內 3 張只轉出 1 張（v1.4.98 修）。
    n_frames = getattr(opened, "n_frames", 1) or 1
    items: list[dict] = []
    from ...core import upload_owner as _uo
    base_stem = Path(name).stem
    for i in range(n_frames):
        try:
            opened.seek(i)
        except (EOFError, OSError):
            break
        # Convert to RGB once per frame; .copy() before manipulation so
        # subsequent seek() on `opened` doesn't disturb our processing.
        img = opened.copy()
        # EXIF orientation only applies to first frame (multi-page TIFF
        # rarely has per-frame EXIF, but exif_transpose handles missing tags)
        if i == 0:
            img = ImageOps.exif_transpose(img)
        # Convert palette / CMYK / RGBA-with-alpha to RGB so PDF embed works
        if img.mode not in ("RGB", "L"):
            if img.mode == "RGBA":
                bg = Image.new("RGB", img.size, "white")
                bg.paste(img, mask=img.split()[3])
                img = bg
            else:
                img = img.convert("RGB")
        fid = uuid.uuid4().hex
        _uo.record(fid, request)
        img.save(str(_img_path(fid)), format="PNG", optimize=False)
        thumb = img.copy()
        thumb.thumbnail((_THUMB_MAX, _THUMB_MAX), Image.LANCZOS)
        thumb.save(str(_thumb_path(fid)), format="PNG", optimize=True)
        full = img.copy()
        full.thumbnail((_FULL_MAX, _FULL_MAX), Image.LANCZOS)
        full.save(str(_full_path(fid)), format="PNG", optimize=True)
        # 為多頁檔加 frame 編號到 filename，方便使用者在介面上分辨
        display_name = name if n_frames == 1 else f"{base_stem} ({i + 1}/{n_frames}){ext}"
        items.append({
            "file_id": fid,
            "filename": display_name,
            "width": img.width,
            "height": img.height,
            "thumb_url": f"/tools/image-to-pdf/thumb/{fid}",
            "full_url": f"/tools/image-to-pdf/full/{fid}",
        })
    if not items:
        raise HTTPException(400, "圖片無內容（無法讀出任何 frame）")
    # 單張：回單個 dict（向下相容舊客戶端）。多張：回 list。
    if len(items) == 1:
        return items[0]
    return items


@router.get("/thumb/{fid}")
async def thumb(fid: str):
    fid = _validate_fid(fid)
    p = _thumb_path(fid)
    if not p.exists():
        raise HTTPException(404, "thumbnail not found")
    return FileResponse(str(p), media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})


@router.get("/full/{fid}")
async def full(fid: str):
    fid = _validate_fid(fid)
    p = _full_path(fid)
    if not p.exists():
        raise HTTPException(404, "preview not found")
    return FileResponse(str(p), media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})


@router.post("/delete/{fid}")
async def delete_image(fid: str):
    """Best-effort cleanup of one upload. Frontend calls this when a card is
    removed so disk doesn't fill up — but generate() doesn't depend on it
    (the generate request just lists which file_ids to include)."""
    fid = _validate_fid(fid)
    for p in (_img_path(fid), _thumb_path(fid), _full_path(fid)):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True}


def _resolve_page_size(page_size: str, fit: str,
                       img_w_pt: float, img_h_pt: float) -> tuple[float, float]:
    """Return the (page_w_pt, page_h_pt) for one page given config + image size.

    page_size = 'auto' uses the image's own size (1 pt per CSS pixel-equivalent
    based on a 72 DPI assumption). Otherwise picks from PAGE_SIZES_PT and
    auto-orients (portrait vs landscape) to match the image aspect.
    """
    if page_size == "auto":
        return (img_w_pt, img_h_pt)
    base = PAGE_SIZES_PT.get(page_size.lower())
    if not base:
        return PAGE_SIZES_PT["a4"]  # safe default
    pw, ph = base
    # Auto-orient: if image is landscape, swap to landscape page (and v.v.)
    img_landscape = img_w_pt > img_h_pt
    page_landscape = pw > ph
    if img_landscape != page_landscape and fit != "fit-portrait":
        pw, ph = ph, pw
    return (pw, ph)


@router.post("/generate")
async def generate(request: Request):
    """Generate a single PDF from an ordered list of images.

    Body (JSON):
      {
        "items": [{"file_id": "...", "rotation": 0|90|180|270}, ...],
        "page_size": "auto"|"a4"|"a3"|"letter"|...,
        "margin_mm": 0|5|10|20,
        "filename": "output.pdf"
      }
    """
    body = await request.json()
    items = body.get("items") or []
    page_size = (body.get("page_size") or "auto").strip().lower()
    fit = (body.get("fit") or "fit").strip().lower()
    try:
        margin_mm = float(body.get("margin_mm", 0) or 0)
    except Exception:
        margin_mm = 0.0
    margin_mm = max(0.0, min(50.0, margin_mm))
    margin_pt = margin_mm * 72.0 / 25.4   # 1 mm = 72/25.4 pt
    bg_color = (body.get("bg") or "#ffffff").strip()  # for letterboxing
    filename = (body.get("filename") or "images.pdf").strip()
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    if not items:
        raise HTTPException(400, "沒有任何圖片")
    # Validate all file_ids upfront — reject the whole job rather than
    # producing a half-baked PDF.
    valid_items = []
    for it in items:
        fid = (it.get("file_id") or "").strip()
        if not _FID_RE.match(fid):
            raise HTTPException(400, f"非法 file_id：{fid}")
        if not _img_path(fid).exists():
            raise HTTPException(404, f"圖片已過期或不存在：{fid}")
        try:
            rot = int(it.get("rotation", 0)) % 360
        except Exception:
            rot = 0
        if rot not in (0, 90, 180, 270):
            rot = 0
        valid_items.append({"file_id": fid, "rotation": rot})

    # Parse bg color (#rrggbb) → tuple
    bg_rgb = (1.0, 1.0, 1.0)
    if bg_color.startswith("#") and len(bg_color) == 7:
        try:
            bg_rgb = (int(bg_color[1:3], 16) / 255.0,
                      int(bg_color[3:5], 16) / 255.0,
                      int(bg_color[5:7], 16) / 255.0)
        except Exception:
            pass

    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
    bdir = _work_dir() / f"job_{bid}"
    bdir.mkdir(parents=True, exist_ok=True)
    out_path = bdir / filename

    def run(job):
        doc = fitz.open()
        try:
            for idx, it in enumerate(valid_items):
                fid = it["file_id"]
                rot = it["rotation"]
                job.message = f"處理第 {idx + 1} / {len(valid_items)} 頁"
                job.progress = idx / max(len(valid_items), 1) * 0.95

                src = Image.open(str(_img_path(fid)))
                if rot:
                    src = src.rotate(-rot, expand=True)
                # Pretend 1 px = 1 pt for "auto" page size — produces a page
                # whose physical size matches the image's pixel size at 72 DPI.
                # (Higher DPI → smaller page; we keep simple.)
                img_w_pt = float(src.width)
                img_h_pt = float(src.height)
                page_w, page_h = _resolve_page_size(
                    page_size, fit, img_w_pt, img_h_pt
                )
                page = doc.new_page(width=page_w, height=page_h)

                # Fill background (only matters when image is letterboxed
                # against a fixed page size).
                if page_size != "auto":
                    page.draw_rect(
                        fitz.Rect(0, 0, page_w, page_h),
                        color=None, fill=bg_rgb, overlay=False,
                    )

                # Compute image rect inside the page (respecting margin).
                avail_w = max(page_w - 2 * margin_pt, 1.0)
                avail_h = max(page_h - 2 * margin_pt, 1.0)
                # Fit image into avail box (preserve aspect)
                scale = min(avail_w / img_w_pt, avail_h / img_h_pt)
                draw_w = img_w_pt * scale
                draw_h = img_h_pt * scale
                x0 = (page_w - draw_w) / 2
                y0 = (page_h - draw_h) / 2
                rect = fitz.Rect(x0, y0, x0 + draw_w, y0 + draw_h)

                # Encode rotated image to bytes for insert_image
                buf = io.BytesIO()
                # Save as JPEG when source is photographic for size; PNG for
                # screenshots / line art. Heuristic: count unique colors —
                # >2000 → photo; otherwise PNG. Cheap to estimate via thumbnail.
                small = src.copy()
                small.thumbnail((50, 50))
                colors = small.getcolors(maxcolors=2500)
                is_photo = colors is None or len(colors) >= 200
                if is_photo:
                    # JPEG quality 85 — visual lossless, big size win on photos
                    if src.mode != "RGB":
                        src = src.convert("RGB")
                    src.save(buf, format="JPEG", quality=85, optimize=True)
                else:
                    # Line art / screenshots stay PNG to preserve sharp edges
                    src.save(buf, format="PNG", optimize=True)
                buf.seek(0)
                page.insert_image(rect, stream=buf.read())
            doc.save(str(out_path), garbage=3, deflate=True)
        finally:
            doc.close()
        job.progress = 1.0
        job.message = f"完成（{len(valid_items)} 頁）"
        job.result_path = out_path
        job.result_filename = filename

    job = job_manager.submit("image-to-pdf", run, meta={"count": len(valid_items)})
    return {"job_id": job.id}


# ----- Public single-shot API for scripted use ------------------------------

@router.post("/api/image-to-pdf")
async def api_image_to_pdf(
    files: List[UploadFile] = File(..., description="image files in desired order"),
    page_size: str = Form("auto"),
    margin_mm: float = Form(0.0),
    rotations: str = Form("", description="comma-separated 0/90/180/270 per file (default 0 each)"),
    filename: str = Form("images.pdf"),
):
    """One-shot conversion endpoint — accept image files in form-data, return
    the generated PDF directly. For scripted / API token usage."""
    if not files:
        raise HTTPException(400, "沒有圖片")

    # Parse rotations list (default 0 per file)
    rot_list: list[int] = []
    if rotations.strip():
        for r in rotations.split(","):
            try:
                v = int(r.strip()) % 360
            except Exception:
                v = 0
            if v not in (0, 90, 180, 270):
                v = 0
            rot_list.append(v)
    while len(rot_list) < len(files):
        rot_list.append(0)

    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    bdir = _work_dir() / f"api_{uuid.uuid4().hex}"
    bdir.mkdir(parents=True, exist_ok=True)
    out_path = bdir / filename

    doc = fitz.open()
    try:
        for idx, f in enumerate(files):
            ext = Path(f.filename or "").suffix.lower()
            if ext not in _ALLOWED_EXT:
                raise HTTPException(400, f"不支援的圖片格式：{ext}")
            raw = await f.read()
            if not raw:
                raise HTTPException(400, f"空檔：{f.filename}")
            try:
                src = Image.open(io.BytesIO(raw))
                src = ImageOps.exif_transpose(src)
                if src.mode not in ("RGB", "L"):
                    if src.mode == "RGBA":
                        bg = Image.new("RGB", src.size, "white")
                        bg.paste(src, mask=src.split()[3])
                        src = bg
                    else:
                        src = src.convert("RGB")
            except Exception as e:
                raise HTTPException(400, f"無法解析圖片 {f.filename}：{e}")
            rot = rot_list[idx]
            if rot:
                src = src.rotate(-rot, expand=True)
            img_w_pt = float(src.width)
            img_h_pt = float(src.height)
            page_w, page_h = _resolve_page_size(page_size, "fit", img_w_pt, img_h_pt)
            margin_pt = max(0.0, min(50.0, margin_mm)) * 72.0 / 25.4
            page = doc.new_page(width=page_w, height=page_h)
            avail_w = max(page_w - 2 * margin_pt, 1.0)
            avail_h = max(page_h - 2 * margin_pt, 1.0)
            scale = min(avail_w / img_w_pt, avail_h / img_h_pt)
            draw_w = img_w_pt * scale
            draw_h = img_h_pt * scale
            x0 = (page_w - draw_w) / 2
            y0 = (page_h - draw_h) / 2
            rect = fitz.Rect(x0, y0, x0 + draw_w, y0 + draw_h)
            buf = io.BytesIO()
            if src.mode != "RGB":
                src = src.convert("RGB")
            src.save(buf, format="JPEG", quality=85, optimize=True)
            buf.seek(0)
            page.insert_image(rect, stream=buf.read())
        doc.save(str(out_path), garbage=3, deflate=True)
    finally:
        doc.close()

    from ...core.http_utils import content_disposition
    return FileResponse(
        str(out_path), media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(filename)},
    )
