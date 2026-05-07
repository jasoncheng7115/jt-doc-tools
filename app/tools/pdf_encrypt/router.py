"""Endpoints for PDF 密碼保護."""
from __future__ import annotations

import time
import uuid
import zipfile
from pathlib import Path
from typing import List

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core.job_manager import job_manager


router = APIRouter()


PERMISSION_FLAGS = {
    "print":           fitz.PDF_PERM_PRINT,
    "modify":          fitz.PDF_PERM_MODIFY,
    "copy":            fitz.PDF_PERM_COPY,
    "annotate":        fitz.PDF_PERM_ANNOTATE,
    "form":            fitz.PDF_PERM_FORM,
    "accessibility":   fitz.PDF_PERM_ACCESSIBILITY,
    "assemble":        fitz.PDF_PERM_ASSEMBLE,
    "print_hq":        fitz.PDF_PERM_PRINT_HQ,
}

ALGO_MAP = {
    "aes-256": fitz.PDF_ENCRYPT_AES_256,
    "aes-128": fitz.PDF_ENCRYPT_AES_128,
    "rc4-128": fitz.PDF_ENCRYPT_RC4_128,
}


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_encrypt.html", {"request": request})


@router.post("/submit")
async def submit(
    request: Request,
    file: List[UploadFile] = File(...),
    user_pw: str = Form(""),
    owner_pw: str = Form(""),
    algorithm: str = Form("aes-256"),
    allow_print: bool = Form(False),
    allow_modify: bool = Form(False),
    allow_copy: bool = Form(False),
    allow_annotate: bool = Form(False),
    allow_form: bool = Form(False),
    allow_accessibility: bool = Form(True),  # strongly recommended on
    allow_assemble: bool = Form(False),
    allow_print_hq: bool = Form(False),
):
    if not user_pw and not owner_pw:
        raise HTTPException(400, "至少需設定開啟密碼或擁有者密碼其中之一")
    if algorithm not in ALGO_MAP:
        raise HTTPException(400, f"algorithm 必須是 {list(ALGO_MAP)}")
    files = file or []
    if not files:
        raise HTTPException(400, "沒有檔案")

    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
    bdir = settings.temp_dir / f"enc_{bid}"
    bdir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")
        data = await f.read()
        if not data:
            raise HTTPException(400, f"空檔：{f.filename}")
        sp = bdir / f"{i:03d}_{Path(f.filename).name}"
        sp.write_bytes(data)
        saved.append((sp, f.filename))

    # Compose the permissions bitmask from the user's allow-checkboxes.
    permissions = 0
    if allow_print:         permissions |= PERMISSION_FLAGS["print"]
    if allow_modify:        permissions |= PERMISSION_FLAGS["modify"]
    if allow_copy:          permissions |= PERMISSION_FLAGS["copy"]
    if allow_annotate:      permissions |= PERMISSION_FLAGS["annotate"]
    if allow_form:          permissions |= PERMISSION_FLAGS["form"]
    if allow_accessibility: permissions |= PERMISSION_FLAGS["accessibility"]
    if allow_assemble:      permissions |= PERMISSION_FLAGS["assemble"]
    if allow_print_hq:      permissions |= PERMISSION_FLAGS["print_hq"]

    enc_algo = ALGO_MAP[algorithm]

    def run(job):
        outs: list[Path] = []
        for fi, (sp, orig) in enumerate(saved):
            job.message = f"加密 {orig}"
            job.progress = (fi / len(saved)) * 0.95
            with fitz.open(str(sp)) as doc:
                # If the input is already encrypted, try to open without
                # a password; if that fails, error out.
                if doc.needs_pass:
                    raise RuntimeError(f"{orig} 已經有密碼，請先用「PDF 密碼解除」移除")
                op = bdir / f"{Path(orig).stem}_encrypted.pdf"
                doc.save(
                    str(op),
                    encryption=enc_algo,
                    owner_pw=owner_pw or user_pw,
                    user_pw=user_pw,
                    permissions=permissions,
                    garbage=3, deflate=True,
                )
                outs.append(op)
        if len(outs) == 1:
            job.result_path = outs[0]; job.result_filename = outs[0].name
        else:
            zname = f"encrypted_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = bdir / zname
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in outs: zf.write(p, arcname=p.name)
            job.result_path = zp; job.result_filename = zname
        job.progress = 1.0
        job.message = f"完成（{len(outs)} 份）"

    job = job_manager.submit("pdf-encrypt", run, meta={"count": len(saved)})
    return {"job_id": job.id}
