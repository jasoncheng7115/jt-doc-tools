"""Endpoints for PDF 註解固定化."""
from __future__ import annotations

import uuid
from collections import Counter
from pathlib import Path

import fitz
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ...config import settings
from ...core.http_utils import content_disposition

from ..pdf_annotations.router import _read_annotations


router = APIRouter()


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
async def flatten(file: UploadFile = File(...)):
    """Bake all annotations into the page content stream.

    Uses PyMuPDF's ``doc.bake(annots=True, widgets=False)`` — this draws every
    annotation's appearance directly onto the page, then removes the annotation
    object. Result: the marks are visually identical but cannot be edited or
    extracted via the annotation API. Form widgets are left active so users
    can still fill out forms after flattening review markup.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    uid = uuid.uuid4().hex
    src = settings.temp_dir / f"flat_{uid}_in.pdf"
    out = settings.temp_dir / f"flat_{uid}_out.pdf"
    src.write_bytes(data)
    baked = 0
    try:
        with fitz.open(str(src)) as doc:
            if doc.needs_pass:
                raise HTTPException(400, "PDF 已加密,請先解密")
            # Count annotations before baking — bake() destroys them.
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


@router.post("/api/pdf-annotations-flatten")
async def api_flatten(file: UploadFile = File(...)):
    return await flatten(file)
