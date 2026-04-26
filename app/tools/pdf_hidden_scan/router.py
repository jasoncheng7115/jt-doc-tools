"""Endpoints for PDF 隱藏內容掃描."""
from __future__ import annotations

import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings


router = APIRouter()


def _scan(doc: "fitz.Document") -> dict:
    """Walk the document and collect every class of hidden / risky
    content we support removing. Returns {category: [findings]}."""
    js_events: list[dict] = []
    embeds: list[dict] = []
    uri_links: list[dict] = []
    launch_actions: list[dict] = []
    hidden_text: list[dict] = []
    annot_details: list[dict] = []
    threed_multi: list[dict] = []

    # 1) Document-level JS (/OpenAction, /AA, /Names/JavaScript)
    try:
        cat = doc.pdf_catalog()
        cat_obj = doc.xref_object(cat, compressed=False) if cat else ""
        if "/JavaScript" in cat_obj or "/JS" in cat_obj:
            js_events.append({"scope": "document", "kind": "catalog-js",
                              "detail": "Catalog 內含 JavaScript 或 Names tree /JavaScript"})
        if "/OpenAction" in cat_obj:
            js_events.append({"scope": "document", "kind": "open-action",
                              "detail": "/OpenAction（開檔即執行動作）"})
    except Exception:
        pass

    # 2) Embedded files
    try:
        for name in doc.embfile_names():
            try:
                meta = doc.embfile_info(name) or {}
                embeds.append({
                    "name": name,
                    "size": meta.get("size"),
                    "subtype": meta.get("subject") or meta.get("description") or "",
                })
            except Exception:
                embeds.append({"name": name})
    except Exception:
        pass

    for pno in range(doc.page_count):
        page = doc[pno]
        # 3) Link actions — URI or Launch
        try:
            for link in page.get_links() or []:
                kind = link.get("kind")
                # PyMuPDF: link["kind"] — 1=GOTO, 2=GOTOR, 3=LAUNCH, 4=URI, ...
                if kind == fitz.LINK_LAUNCH:
                    launch_actions.append({"page": pno + 1,
                                           "target": link.get("file", "")})
                elif kind == fitz.LINK_URI:
                    uri_links.append({"page": pno + 1,
                                      "uri": link.get("uri", "")})
        except Exception:
            pass

        # 4) Annotations with triggers
        try:
            for annot in (page.annots() or []):
                t = annot.type
                info = annot.info or {}
                annot_details.append({
                    "page": pno + 1,
                    "type": t[1] if isinstance(t, (list, tuple)) else str(t),
                    "author": info.get("title", ""),
                    "content": (info.get("content") or "")[:80],
                })
        except Exception:
            pass

        # 5) White-on-white / outside-page text
        try:
            prect = page.rect
            td = page.get_text("dict")
            for block in td.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for sp in line.get("spans", []):
                        txt = (sp.get("text") or "").strip()
                        if not txt:
                            continue
                        col = int(sp.get("color", 0) or 0)
                        # White text (0xFFFFFF)
                        if col == 0xFFFFFF:
                            hidden_text.append({
                                "page": pno + 1, "reason": "white",
                                "text": txt[:60]
                            })
                            continue
                        bbox = sp.get("bbox", [0, 0, 0, 0])
                        bx0, by0, bx1, by1 = bbox
                        # Entirely outside the page (common smuggling trick)
                        if bx1 < 0 or by1 < 0 or bx0 > prect.width or by0 > prect.height:
                            hidden_text.append({
                                "page": pno + 1, "reason": "outside-page",
                                "text": txt[:60]
                            })
                            continue
                        # Font size zero or near-zero
                        if float(sp.get("size", 0) or 0) < 0.5:
                            hidden_text.append({
                                "page": pno + 1, "reason": "zero-size",
                                "text": txt[:60]
                            })
        except Exception:
            pass

    # 6) 3D / RichMedia — look for /Type /3D or /RichMedia in page contents
    try:
        for pno in range(doc.page_count):
            try:
                page_xref = doc.page_xref(pno)
                obj = doc.xref_object(page_xref, compressed=False) or ""
                if "/3D" in obj or "/RichMedia" in obj or "/Movie" in obj:
                    threed_multi.append({"page": pno + 1})
            except Exception:
                continue
    except Exception:
        pass

    return {
        "js_events": js_events,
        "embeds": embeds,
        "uri_links": uri_links,
        "launch_actions": launch_actions,
        "hidden_text": hidden_text,
        "annot_details": annot_details,
        "threed_multi": threed_multi,
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_hidden_scan.html", {"request": request})


@router.post("/scan")
async def scan(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    uid = uuid.uuid4().hex
    src = settings.temp_dir / f"hid_{uid}_in.pdf"
    src.write_bytes(data)
    try:
        (settings.temp_dir / f"hid_{uid}_name.txt").write_text(
            file.filename or "document.pdf", encoding="utf-8")
    except Exception:
        pass
    import asyncio as _asyncio
    def _run_scan():
        with fitz.open(str(src)) as doc:
            return _scan(doc)
    findings = await _asyncio.to_thread(_run_scan)
    totals = {k: len(v) for k, v in findings.items()}
    return {"upload_id": uid, "filename": file.filename,
            "findings": findings, "totals": totals}


def _clean_sync(src, out, strip):
    """Heavy hidden-content cleanup (PyMuPDF), called via asyncio.to_thread."""
    doc = fitz.open(str(src))
    removed = {"js": 0, "embeds": 0, "uri": 0, "launch": 0,
               "hidden_text": 0, "annots": 0, "3d": 0}
    try:
        # Document-level JavaScript: clear Names /JavaScript tree +
        # /OpenAction + each page /AA entries.
        if "js" in strip:
            try:
                cat = doc.pdf_catalog()
                if cat:
                    obj = doc.xref_object(cat, compressed=False) or ""
                    new_obj = obj
                    for key in ("/OpenAction", "/AA", "/JavaScript", "/JS"):
                        while key in new_obj:
                            # naive strip — remove the key/value pair line
                            idx = new_obj.find(key)
                            # find the end of this entry (next newline or >>)
                            end_idx = new_obj.find("\n", idx)
                            if end_idx < 0:
                                end_idx = idx + len(key)
                            new_obj = new_obj[:idx] + new_obj[end_idx:]
                            removed["js"] += 1
                    if new_obj != obj:
                        doc.update_object(cat, new_obj)
            except Exception:
                pass

        if "embeds" in strip:
            try:
                for name in list(doc.embfile_names()):
                    try:
                        doc.embfile_del(name)
                        removed["embeds"] += 1
                    except Exception:
                        pass
            except Exception:
                pass

        # Per-page cleanup for links + annotations
        if any(k in strip for k in ("uri", "launch", "annots", "hidden_text")):
            for pno in range(doc.page_count):
                page = doc[pno]
                # Links: rebuild without URI/Launch ones
                if "uri" in strip or "launch" in strip:
                    try:
                        for link in list(page.get_links() or []):
                            k = link.get("kind")
                            if "uri" in strip and k == fitz.LINK_URI:
                                try: page.delete_link(link); removed["uri"] += 1
                                except Exception: pass
                            elif "launch" in strip and k == fitz.LINK_LAUNCH:
                                try: page.delete_link(link); removed["launch"] += 1
                                except Exception: pass
                    except Exception:
                        pass
                if "annots" in strip:
                    try:
                        for a in list(page.annots() or []):
                            try: page.delete_annot(a); removed["annots"] += 1
                            except Exception: pass
                    except Exception:
                        pass
                if "hidden_text" in strip:
                    # Re-scan and redact each hidden-text bbox
                    try:
                        prect = page.rect
                        td = page.get_text("dict")
                        any_redact = False
                        for block in td.get("blocks", []):
                            if block.get("type") != 0:
                                continue
                            for line in block.get("lines", []):
                                for sp in line.get("spans", []):
                                    if not (sp.get("text") or "").strip():
                                        continue
                                    col = int(sp.get("color", 0) or 0)
                                    bbox = sp.get("bbox", [0, 0, 0, 0])
                                    bx0, by0, bx1, by1 = bbox
                                    bad = False
                                    if col == 0xFFFFFF: bad = True
                                    elif bx1 < 0 or by1 < 0 or bx0 > prect.width or by0 > prect.height: bad = True
                                    elif float(sp.get("size", 0) or 0) < 0.5: bad = True
                                    if bad:
                                        try:
                                            page.add_redact_annot(fitz.Rect(*bbox))
                                            any_redact = True
                                            removed["hidden_text"] += 1
                                        except Exception:
                                            pass
                        if any_redact:
                            try:
                                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
                            except Exception:
                                page.apply_redactions()
                    except Exception:
                        pass
        doc.save(str(out), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
    return removed


@router.post("/clean")
async def clean(request: Request):
    body = await request.json()
    uid = (body.get("upload_id") or "").strip()
    if not uid:
        raise HTTPException(400, "upload_id required")
    src = settings.temp_dir / f"hid_{uid}_in.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")
    strip = set(body.get("strip") or [])
    # Supported keys: js, embeds, uri, launch, hidden_text, annots, 3d
    out = settings.temp_dir / f"hid_{uid}_out.pdf"
    import asyncio as _asyncio
    removed = await _asyncio.to_thread(_clean_sync, src, out, strip)
    return {
        "ok": True, "removed": removed,
        "download_url": f"/tools/pdf-hidden-scan/download/{uid}",
    }


@router.get("/download/{uid}")
async def download(uid: str):
    out = settings.temp_dir / f"hid_{uid}_out.pdf"
    if not out.exists():
        raise HTTPException(404, "未產生或已過期")
    stem = "document"
    try:
        n = (settings.temp_dir / f"hid_{uid}_name.txt").read_text(encoding="utf-8").strip()
        if n: stem = Path(n).stem
    except Exception:
        pass
    return FileResponse(str(out), media_type="application/pdf",
                        filename=f"{stem}_cleaned.pdf")
