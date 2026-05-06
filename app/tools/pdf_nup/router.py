"""N-up imposition using PyMuPDF.

Each upload is UUID-keyed under ``settings.temp_dir``; the sweeper cleans
stale files after the configured TTL. Multi-user safe — different users
get different UUIDs.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ...config import settings
from ...core import office_convert


router = APIRouter()


# --------------------------------------------------------------------- helpers

PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "a4": (210.0, 297.0),
    "a3": (297.0, 420.0),
    "a5": (148.0, 210.0),
    "letter": (215.9, 279.4),
    "legal": (215.9, 355.6),
    "b4": (250.0, 353.0),
    "b5": (176.0, 250.0),
}


def _mm_to_pt(mm: float) -> float:
    return float(mm) * 72.0 / 25.4


def _src_path(upload_id: str, idx: int) -> Path:
    return settings.temp_dir / f"nup_{upload_id}_{idx}.pdf"


def _out_path(upload_id: str) -> Path:
    return settings.temp_dir / f"nup_{upload_id}_out.pdf"


def _preview_path(upload_id: str) -> Path:
    return settings.temp_dir / f"nup_{upload_id}_preview.png"


async def _load_upload_as_pdf(file: UploadFile, upload_id: str, idx: int) -> Path:
    """Save an UploadFile to temp dir; if it's Office/ODF, convert to PDF
    via LibreOffice/OxOffice first."""
    data = await file.read()
    if not data:
        raise HTTPException(400, f"empty file: {file.filename}")
    target = _src_path(upload_id, idx)
    ext = Path(file.filename or "x.pdf").suffix.lower()
    if ext == ".pdf":
        target.write_bytes(data)
    elif office_convert.is_office_file(file.filename or ""):
        tmp = settings.temp_dir / f"nup_{upload_id}_{idx}_orig{ext}"
        tmp.write_bytes(data)
        try:
            office_convert.convert_to_pdf(tmp, target, timeout=120.0)
        except RuntimeError as e:
            raise HTTPException(500, f"convert failed: {e}")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    else:
        raise HTTPException(400, f"unsupported file type: {ext}")
    return target


# --------------------------------------------------------------------- models

class NupOptions(BaseModel):
    upload_id: str
    cols: int = 2
    rows: int = 2
    paper: str = "a4"            # key of PAPER_SIZES_MM or "custom"
    paper_w_mm: float = 210.0    # only used when paper == "custom"
    paper_h_mm: float = 297.0
    orientation: str = "auto"    # auto / portrait / landscape
    margin_top_mm: float = 10.0
    margin_right_mm: float = 10.0
    margin_bottom_mm: float = 10.0
    margin_left_mm: float = 10.0
    gap_x_mm: float = 5.0
    gap_y_mm: float = 5.0
    direction: str = "ltr"       # ltr (row-major, left→right then top→bottom)
                                 # or ttb (column-major, top→bottom then left→right)
    reverse: bool = False
    auto_rotate: bool = True     # rotate source pages to best fit each cell
    show_border: bool = False
    border_width_pt: float = 0.5
    border_color: str = "#94a3b8"
    show_page_num: bool = False
    page_num_format: str = "{n}"  # {n} = original 1-based page number, {t} = total
    page_num_size_pt: float = 8.0
    pad_blanks: bool = True      # fill last sheet with blanks
    show_crop_marks: bool = False
    crop_mark_len_mm: float = 4.0
    page_align: str = "center"   # center / left-top (within cell)
    # Input file count retained server-side via upload_id; see /load.
    file_count: int = 1


def _hex_to_rgb01(hx: str) -> tuple[float, float, float]:
    h = (hx or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (0.4, 0.45, 0.55)
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return (r, g, b)


def _collect_src_pages(upload_id: str, file_count: int) -> list[tuple[fitz.Document, int]]:
    """Open all uploaded source PDFs for this session and return a flat
    list of (doc, page_index) tuples in upload order. Callers must close
    the docs."""
    docs: list[fitz.Document] = []
    pages: list[tuple[fitz.Document, int]] = []
    for i in range(file_count):
        p = _src_path(upload_id, i)
        if not p.exists():
            continue
        doc = fitz.open(str(p))
        docs.append(doc)
        for pg in range(doc.page_count):
            pages.append((doc, pg))
    return pages


def _paper_dims_pt(opts: NupOptions) -> tuple[float, float]:
    key = (opts.paper or "a4").lower()
    if key == "custom":
        w_mm, h_mm = float(opts.paper_w_mm), float(opts.paper_h_mm)
    else:
        w_mm, h_mm = PAPER_SIZES_MM.get(key, PAPER_SIZES_MM["a4"])
    w_pt, h_pt = _mm_to_pt(w_mm), _mm_to_pt(h_mm)
    orient = (opts.orientation or "auto").lower()
    if orient == "landscape" and w_pt < h_pt:
        w_pt, h_pt = h_pt, w_pt
    elif orient == "portrait" and w_pt > h_pt:
        w_pt, h_pt = h_pt, w_pt
    elif orient == "auto":
        # Auto: if cols > rows, assume landscape; if rows > cols, portrait.
        if opts.cols > opts.rows and w_pt < h_pt:
            w_pt, h_pt = h_pt, w_pt
        elif opts.rows > opts.cols and w_pt > h_pt:
            w_pt, h_pt = h_pt, w_pt
    return w_pt, h_pt


def _cell_rect(col: int, row: int, opts: NupOptions,
               paper_w: float, paper_h: float) -> fitz.Rect:
    ml = _mm_to_pt(opts.margin_left_mm)
    mr = _mm_to_pt(opts.margin_right_mm)
    mt = _mm_to_pt(opts.margin_top_mm)
    mb = _mm_to_pt(opts.margin_bottom_mm)
    gx = _mm_to_pt(opts.gap_x_mm)
    gy = _mm_to_pt(opts.gap_y_mm)
    avail_w = paper_w - ml - mr - gx * (opts.cols - 1)
    avail_h = paper_h - mt - mb - gy * (opts.rows - 1)
    cw = avail_w / max(1, opts.cols)
    ch = avail_h / max(1, opts.rows)
    x0 = ml + col * (cw + gx)
    y0 = mt + row * (ch + gy)
    return fitz.Rect(x0, y0, x0 + cw, y0 + ch)


def _fit_rect_for_page(cell: fitz.Rect, src_w: float, src_h: float,
                       auto_rotate: bool, align: str) -> tuple[fitz.Rect, int]:
    """Compute target rect that preserves aspect and optionally rotates
    the src page by 90° when its orientation mismatches the cell."""
    cw, ch = cell.width, cell.height
    rot = 0
    if auto_rotate and (src_w > src_h) != (cw > ch):
        # Rotate src by 90 to better match cell aspect
        src_w, src_h = src_h, src_w
        rot = 90
    # Scale to fit
    scale = min(cw / max(1, src_w), ch / max(1, src_h))
    draw_w = src_w * scale
    draw_h = src_h * scale
    if align == "left-top":
        x0 = cell.x0
        y0 = cell.y0
    else:  # center
        x0 = cell.x0 + (cw - draw_w) / 2
        y0 = cell.y0 + (ch - draw_h) / 2
    return fitz.Rect(x0, y0, x0 + draw_w, y0 + draw_h), rot


def _draw_crop_marks(page: fitz.Page, paper_w: float, paper_h: float, mm: float):
    """Add crop marks (registration marks) at the 4 corners."""
    ln = _mm_to_pt(mm)
    off = _mm_to_pt(3.0)
    black = (0, 0, 0)
    width = 0.5
    corners = [
        (0, 0),
        (paper_w, 0),
        (0, paper_h),
        (paper_w, paper_h),
    ]
    for x, y in corners:
        sx = -1 if x > paper_w / 2 else 1
        sy = -1 if y > paper_h / 2 else 1
        # Horizontal tick
        page.draw_line(fitz.Point(x + sx * off, y),
                       fitz.Point(x + sx * (off + ln), y),
                       color=black, width=width)
        # Vertical tick
        page.draw_line(fitz.Point(x, y + sy * off),
                       fitz.Point(x, y + sy * (off + ln)),
                       color=black, width=width)


def impose(upload_id: str, opts: NupOptions, *, preview_only: bool = False) -> Path:
    """Run the imposition. When ``preview_only`` is true, only the first
    output sheet is rendered to PNG and that path is returned; otherwise
    the full PDF path is returned."""
    paper_w, paper_h = _paper_dims_pt(opts)
    pages = _collect_src_pages(upload_id, opts.file_count)
    if not pages:
        raise HTTPException(400, "no source pages (did you upload?)")
    if opts.reverse:
        pages = list(reversed(pages))
    n_per_sheet = max(1, opts.cols * opts.rows)
    # Pad with Nones so last sheet is filled
    pad = (-len(pages)) % n_per_sheet
    if opts.pad_blanks and pad:
        pages = pages + [None] * pad  # type: ignore[list-item]

    border_rgb = _hex_to_rgb01(opts.border_color) if opts.show_border else None

    out_doc = fitz.open()
    total_sheets = max(1, (len(pages) + n_per_sheet - 1) // n_per_sheet)
    sheets_to_render = 1 if preview_only else total_sheets

    for sheet_idx in range(sheets_to_render):
        page = out_doc.new_page(width=paper_w, height=paper_h)
        chunk = pages[sheet_idx * n_per_sheet:(sheet_idx + 1) * n_per_sheet]
        for slot, entry in enumerate(chunk):
            # Map slot → (col, row) based on direction
            if opts.direction == "ttb":
                col, row = slot // opts.rows, slot % opts.rows
            else:
                col, row = slot % opts.cols, slot // opts.cols
            cell = _cell_rect(col, row, opts, paper_w, paper_h)
            if entry is None:
                # Blank slot — optionally draw light border to hint
                if opts.show_border and border_rgb:
                    page.draw_rect(cell, color=border_rgb,
                                   width=opts.border_width_pt, dashes="[2 2]")
                continue
            src_doc, src_idx = entry
            src_page = src_doc[src_idx]
            sw, sh = src_page.rect.width, src_page.rect.height
            target, rot = _fit_rect_for_page(cell, sw, sh, opts.auto_rotate, opts.page_align)
            try:
                page.show_pdf_page(target, src_doc, src_idx, rotate=rot)
            except Exception:
                # Some PDFs fail show_pdf_page; render to pixmap fallback
                mat = fitz.Matrix(150 / 72.0, 150 / 72.0)
                pix = src_page.get_pixmap(matrix=mat)
                page.insert_image(target, pixmap=pix)
            if opts.show_border and border_rgb:
                page.draw_rect(target, color=border_rgb, width=opts.border_width_pt)
            if opts.show_page_num:
                txt = (opts.page_num_format or "{n}").replace(
                    "{n}", str(src_idx + 1)).replace("{t}", str(src_doc.page_count))
                fs = max(6.0, float(opts.page_num_size_pt))
                # Put below cell, small gray
                tp = fitz.Point(target.x0 + 2, target.y1 + fs + 1)
                if tp.y > paper_h - 2:
                    tp = fitz.Point(target.x0 + 2, target.y1 - 2)
                try:
                    page.insert_text(tp, txt, fontname="helv", fontsize=fs,
                                     color=(0.4, 0.4, 0.4))
                except Exception:
                    pass
        if opts.show_crop_marks:
            _draw_crop_marks(page, paper_w, paper_h, opts.crop_mark_len_mm)

    # Close src docs
    seen: set[int] = set()
    for entry in pages:
        if not entry:
            continue
        d, _ = entry
        if id(d) not in seen:
            seen.add(id(d))
            try:
                d.close()
            except Exception:
                pass

    if preview_only:
        # Render first sheet to PNG
        out_path = _preview_path(upload_id)
        try:
            pix = out_doc[0].get_pixmap(matrix=fitz.Matrix(120 / 72.0, 120 / 72.0))
            pix.save(str(out_path))
        finally:
            out_doc.close()
        return out_path

    out_path = _out_path(upload_id)
    out_doc.save(str(out_path), garbage=3, deflate=True)
    out_doc.close()
    return out_path


# --------------------------------------------------------------------- routes

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    paper_opts = [{"key": k, "label": k.upper()} for k in PAPER_SIZES_MM.keys()]
    return templates.TemplateResponse(
        "pdf_nup.html", {"request": request, "paper_opts": paper_opts},
    )


@router.post("/load")
async def load(request: Request, files: list[UploadFile] = File(...)):
    """Accept one or more PDF/Office files; store them and return a total
    page count + per-file counts so the UI can preview."""
    if not files:
        raise HTTPException(400, "no files")
    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
    per_file: list[dict] = []
    total_pages = 0
    for idx, f in enumerate(files):
        p = await _load_upload_as_pdf(f, upload_id, idx)
        try:
            with fitz.open(str(p)) as doc:
                pages = doc.page_count
        except Exception as e:
            raise HTTPException(400, f"cannot open {f.filename}: {e}")
        per_file.append({
            "name": f.filename or f"file{idx}.pdf",
            "pages": pages,
        })
        total_pages += pages
    return {
        "upload_id": upload_id,
        "file_count": len(files),
        "files": per_file,
        "total_pages": total_pages,
    }


@router.post("/preview")
async def preview(opts: NupOptions):
    if not opts.upload_id:
        raise HTTPException(400, "missing upload_id")
    import asyncio as _asyncio
    p = await _asyncio.to_thread(impose, opts.upload_id, opts, preview_only=True)
    return FileResponse(str(p), media_type="image/png",
                        headers={"Cache-Control": "no-store"})


@router.post("/generate")
async def generate(opts: NupOptions):
    if not opts.upload_id:
        raise HTTPException(400, "missing upload_id")
    import asyncio as _asyncio
    p = await _asyncio.to_thread(impose, opts.upload_id, opts, preview_only=False)
    return {"ok": True, "url": f"/tools/pdf-nup/download/{opts.upload_id}"}


@router.get("/download/{upload_id}")
async def download(upload_id: str, request: Request):
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner
    require_uuid_hex(upload_id, "upload_id")
    upload_owner.require(upload_id, request)
    p = _out_path(upload_id)
    if not p.exists():
        raise HTTPException(404, "not generated yet")
    return FileResponse(str(p), media_type="application/pdf",
                        filename=f"nup_{upload_id[:8]}.pdf")
