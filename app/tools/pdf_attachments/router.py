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
    from app.core.safe_paths import require_uuid_hex
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
    from app.core.safe_paths import require_uuid_hex
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


# ---- 對外 API：單次 upload + 回 ZIP（所有附件）----
@router.post("/api/pdf-attachments", include_in_schema=True)
async def api_pdf_attachments(request: Request, file: UploadFile = File(...)):
    """單次上傳 PDF，抽出所有內嵌附件，回 ZIP。"""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data or data[:4] != b"%PDF":
        raise HTTPException(400, "不是有效的 PDF")
    uid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(uid, request)
    src = settings.temp_dir / f"att_api_{uid}_in.pdf"
    src.write_bytes(data)
    stem = Path(file.filename or "document").stem
    import asyncio as _asyncio
    def _do() -> tuple[bytes, int]:
        buf = io.BytesIO()
        count = 0
        with fitz.open(str(src)) as doc:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                try:
                    names = list(doc.embfile_names())
                except Exception:
                    names = []
                for name in names:
                    try:
                        payload = doc.embfile_get(name)
                    except Exception:
                        payload = None
                    if payload is None:
                        continue
                    zf.writestr(_safe_name(name), payload)
                    count += 1
        return buf.getvalue(), count
    zip_bytes, count = await _asyncio.to_thread(_do)
    if count == 0:
        raise HTTPException(404, "PDF 內未找到任何附件")
    return Response(
        content=zip_bytes, media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"{stem}_attachments.zip")},
    )
