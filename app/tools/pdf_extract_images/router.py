from __future__ import annotations
import io
import time
import uuid
import zipfile
from pathlib import Path
from typing import List
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
import fitz
from ...config import settings
from ...core.job_manager import job_manager
from ...core import pdf_preview
from ...core.http_utils import content_disposition

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_extract_images.html", {"request": request})


# ---------- input PDF preview (page thumbs after upload) ----------

@router.post("/load")
async def load(request: Request, file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data: raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
    src = settings.temp_dir / f"exL_{upload_id}.pdf"
    src.write_bytes(data)
    with fitz.open(str(src)) as doc:
        n = doc.page_count
    return {
        "upload_id": upload_id, "filename": file.filename, "page_count": n,
        "pages": [
            {"page": i + 1, "thumb": f"/tools/pdf-extract-images/page-thumb/{upload_id}/{i + 1}"}
            for i in range(n)
        ],
    }


@router.get("/page-thumb/{upload_id}/{page}")
async def page_thumb(upload_id: str, page: int, large: bool = False):
    src = settings.temp_dir / f"exL_{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(404, "upload not found (expired?)")
    suffix = "_large" if large else ""
    out = settings.temp_dir / f"exL_{upload_id}_thumb{suffix}_{page}.png"
    if not out.exists():
        pdf_preview.render_page_png(src, out, page - 1, dpi=160 if large else 64)
    return FileResponse(str(out), media_type="image/png",
                        headers={"Cache-Control": "max-age=300"})


# ---------- extract job: store individual images on disk so we can serve
# thumbs and let users pick + download a subset. ----------

@router.post("/extract")
async def extract(request: Request, file: UploadFile = File(...)):
    """Run extraction on ONE file. Returns the list of extracted images so
    the UI can render thumbs and offer per-image downloads.

    Wrapped in asyncio.to_thread (same pattern as pdf-extract-text and
    pdf-to-image): the fitz.open + per-page get_images + per-image Pixmap
    construction is sync C code that would otherwise block the event loop
    for the whole site on a large PDF."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data: raise HTTPException(400, "empty file")
    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
    bdir = settings.temp_dir / f"ext_{bid}"; bdir.mkdir(parents=True, exist_ok=True)
    src = bdir / Path(file.filename).name
    src.write_bytes(data)
    request.state.upload_filename = file.filename or ""

    import asyncio as _asyncio

    def _do_extract():
        # Dedupe by xref. PDFs commonly reference the same Image XObject
        # (logos, header backgrounds) on every page; without dedupe a
        # 50-page deck with one logo gives 50 copies of that logo. Track
        # by xref so each unique image is extracted once and we record
        # all the page numbers it appears on.
        items: list[dict] = []
        seen_xref: dict[int, int] = {}   # xref -> index into items
        with fitz.open(str(src)) as doc:
            global_idx = 0
            for pno in range(doc.page_count):
                page = doc[pno]
                for img in page.get_images(full=True):
                    xref = img[0]
                    smask_xref = img[1] if len(img) > 1 else 0
                    if xref in seen_xref:
                        items[seen_xref[xref]]["pages"].append(pno + 1)
                        continue
                    ext = "png"; img_bytes: bytes
                    try:
                        pix = fitz.Pixmap(doc, xref)
                        if smask_xref:
                            try:
                                mask = fitz.Pixmap(doc, smask_xref)
                                if pix.n - pix.alpha >= 4:
                                    pix = fitz.Pixmap(fitz.csRGB, pix)
                                pix = fitz.Pixmap(pix, mask)
                            except Exception:
                                pass
                        elif pix.n - pix.alpha >= 4:
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        img_bytes = pix.tobytes("png")
                    except Exception:
                        d = doc.extract_image(xref)
                        img_bytes = d.get("image", b""); ext = d.get("ext", "bin")
                    if not img_bytes:
                        continue
                    global_idx += 1
                    key = f"img{global_idx:03d}_xref{xref}.{ext}"
                    fp = bdir / key
                    fp.write_bytes(img_bytes)
                    seen_xref[xref] = len(items)
                    items.append({
                        "id": key, "page": pno + 1, "pages": [pno + 1], "ext": ext,
                        "size": len(img_bytes), "xref": xref,
                        "url": f"/tools/pdf-extract-images/file/{bid}/{key}",
                    })
        return items

    items = await _asyncio.to_thread(_do_extract)
    return {"batch_id": bid, "filename": file.filename,
            "count": len(items), "items": items}


@router.get("/file/{batch_id}/{name}")
async def get_file(batch_id: str, name: str):
    safe = Path(name).name
    fp = settings.temp_dir / f"ext_{batch_id}" / safe
    if not fp.exists():
        raise HTTPException(404)
    media = "image/png" if safe.lower().endswith(".png") else "application/octet-stream"
    return FileResponse(str(fp), media_type=media)


@router.post("/zip-selected")
async def zip_selected(request: Request):
    body = await request.json()
    batch_id = body.get("batch_id")
    names = body.get("names") or []
    if not batch_id or not isinstance(names, list) or not names:
        raise HTTPException(400, "batch_id 與 names 必填")
    bdir = settings.temp_dir / f"ext_{batch_id}"
    if not bdir.exists():
        raise HTTPException(404, "batch 不存在或已過期")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in names:
            safe = Path(str(n)).name
            fp = bdir / safe
            if fp.exists():
                zf.write(fp, arcname=safe)
    name = f"images_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": content_disposition(name)},
    )


# ---------- legacy multi-file batch submit (kept for backward compat) ----------

@router.post("/submit")
async def submit(file: List[UploadFile] = File(...)):
    files = file or []
    if not files: raise HTTPException(400, "沒有檔案")
    bid = uuid.uuid4().hex
    bdir = settings.temp_dir / f"ext_{bid}"; bdir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")
        data = await f.read()
        if not data: raise HTTPException(400, f"空檔：{f.filename}")
        sp = bdir / f"{i:03d}_{Path(f.filename).name}"; sp.write_bytes(data)
        saved.append((sp, f.filename))

    zname = f"images_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    zp = bdir / zname

    def run(job):
        total_imgs = 0
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            for fi, (sp, orig) in enumerate(saved):
                stem = Path(orig).stem
                job.message = f"擷取 {orig}"; job.progress = (fi/len(saved)) * 0.95
                with fitz.open(str(sp)) as doc:
                    for pno in range(doc.page_count):
                        page = doc[pno]
                        for img_index, img in enumerate(page.get_images(full=True), start=1):
                            xref = img[0]
                            smask_xref = img[1] if len(img) > 1 else 0
                            try:
                                pix = fitz.Pixmap(doc, xref)
                                if smask_xref:
                                    try:
                                        mask = fitz.Pixmap(doc, smask_xref)
                                        if pix.n - pix.alpha >= 4:
                                            pix = fitz.Pixmap(fitz.csRGB, pix)
                                        pix = fitz.Pixmap(pix, mask)
                                    except Exception:
                                        pass
                                elif pix.n - pix.alpha >= 4:
                                    pix = fitz.Pixmap(fitz.csRGB, pix)
                                ext = "png"
                                data = pix.tobytes("png")
                            except Exception:
                                d = doc.extract_image(xref)
                                data = d.get("image"); ext = d.get("ext", "bin")
                            arcname = f"{stem}/p{pno + 1:03d}_img{img_index:02d}.{ext}"
                            zf.writestr(arcname, data)
                            total_imgs += 1
        job.result_path = zp; job.result_filename = zname
        job.progress = 1.0; job.message = f"完成（{total_imgs} 張圖片）"

    job = job_manager.submit("pdf-extract-images", run, meta={"count": len(saved)})
    return {"job_id": job.id}
