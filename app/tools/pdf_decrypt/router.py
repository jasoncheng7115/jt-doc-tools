"""Endpoints for PDF 密碼解除."""
from __future__ import annotations

import time
import uuid
import zipfile
from pathlib import Path
from typing import List

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...config import settings
from ...core.job_manager import job_manager


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_decrypt.html", {"request": request})


@router.post("/submit")
async def submit(
    file: List[UploadFile] = File(...),
    password: str = Form(""),
):
    files = file or []
    if not files:
        raise HTTPException(400, "沒有檔案")

    bid = uuid.uuid4().hex
    bdir = settings.temp_dir / f"dec_{bid}"
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

    def run(job):
        outs: list[Path] = []
        bad: list[str] = []
        for fi, (sp, orig) in enumerate(saved):
            job.message = f"解除 {orig}"
            job.progress = (fi / len(saved)) * 0.95
            with fitz.open(str(sp)) as doc:
                if doc.needs_pass:
                    if not password or not doc.authenticate(password):
                        bad.append(orig)
                        continue
                op = bdir / f"{Path(orig).stem}_decrypted.pdf"
                # Saving with encryption=NONE strips all protection.
                doc.save(str(op), encryption=fitz.PDF_ENCRYPT_NONE,
                         garbage=3, deflate=True)
                outs.append(op)
        if bad and not outs:
            raise RuntimeError(f"密碼不正確：{', '.join(bad)}")
        if bad:
            job.message = f"完成，但以下檔案密碼錯誤已跳過：{', '.join(bad)}"
        if len(outs) == 1:
            job.result_path = outs[0]; job.result_filename = outs[0].name
        else:
            zname = f"decrypted_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = bdir / zname
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in outs: zf.write(p, arcname=p.name)
            job.result_path = zp; job.result_filename = zname
        job.progress = 1.0
        if not job.message or "已跳過" not in job.message:
            job.message = f"完成（{len(outs)} 份）"

    job = job_manager.submit("pdf-decrypt", run, meta={"count": len(saved)})
    return {"job_id": job.id}
