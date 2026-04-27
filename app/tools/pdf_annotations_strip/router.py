"""Endpoints for PDF 註解清除."""
from __future__ import annotations

import uuid
from collections import Counter
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ...config import settings
from ...core.http_utils import content_disposition

# Reuse type label map + reader from sibling tool to avoid duplication.
from ..pdf_annotations.router import (
    _TYPE_LABELS,
    _USER_TYPE_IDS,
    _read_annotations,
)


router = APIRouter()


def _parse_csv_list(s: str | None) -> list[str]:
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_annotations_strip.html", {"request": request})


@router.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    src = settings.temp_dir / f"strip_{uuid.uuid4().hex}_in.pdf"
    src.write_bytes(data)
    try:
        annots = _read_annotations(src)
        with fitz.open(str(src)) as doc:
            pc = doc.page_count
        by_type   = Counter(a["type_label"] for a in annots)
        by_author = Counter((a["author"] or "(未署名)") for a in annots)
        return JSONResponse({
            "filename":   file.filename,
            "page_count": pc,
            "total":      len(annots),
            "by_type":    [{"label": k, "type": _english_for(k), "count": v}
                           for k, v in by_type.most_common()],
            "by_author":  [{"author": k, "count": v}
                           for k, v in by_author.most_common()],
        })
    finally:
        src.unlink(missing_ok=True)


def _english_for(zh_label: str) -> str:
    for tid, (en, zh) in _TYPE_LABELS.items():
        if zh == zh_label:
            return en
    return zh_label


@router.post("/strip")
async def strip(
    file: UploadFile = File(...),
    types: str = Form(""),
    authors: str = Form(""),
    mode: str = Form("all"),  # "all" | "filter"
):
    """Remove annotations and return cleaned PDF.

    - mode=all: drop every user-facing annotation.
    - mode=filter: only drop annotations matching `types` (English names) or
      `authors`. Empty selection in filter mode is a 400 to avoid accidental
      no-op downloads.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    uid = uuid.uuid4().hex
    src = settings.temp_dir / f"strip_{uid}_in.pdf"
    out = settings.temp_dir / f"strip_{uid}_out.pdf"
    src.write_bytes(data)

    type_set   = set(_parse_csv_list(types))
    author_set = set(_parse_csv_list(authors))
    if mode == "filter" and not (type_set or author_set):
        src.unlink(missing_ok=True)
        raise HTTPException(400, "篩選模式至少要勾選一個類型或作者")

    removed = 0
    try:
        with fitz.open(str(src)) as doc:
            if doc.needs_pass:
                raise HTTPException(400, "PDF 已加密,請先解密")
            for pno in range(doc.page_count):
                page = doc[pno]
                # Iterate to a list first — deleting while iterating page.annots()
                # invalidates the generator on some PyMuPDF versions.
                to_remove = []
                for a in page.annots() or []:
                    tid = a.type[0]
                    if tid not in _USER_TYPE_IDS:
                        continue
                    if mode == "all":
                        to_remove.append(a)
                        continue
                    info = a.info or {}
                    a_author = (info.get("title") or "").strip() or "(未署名)"
                    a_type   = a.type[1]
                    if (a_type in type_set) or (a_author in author_set):
                        to_remove.append(a)
                for a in to_remove:
                    page.delete_annot(a)
                    removed += 1
            doc.save(str(out), garbage=4, deflate=True)
        base = Path(file.filename or "document.pdf").stem
        return FileResponse(
            str(out),
            media_type="application/pdf",
            filename=f"{base}_no-annots.pdf",
            headers={"X-Annotations-Removed": str(removed),
                     "Content-Disposition": content_disposition(f"{base}_no-annots.pdf")},
        )
    finally:
        src.unlink(missing_ok=True)


@router.post("/api/pdf-annotations-strip")
async def api_strip(
    file: UploadFile = File(...),
    types: str = Form(""),
    authors: str = Form(""),
    mode: str = Form("all"),
):
    return await strip(file, types=types, authors=authors, mode=mode)
