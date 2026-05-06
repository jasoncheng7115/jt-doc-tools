"""Endpoints for PDF 註解平面化."""
from __future__ import annotations

import re
import uuid
from collections import Counter
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ...config import settings
from ...core.http_utils import content_disposition

from ..pdf_annotations.router import _read_annotations


router = APIRouter()

_UID_RE = re.compile(r"^[a-f0-9]{32}$")


def _validate_uid(uid: str) -> None:
    if not _UID_RE.match(uid or ""):
        raise HTTPException(400, "invalid baked_uid")


def _baked_paths(uid: str) -> tuple[Path, Path]:
    """Return (baked_pdf_path, name_sidecar_path)."""
    return (
        settings.temp_dir / f"flat_{uid}_out.pdf",
        settings.temp_dir / f"flat_{uid}_name.txt",
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_annotations_flatten.html", {"request": request})


@router.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    src = settings.temp_dir / f"flat_{uuid.uuid4().hex}_in.pdf"
    src.write_bytes(data)
    try:
        annots = _read_annotations(src)
        with fitz.open(str(src)) as doc:
            pc = doc.page_count
            has_widgets = any(p.first_widget for p in doc)
        by_type = Counter(a["type_label"] for a in annots)
        return JSONResponse({
            "filename":    file.filename,
            "page_count":  pc,
            "total":       len(annots),
            "by_type":     [{"label": k, "count": v} for k, v in by_type.most_common()],
            "has_widgets": has_widgets,
        })
    finally:
        src.unlink(missing_ok=True)


@router.post("/flatten")
async def flatten(request: Request, file: UploadFile = File(...)):
    """Bake all annotations into the page content stream.

    Uses PyMuPDF's ``doc.bake(annots=True, widgets=False)`` — this draws every
    annotation's appearance directly onto the page, then removes the annotation
    object. Result: the marks are visually identical but cannot be edited or
    extracted via the annotation API. Form widgets are left active so users
    can still fill out forms after flattening review markup.

    Returns JSON with ``baked_uid``; client then calls ``/baked-preview`` for
    thumbnails and ``/baked-download`` to fetch the file. Avoiding immediate
    file response lets the UI show a per-page preview before the user commits
    to saving.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    baked_uid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(baked_uid, request)
    src = settings.temp_dir / f"flat_{baked_uid}_in.pdf"
    out, name_path = _baked_paths(baked_uid)
    src.write_bytes(data)
    baked = 0
    try:
        with fitz.open(str(src)) as doc:
            if doc.needs_pass:
                raise HTTPException(400, "PDF 已加密，請先解密")
            # Count annotations before baking — bake() destroys them.
            for p in doc:
                baked += sum(1 for _ in (p.annots() or []))
            page_count = doc.page_count
            doc.bake(annots=True, widgets=False)
            doc.save(str(out), garbage=4, deflate=True)
        # Persist the original filename for the eventual download.
        name_path.write_text(file.filename or "document.pdf", encoding="utf-8")
        return JSONResponse({
            "baked_uid":   baked_uid,
            "filename":    file.filename,
            "page_count":  page_count,
            "baked_count": baked,
        })
    finally:
        src.unlink(missing_ok=True)


@router.post("/api/pdf-annotations-flatten")
async def api_flatten(file: UploadFile = File(...)):
    """API variant — returns the flattened PDF directly (no preview step)."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    uid = uuid.uuid4().hex
    src = settings.temp_dir / f"flat_{uid}_api_in.pdf"
    out = settings.temp_dir / f"flat_{uid}_api_out.pdf"
    src.write_bytes(data)
    baked = 0
    try:
        with fitz.open(str(src)) as doc:
            if doc.needs_pass:
                raise HTTPException(400, "PDF 已加密，請先解密")
            for p in doc:
                baked += sum(1 for _ in (p.annots() or []))
            doc.bake(annots=True, widgets=False)
            doc.save(str(out), garbage=4, deflate=True)
        base = Path(file.filename or "document.pdf").stem
        return FileResponse(
            str(out),
            media_type="application/pdf",
            filename=f"{base}_flattened.pdf",
            headers={"X-Annotations-Baked": str(baked),
                     "Content-Disposition": content_disposition(f"{base}_flattened.pdf")},
        )
    finally:
        src.unlink(missing_ok=True)


@router.get("/baked-preview/{baked_uid}/{page}")
async def baked_preview(baked_uid: str, page: int, request: Request):
    """Render a single page of the flattened PDF as a thumbnail PNG."""
    _validate_uid(baked_uid)
    from ...core import upload_owner
    upload_owner.require(baked_uid, request)
    if page < 1:
        raise HTTPException(400, "invalid page")
    out, _ = _baked_paths(baked_uid)
    if not out.exists():
        raise HTTPException(410, "結果已過期，請重新執行")
    with fitz.open(str(out)) as doc:
        if page > doc.page_count:
            raise HTTPException(404, "page out of range")
        mat = fitz.Matrix(1.4, 1.4)  # ~100 DPI thumbnail
        pix = doc[page - 1].get_pixmap(matrix=mat, alpha=False)
        png = pix.tobytes("png")
    from fastapi.responses import Response
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=600"})


@router.get("/baked-download/{baked_uid}")
async def baked_download(baked_uid: str, request: Request):
    """Stream the flattened PDF for download."""
    _validate_uid(baked_uid)
    from ...core import upload_owner
    upload_owner.require(baked_uid, request)
    out, name_path = _baked_paths(baked_uid)
    if not out.exists():
        raise HTTPException(410, "結果已過期，請重新執行")
    fname = "document.pdf"
    if name_path.exists():
        try:
            fname = name_path.read_text(encoding="utf-8").strip() or fname
        except Exception:
            pass
    base = Path(fname).stem
    download_name = f"{base}_flattened.pdf"
    return FileResponse(
        str(out),
        media_type="application/pdf",
        filename=download_name,
        headers={"Content-Disposition": content_disposition(download_name)},
    )
