"""Endpoints for PDF 附件萃取."""
from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

import fitz
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from ...config import settings
from ...core.http_utils import content_disposition


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_attachments.html", {"request": request})


def _safe_name(name: str) -> str:
    return Path(name).name.replace("/", "_").replace("\\", "_") or "attachment.bin"


@router.post("/scan")
async def scan(request: Request, file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    uid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(uid, request)
    src = settings.temp_dir / f"att_{uid}_in.pdf"
    src.write_bytes(data)
    try:
        (settings.temp_dir / f"att_{uid}_name.txt").write_text(
            file.filename or "document.pdf", encoding="utf-8")
    except Exception:
        pass

    import asyncio as _asyncio
    def _scan():
        items: list[dict] = []
        with fitz.open(str(src)) as doc:
            try:
                names = list(doc.embfile_names())
            except Exception:
                names = []
            for name in names:
                try:
                    info = doc.embfile_info(name) or {}
                    items.append({
                        "name": name,
                        "size": int(info.get("size") or 0),
                        "creation": info.get("creationDate", ""),
                        "mod": info.get("modDate", ""),
                        "desc": info.get("desc") or info.get("description") or "",
                        "collection": info.get("collection", ""),
                    })
                except Exception:
                    items.append({"name": name})
        return items
    items = await _asyncio.to_thread(_scan)
    return {"upload_id": uid, "filename": file.filename, "attachments": items}


@router.get("/file/{uid}/{name}")
async def get_file(uid: str, name: str, request: Request):
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner
    require_uuid_hex(uid, "uid")
    upload_owner.require(uid, request)
    src = settings.temp_dir / f"att_{uid}_in.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")
    safe = _safe_name(name)
    with fitz.open(str(src)) as doc:
        try:
            data = doc.embfile_get(name)  # older API
        except Exception:
            data = None
        if data is None:
            raise HTTPException(404, "附件不存在")
    return Response(
        content=data, media_type="application/octet-stream",
        headers={"Content-Disposition": content_disposition(safe)},
    )


@router.post("/zip")
async def zip_all(request: Request):
    body = await request.json()
    uid = (body.get("upload_id") or "").strip()
    names = body.get("names") or []
    if not uid:
        raise HTTPException(400, "upload_id required")
    src = settings.temp_dir / f"att_{uid}_in.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with fitz.open(str(src)) as doc:
            for name in names:
                try:
                    data = doc.embfile_get(name)
                except Exception:
                    data = None
                if data is None:
                    continue
                zf.writestr(_safe_name(name), data)
                count += 1
    stem = "attachments"
    try:
        n = (settings.temp_dir / f"att_{uid}_name.txt").read_text(encoding="utf-8").strip()
        if n: stem = Path(n).stem + "_attachments"
    except Exception:
        pass
    return Response(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"{stem}.zip")},
    )


@router.post("/strip")
async def strip_attachments(request: Request):
    """Produce a copy of the PDF with every embedded file removed."""
    body = await request.json()
    uid = (body.get("upload_id") or "").strip()
    if not uid:
        raise HTTPException(400, "upload_id required")
    src = settings.temp_dir / f"att_{uid}_in.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")
    out = settings.temp_dir / f"att_{uid}_stripped.pdf"
    removed = 0
    doc = fitz.open(str(src))
    try:
        for name in list(doc.embfile_names()):
            try:
                doc.embfile_del(name)
                removed += 1
            except Exception:
                pass
        doc.save(str(out), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
    return {"ok": True, "removed": removed,
            "download_url": f"/tools/pdf-attachments/stripped/{uid}"}


@router.get("/stripped/{uid}")
async def stripped(uid: str, request: Request):
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(uid, "uid")
    _uo.require(uid, request)
    out = settings.temp_dir / f"att_{uid}_stripped.pdf"
    if not out.exists():
        raise HTTPException(404)
    stem = "document"
    try:
        n = (settings.temp_dir / f"att_{uid}_name.txt").read_text(encoding="utf-8").strip()
        if n: stem = Path(n).stem
    except Exception:
        pass
    return FileResponse(str(out), media_type="application/pdf",
                        filename=f"{stem}_no-attachments.pdf")
