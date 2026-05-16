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
    request: Request,
    file: List[UploadFile] = File(...),
    mode: str = Form("each"),  # each | ranges
    ranges: str = Form(""),
):
    files = file or []
    if not files:
        raise HTTPException(400, "沒有檔案")
    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
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


# ---- 對外 API：單次 upload + 切割 + 回 ZIP（或單一 PDF）----
from fastapi.responses import FileResponse as _FileResponse  # noqa: E402


@router.post("/api/pdf-split", include_in_schema=True)
async def api_pdf_split(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("each"),    # each | ranges
    ranges: str = Form(""),
):
    """單次上傳 PDF，依 mode 切割：each = 每頁一份；ranges = 依 ranges 切。
    多份結果回 ZIP，單份回 PDF。"""
    if mode not in ("each", "ranges"):
        raise HTTPException(400, "mode 必須是 each 或 ranges")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data or data[:4] != b"%PDF":
        raise HTTPException(400, "不是有效的 PDF")
    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
    bdir = settings.temp_dir / f"split_api_{bid}"
    bdir.mkdir(parents=True, exist_ok=True)
    sp = bdir / Path(file.filename).name
    sp.write_bytes(data)
    stem = Path(file.filename or "document").stem
    import asyncio as _asyncio
    def _do() -> tuple[Path, str, str]:
        outs: list[Path] = []
        with fitz.open(str(sp)) as src:
            n = src.page_count
            if mode == "each":
                parts = [[i] for i in range(n)]
            else:
                parts = _parse_ranges(ranges or "", n)
                if not parts:
                    parts = [[i] for i in range(n)]
            for page_indices in parts:
                out = fitz.open()
                for p in page_indices:
                    out.insert_pdf(src, from_page=p, to_page=p)
                label = (f"p{page_indices[0] + 1}" if len(page_indices) == 1
                         else f"p{page_indices[0] + 1}-{page_indices[-1] + 1}")
                op = bdir / f"{stem}_{label}.pdf"
                out.save(str(op), garbage=3, deflate=True)
                out.close()
                outs.append(op)
        if len(outs) == 1:
            return outs[0], outs[0].name, "application/pdf"
        zname = f"{stem}_split.zip"
        zp = bdir / zname
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in outs:
                zf.write(p, arcname=p.name)
        return zp, zname, "application/zip"
    result_path, result_name, media = await _asyncio.to_thread(_do)
    return _FileResponse(str(result_path), media_type=media,
                         filename=result_name)
