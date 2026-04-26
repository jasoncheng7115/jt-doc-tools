from __future__ import annotations
import time
import uuid
import zipfile
from pathlib import Path
from typing import List
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from ...config import settings
from ...core.job_manager import job_manager
from ...core import office_convert

router = APIRouter()

ACCEPT = ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.odp,.rtf,.csv,.txt"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("office_to_pdf.html", {"request": request, "accept": ACCEPT})


@router.post("/submit")
async def submit(file: List[UploadFile] = File(...)):
    files = file or []
    if not files: raise HTTPException(400, "沒有檔案")
    bid = uuid.uuid4().hex
    bdir = settings.temp_dir / f"o2p_{bid}"; bdir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str, bool]] = []   # (path, orig_name, is_pdf)
    for i, f in enumerate(files):
        name = f.filename or ""
        is_pdf = name.lower().endswith(".pdf")
        if not is_pdf and not office_convert.is_office_file(name):
            raise HTTPException(400, f"不支援的檔案格式：{name}")
        data = await f.read()
        if not data: raise HTTPException(400, f"空檔：{name}")
        sp = bdir / f"{i:03d}_{Path(name).name}"; sp.write_bytes(data)
        saved.append((sp, name, is_pdf))

    def run(job):
        outs: list[Path] = []
        for fi, (sp, orig, is_pdf) in enumerate(saved):
            job.message = f"處理 {orig}"; job.progress = (fi/len(saved)) * 0.95
            op = bdir / f"{Path(orig).stem}.pdf"
            if is_pdf:
                # Already a PDF — pass through. Convenient for mixed batches.
                import shutil as _sh
                _sh.copyfile(str(sp), str(op))
            else:
                office_convert.convert_to_pdf(sp, op)
            outs.append(op)
        if len(outs) == 1:
            job.result_path = outs[0]; job.result_filename = outs[0].name
        else:
            zname = f"converted_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = bdir / zname
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in outs: zf.write(p, arcname=p.name)
            job.result_path = zp; job.result_filename = zname
        job.progress = 1.0; job.message = f"完成（{len(outs)} 份）"

    job = job_manager.submit("office-to-pdf", run, meta={"count": len(saved)})
    return {"job_id": job.id}
