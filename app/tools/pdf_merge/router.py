from __future__ import annotations
import time
import uuid
from pathlib import Path
from typing import List
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
import fitz
from ...config import settings
from ...core.job_manager import job_manager

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_merge.html", {"request": request})


@router.post("/submit")
async def submit(request: Request, file: List[UploadFile] = File(...)):
    files = file or []
    if len(files) < 2:
        raise HTTPException(400, "至少需要 2 個 PDF 檔")
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")
    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
    bdir = settings.temp_dir / f"merge_{bid}"
    bdir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        data = await f.read()
        if not data:
            raise HTTPException(400, f"空檔：{f.filename}")
        sp = bdir / f"{i:03d}_{Path(f.filename).name}"
        sp.write_bytes(data)
        saved.append((sp, f.filename))

    out_name = f"merged_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    out_path = bdir / out_name

    def run(job):
        merged = fitz.open()
        for i, (sp, _orig) in enumerate(saved):
            job.message = f"合併第 {i + 1}/{len(saved)} 份"
            job.progress = (i / len(saved)) * 0.95
            with fitz.open(str(sp)) as src:
                merged.insert_pdf(src)
        merged.save(str(out_path), garbage=3, deflate=True)
        merged.close()
        job.progress = 1.0
        job.message = f"完成（合併 {len(saved)} 份）"
        job.result_path = out_path
        job.result_filename = out_name

    job = job_manager.submit("pdf-merge", run, meta={"count": len(saved)})
    return {"job_id": job.id}
