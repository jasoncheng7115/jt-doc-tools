from __future__ import annotations
import re
import time
import uuid
import zipfile
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
    return templates.TemplateResponse("pdf_split.html", {"request": request})


def _parse_ranges(text: str, page_count: int) -> list[list[int]]:
    """'1-3,5,7-' → [[0,1,2],[4],[6,7,...page_count-1]]. 1-based input."""
    out: list[list[int]] = []
    for chunk in re.split(r"[,，;；\s]+", text.strip()):
        if not chunk:
            continue
        m = re.match(r"^(\d+)?\s*-\s*(\d+)?$", chunk)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else page_count
            a = max(1, min(page_count, a))
            b = max(1, min(page_count, b))
            if a > b: a, b = b, a
            out.append(list(range(a - 1, b)))
        elif chunk.isdigit():
            n = max(1, min(page_count, int(chunk)))
            out.append([n - 1])
        else:
            raise HTTPException(400, f"範圍語法錯誤：{chunk}")
    return out


@router.post("/submit")
async def submit(
    file: List[UploadFile] = File(...),
    mode: str = Form("each"),  # each | ranges
    ranges: str = Form(""),
):
    files = file or []
    if not files:
        raise HTTPException(400, "沒有檔案")
    bid = uuid.uuid4().hex
    bdir = settings.temp_dir / f"split_{bid}"
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
        for fi, (sp, orig) in enumerate(saved):
            stem = Path(orig).stem
            with fitz.open(str(sp)) as src:
                n = src.page_count
                if mode == "each":
                    parts = [[i] for i in range(n)]
                else:
                    parts = _parse_ranges(ranges or "", n)
                    if not parts:
                        parts = [[i] for i in range(n)]
                for pi, page_indices in enumerate(parts):
                    job.message = f"切割 {orig} 第 {pi + 1}/{len(parts)} 段"
                    job.progress = ((fi + (pi + 1) / max(1, len(parts))) / max(1, len(saved))) * 0.95
                    out = fitz.open()
                    for p in page_indices:
                        out.insert_pdf(src, from_page=p, to_page=p)
                    label = f"p{page_indices[0] + 1}" if len(page_indices) == 1 else f"p{page_indices[0] + 1}-{page_indices[-1] + 1}"
                    op = bdir / f"{stem}_{label}.pdf"
                    out.save(str(op), garbage=3, deflate=True)
                    out.close()
                    outs.append(op)
        if len(outs) == 1:
            job.result_path = outs[0]; job.result_filename = outs[0].name
        else:
            zname = f"split_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = bdir / zname
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in outs:
                    zf.write(p, arcname=p.name)
            job.result_path = zp; job.result_filename = zname
        job.progress = 1.0; job.message = f"完成（{len(outs)} 份）"

    job = job_manager.submit("pdf-split", run, meta={"count": len(saved)})
    return {"job_id": job.id}
