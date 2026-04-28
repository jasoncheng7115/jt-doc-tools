"""Endpoints for PDF 註解清除."""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ...config import settings
from ...core.http_utils import content_disposition

# Reuse type label map + reader from sibling tool to avoid duplication.
from ..pdf_annotations.router import (
    _TYPE_LABELS,
    _USER_TYPE_IDS,
    _read_annotations,
)


router = APIRouter()
_UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _parse_csv_list(s: str | None) -> list[str]:
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


def _validate_upload_id(upload_id: str) -> None:
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        raise HTTPException(400, "invalid upload_id")


def _cached_paths(upload_id: str) -> tuple[Path, Path]:
    """Return (pdf_path, sidecar_json_path)."""
    return (
        settings.temp_dir / f"strip_{upload_id}_in.pdf",
        settings.temp_dir / f"strip_{upload_id}_data.json",
    )


def _load_cached(upload_id: str) -> dict[str, Any]:
    _validate_upload_id(upload_id)
    _, sidecar = _cached_paths(upload_id)
    if not sidecar.exists():
        raise HTTPException(410, "上傳已過期，請重新分析")
    return json.loads(sidecar.read_text(encoding="utf-8"))


def _english_for(zh_label: str) -> str:
    for tid, (en, zh) in _TYPE_LABELS.items():
        if zh == zh_label:
            return en
    return zh_label


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_annotations_strip.html", {"request": request})


@router.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    """Read annotations and persist PDF + sidecar JSON.

    The PDF stays at ``strip_{uid}_in.pdf`` so /preview can render
    thumbnails，subsequent /strip calls reuse the upload via ``upload_id``
    instead of re-uploading．Both files clean up on the 2-hour TTL．
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    src, sidecar = _cached_paths(upload_id)
    src.write_bytes(data)
    try:
        annots = _read_annotations(src)
        with fitz.open(str(src)) as doc:
            pc = doc.page_count
        by_type   = Counter(a["type_label"] for a in annots)
        by_author = Counter((a["author"] or "(未署名)") for a in annots)
        payload = {
            "filename":   file.filename or "document.pdf",
            "upload_id":  upload_id,
            "page_count": pc,
            "total":      len(annots),
            "annots":     annots,
            "by_type":    [{"label": k, "type": _english_for(k), "count": v}
                           for k, v in by_type.most_common()],
            "by_author":  [{"author": k, "count": v}
                           for k, v in by_author.most_common()],
        }
        sidecar.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return JSONResponse(payload)
    except Exception:
        src.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise


@router.get("/preview/{upload_id}/{page}")
async def preview(upload_id: str, page: int):
    """Render one page (with annotations baked) as a thumbnail PNG."""
    _validate_upload_id(upload_id)
    if page < 1:
        raise HTTPException(400, "invalid page")
    src, _ = _cached_paths(upload_id)
    if not src.exists():
        raise HTTPException(410, "上傳已過期，請重新分析")
    with fitz.open(str(src)) as doc:
        if page > doc.page_count:
            raise HTTPException(404, "page out of range")
        mat = fitz.Matrix(1.4, 1.4)
        pix = doc[page - 1].get_pixmap(matrix=mat, alpha=False)
        png = pix.tobytes("png")
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=600"})


def _strip_pdf(src: Path, out: Path,
               mode: str, type_set: set[str], author_set: set[str]) -> int:
    """Run the actual annot deletion; returns count removed."""
    removed = 0
    with fitz.open(str(src)) as doc:
        if doc.needs_pass:
            raise HTTPException(400, "PDF 已加密，請先解密")
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
    return removed


@router.post("/strip")
async def strip(
    upload_id: str = Form(...),
    types: str = Form(""),
    authors: str = Form(""),
    mode: str = Form("all"),
):
    """Remove annotations and return cleaned PDF — uses cached upload."""
    cached = _load_cached(upload_id)
    src, _ = _cached_paths(upload_id)
    if not src.exists():
        raise HTTPException(410, "上傳已過期，請重新分析")

    type_set   = set(_parse_csv_list(types))
    author_set = set(_parse_csv_list(authors))
    if mode == "filter" and not (type_set or author_set):
        raise HTTPException(400, "篩選模式至少要勾選一個類型或作者")

    out = settings.temp_dir / f"strip_{upload_id}_out.pdf"
    removed = _strip_pdf(src, out, mode, type_set, author_set)
    base = Path(cached["filename"]).stem
    return FileResponse(
        str(out),
        media_type="application/pdf",
        filename=f"{base}_no-annots.pdf",
        headers={"X-Annotations-Removed": str(removed),
                 "Content-Disposition": content_disposition(f"{base}_no-annots.pdf")},
    )


@router.post("/api/pdf-annotations-strip")
async def api_strip(
    file: UploadFile = File(...),
    types: str = Form(""),
    authors: str = Form(""),
    mode: str = Form("all"),
):
    """Public API: single-shot upload + strip + download (no caching)."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    uid = uuid.uuid4().hex
    src = settings.temp_dir / f"strip_api_{uid}_in.pdf"
    out = settings.temp_dir / f"strip_api_{uid}_out.pdf"
    src.write_bytes(data)
    type_set   = set(_parse_csv_list(types))
    author_set = set(_parse_csv_list(authors))
    if mode == "filter" and not (type_set or author_set):
        src.unlink(missing_ok=True)
        raise HTTPException(400, "篩選模式至少要勾選一個類型或作者")
    try:
        removed = _strip_pdf(src, out, mode, type_set, author_set)
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
