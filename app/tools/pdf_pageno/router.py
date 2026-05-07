from __future__ import annotations
import time
import uuid
import zipfile
from pathlib import Path
from typing import List
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
import fitz
from ...config import settings
from ...core.job_manager import job_manager
from ...core import pdf_preview

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_pageno.html", {"request": request})


@router.post("/load")
async def load(request: Request, file: UploadFile = File(...)):
    """Stash the upload + return page count and thumbnail URLs (single file)."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data: raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
    src = settings.temp_dir / f"pnL_{upload_id}.pdf"
    src.write_bytes(data)
    with fitz.open(str(src)) as doc:
        n = doc.page_count
    return {
        "upload_id": upload_id, "filename": file.filename, "page_count": n,
        "pages": [
            {"page": i + 1, "thumb": f"/tools/pdf-pageno/thumb/{upload_id}/{i + 1}"}
            for i in range(n)
        ],
    }


@router.get("/thumb/{upload_id}/{page}")
async def thumb(upload_id: str, page: int, large: bool = False):
    src = settings.temp_dir / f"pnL_{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(404, "upload not found (expired?)")
    suffix = "_large" if large else ""
    out = settings.temp_dir / f"pnL_{upload_id}_thumb{suffix}_{page}.png"
    if not out.exists():
        pdf_preview.render_page_png(src, out, page - 1, dpi=160 if large else 64)
    return FileResponse(str(out), media_type="image/png",
                        headers={"Cache-Control": "max-age=300"})


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    h = (hex_color or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return (int(h[0:2], 16) / 255.0,
                int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0)
    except Exception:
        return (0.0, 0.0, 0.0)


def _draw_pageno(
    page, *, page_index: int, total: int,
    position: str, fmt: str, start: int,
    font_size: float, margin_mm: float, color_hex: str,
    from_page: int = 1, to_page: int | None = None,
) -> None:
    """Draw a page number on the given page. If the 1-based page number is
    outside [from_page, to_page], no-op (covers/TOC/back cover can be
    skipped this way).

    ``start`` is the number to print on the first *numbered* page; later
    pages increment from there. ``{N}`` in the format template reflects
    the count of *numbered* pages (to_page - from_page + 1), not the
    total page count of the PDF.
    """
    from ...core.unit_convert import mm_to_pt
    page_no = page_index + 1                 # 1-based index on paper
    if to_page is None:
        to_page = total
    if page_no < from_page or page_no > to_page:
        return                                # outside the numbering range
    numbered_total = max(1, to_page - from_page + 1)
    numbered_idx = page_no - from_page        # 0-based within range
    shown = numbered_idx + start
    text = fmt.replace("{n}", str(shown)).replace("{N}", str(numbered_total))
    m_pt = mm_to_pt(margin_mm)
    r = page.rect
    tw = font_size * len(text) * 0.55
    th = font_size * 1.2
    if position == "tl":   x, y = m_pt, m_pt + th
    elif position == "tc": x, y = (r.width - tw) / 2, m_pt + th
    elif position == "tr": x, y = r.width - tw - m_pt, m_pt + th
    elif position == "bl": x, y = m_pt, r.height - m_pt
    elif position == "bc": x, y = (r.width - tw) / 2, r.height - m_pt
    else:                  x, y = r.width - tw - m_pt, r.height - m_pt
    page.insert_text(
        (x, y), text,
        fontsize=font_size, fontname="helv",
        color=_hex_to_rgb01(color_hex),
    )


@router.post("/preview-thumb")
async def preview_thumb(
    upload_id: str = Form(...),
    page: int = Form(...),
    position: str = Form("br"),
    fmt: str = Form("{n} / {N}"),
    start: int = Form(1),
    font_size: float = Form(11.0),
    margin_mm: float = Form(10.0),
    color: str = Form("#000000"),
    from_page: int = Form(1),
    to_page: int = Form(0),  # 0 = until last page
):
    """Apply the page-number to ONE page in-memory and return a PNG thumb so
    the user sees the *real* rendered output rather than a UI overlay."""
    src = settings.temp_dir / f"pnL_{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(404, "upload not found (expired?)")
    with fitz.open(str(src)) as doc:
        if page < 1 or page > doc.page_count:
            raise HTTPException(400, "page out of range")
        tp: int | None = to_page if to_page and to_page > 0 else None
        _draw_pageno(
            doc[page - 1], page_index=page - 1, total=doc.page_count,
            position=position, fmt=fmt, start=start,
            font_size=font_size, margin_mm=margin_mm, color_hex=color,
            from_page=max(1, from_page), to_page=tp,
        )
        pix = doc[page - 1].get_pixmap(dpi=64, alpha=False)
        png = pix.tobytes("png")
    from fastapi.responses import Response as _Resp
    return _Resp(content=png, media_type="image/png",
                 headers={"Cache-Control": "no-store"})


@router.post("/submit")
async def submit(
    request: Request,
    file: List[UploadFile] = File(...),
    position: str = Form("br"),    # tl tr bl br tc bc
    fmt: str = Form("{n} / {N}"),  # template tokens: {n}=current 1-based, {N}=total
    start: int = Form(1),
    font_size: float = Form(11.0),
    margin_mm: float = Form(10.0),
    color: str = Form("#000000"),
    from_page: int = Form(1),
    to_page: int = Form(0),  # 0 = until last page
):
    files = file or []
    if not files: raise HTTPException(400, "沒有檔案")
    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
    bdir = settings.temp_dir / f"pn_{bid}"; bdir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")
        data = await f.read()
        if not data: raise HTTPException(400, f"空檔：{f.filename}")
        sp = bdir / f"{i:03d}_{Path(f.filename).name}"; sp.write_bytes(data)
        saved.append((sp, f.filename))

    def run(job):
        outs: list[Path] = []
        for fi, (sp, orig) in enumerate(saved):
            job.message = f"處理 {orig}"; job.progress = (fi/len(saved)) * 0.95
            with fitz.open(str(sp)) as doc:
                N = doc.page_count
                tp: int | None = to_page if to_page and to_page > 0 else None
                for i, page in enumerate(doc):
                    _draw_pageno(
                        page, page_index=i, total=N,
                        position=position, fmt=fmt, start=start,
                        font_size=font_size, margin_mm=margin_mm,
                        color_hex=color,
                        from_page=max(1, from_page), to_page=tp,
                    )
                op = bdir / f"{Path(orig).stem}_pageno.pdf"
                doc.save(str(op), garbage=3, deflate=True)
                outs.append(op)
        if len(outs) == 1:
            job.result_path = outs[0]; job.result_filename = outs[0].name
        else:
            zname = f"pageno_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = bdir / zname
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in outs: zf.write(p, arcname=p.name)
            job.result_path = zp; job.result_filename = zname
        job.progress = 1.0; job.message = f"完成（{len(outs)} 份）"

    job = job_manager.submit("pdf-pageno", run, meta={"count": len(saved)})
    return {"job_id": job.id}
