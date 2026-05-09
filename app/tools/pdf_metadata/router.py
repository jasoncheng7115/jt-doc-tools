"""Endpoints for 中繼資料清除."""
from __future__ import annotations

import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_metadata.html", {"request": request})


@router.post("/analyze")
async def analyze(request: Request, file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    uid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(uid, request)
    src = settings.temp_dir / f"meta_{uid}_in.pdf"
    src.write_bytes(data)
    try:
        (settings.temp_dir / f"meta_{uid}_name.txt").write_text(
            file.filename or "document.pdf", encoding="utf-8")
    except Exception:
        pass

    info: dict = {"upload_id": uid, "filename": file.filename}
    with fitz.open(str(src)) as doc:
        info["encrypted"] = bool(doc.needs_pass)
        info["metadata"] = dict(doc.metadata or {})
        # XMP blob (if any) — just the first ~1KB so UI can preview
        try:
            xmp = doc.get_xml_metadata() or ""
            info["xmp_present"] = bool(xmp.strip())
            info["xmp_preview"] = xmp[:800]
        except Exception:
            info["xmp_present"] = False
            info["xmp_preview"] = ""
        # Page-level signals
        page_count = doc.page_count
        annot_total = 0
        link_total = 0
        form_fields = 0
        js_total = 0
        attach_count = 0
        for pno in range(page_count):
            page = doc[pno]
            try:
                annot_total += sum(1 for _ in (page.annots() or []))
            except Exception:
                pass
            try:
                link_total += len(page.get_links() or [])
            except Exception:
                pass
            try:
                form_fields += sum(1 for _ in (page.widgets() or []))
            except Exception:
                pass
        try:
            attach_count = len(doc.embfile_names())
        except Exception:
            pass
        # Document-level JS: PyMuPDF doesn't expose catalog /OpenAction
        # directly — check via xref trailer scan (best-effort).
        try:
            cat = doc.pdf_catalog()
            cat_obj = doc.xref_object(cat, compressed=False) if cat else ""
            if "/OpenAction" in cat_obj or "/AA" in cat_obj or "/Names" in cat_obj:
                if "/JavaScript" in cat_obj or "/JS" in cat_obj:
                    js_total += 1
        except Exception:
            pass
        info["page_count"] = page_count
        info["annot_count"] = annot_total
        info["link_count"] = link_total
        info["form_field_count"] = form_fields
        info["attach_count"] = attach_count
        info["has_toc"] = bool(doc.get_toc())
    info["size"] = src.stat().st_size
    return info


@router.post("/clean")
async def clean(request: Request):
    body = await request.json()
    uid = (body.get("upload_id") or "").strip()
    if not uid:
        raise HTTPException(400, "upload_id required")
    src = settings.temp_dir / f"meta_{uid}_in.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")

    clear_info = bool(body.get("clear_info", True))
    clear_xmp = bool(body.get("clear_xmp", True))
    clear_toc = bool(body.get("clear_toc", False))
    clear_annots = bool(body.get("clear_annots", False))
    clear_forms = bool(body.get("clear_forms", False))

    out = settings.temp_dir / f"meta_{uid}_out.pdf"
    import asyncio as _asyncio
    def _do_clean():
        doc = fitz.open(str(src))
        try:
            if clear_info:
                try: doc.set_metadata({})
                except Exception: pass
            if clear_xmp:
                try: doc.set_xml_metadata("")
                except Exception: pass
            if clear_toc:
                try: doc.set_toc([])
                except Exception: pass
            if clear_annots or clear_forms:
                for pno in range(doc.page_count):
                    page = doc[pno]
                    if clear_annots:
                        try:
                            for a in list(page.annots() or []):
                                try: page.delete_annot(a)
                                except Exception: pass
                        except Exception: pass
                    if clear_forms:
                        try:
                            for w in list(page.widgets() or []):
                                try: page.delete_widget(w)
                                except Exception: pass
                        except Exception: pass
            doc.save(str(out), garbage=4, deflate=True, clean=True)
        finally:
            doc.close()
    await _asyncio.to_thread(_do_clean)

    stem = "document"
    try:
        name_file = settings.temp_dir / f"meta_{uid}_name.txt"
        if name_file.exists():
            stem = Path(name_file.read_text(encoding="utf-8").strip()).stem
    except Exception:
        pass
    return {
        "ok": True,
        "size": out.stat().st_size,
        "download_url": f"/tools/pdf-metadata/download/{uid}?name={stem}_clean.pdf",
    }


@router.get("/download/{uid}")
async def download(uid: str, request: Request, name: str = "clean.pdf"):
    from app.core.safe_paths import require_uuid_hex
    from ...core import upload_owner
    require_uuid_hex(uid, "uid")
    upload_owner.require(uid, request)
    out = settings.temp_dir / f"meta_{uid}_out.pdf"
    if not out.exists():
        raise HTTPException(404, "未產生或已過期")
    # Basic name sanitize
    safe = Path(name).name or "clean.pdf"
    return FileResponse(str(out), media_type="application/pdf",
                        filename=safe)
