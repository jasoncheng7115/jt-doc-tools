from __future__ import annotations
import re
import time
import uuid
import zipfile
from pathlib import Path
from typing import List
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
import fitz
from ...config import settings
from ...core.job_manager import job_manager
from ...core import pdf_preview

router = APIRouter()


def _parse_order(text: str, n: int) -> list[int]:
    """'2,1,3-5,7' → [1,0,2,3,4,6] (0-based)."""
    out: list[int] = []
    for chunk in re.split(r"[,，;；\s]+", text.strip()):
        if not chunk: continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", chunk)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            step = 1 if b >= a else -1
            for v in range(a, b + step, step):
                if 1 <= v <= n: out.append(v - 1)
        elif chunk.isdigit():
            v = int(chunk)
            if 1 <= v <= n: out.append(v - 1)
        else:
            raise HTTPException(400, f"頁序語法錯誤：{chunk}")
    return out


def _parse_drop(text: str, n: int) -> set[int]:
    if not text.strip(): return set()
    out: set[int] = set()
    for chunk in re.split(r"[,，;；\s]+", text.strip()):
        if not chunk: continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", chunk)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            for v in range(min(a,b), max(a,b)+1):
                if 1 <= v <= n: out.add(v - 1)
        elif chunk.isdigit():
            v = int(chunk)
            if 1 <= v <= n: out.add(v - 1)
        else:
            raise HTTPException(400, f"刪除語法錯誤：{chunk}")
    return out


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_pages.html", {"request": request})


@router.post("/load")
async def load(request: Request, file: UploadFile = File(...)):
    """Stash the upload + return page count and thumbnail URLs."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data: raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
    src = settings.temp_dir / f"pgL_{upload_id}.pdf"
    src.write_bytes(data)
    with fitz.open(str(src)) as doc:
        n = doc.page_count
    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "page_count": n,
        "pages": [
            {"page": i + 1, "thumb": f"/tools/pdf-pages/thumb/{upload_id}/{i + 1}"}
            for i in range(n)
        ],
    }


@router.get("/thumb/{upload_id}/{page}")
async def thumb(upload_id: str, page: int, request: Request, large: bool = False):
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = settings.temp_dir / f"pgL_{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(404, "upload not found (expired?)")
    suffix = "_large" if large else ""
    out = settings.temp_dir / f"pgL_{upload_id}_thumb{suffix}_{page}.png"
    if not out.exists():
        pdf_preview.render_page_png(src, out, page - 1, dpi=160 if large else 64)
    return FileResponse(str(out), media_type="image/png",
                        headers={"Cache-Control": "max-age=300"})


@router.post("/submit-from-upload")
async def submit_from_upload(
    request: Request,
    upload_id: str = Form(...),
    order: str = Form(...),       # comma-separated 1-based page numbers
    filename: str = Form(""),
):
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = settings.temp_dir / f"pgL_{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(404, "upload not found (expired?)")
    order_list = [int(x) - 1 for x in order.split(",") if x.strip().isdigit()]
    if not order_list:
        raise HTTPException(400, "結果頁數為 0")

    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
    bdir = settings.temp_dir / f"pg_{bid}"; bdir.mkdir(parents=True, exist_ok=True)
    stem = Path(filename or src.name).stem
    out_name = f"{stem}_pages.pdf"
    out_path = bdir / out_name

    def run(job):
        with fitz.open(str(src)) as s:
            n = s.page_count
            valid = [i for i in order_list if 0 <= i < n]
            if not valid:
                raise HTTPException(400, "結果頁數為 0")
            out = fitz.open()
            for i in valid:
                job.message = f"處理第 {i + 1} 頁"
                out.insert_pdf(s, from_page=i, to_page=i)
            out.save(str(out_path), garbage=3, deflate=True); out.close()
        job.progress = 1.0; job.message = f"完成（{len(valid)} 頁）"
        job.result_path = out_path; job.result_filename = out_name

    job = job_manager.submit("pdf-pages", run, meta={"upload_id": upload_id})
    return {"job_id": job.id}


@router.post("/submit")
async def submit(
    request: Request,
    file: List[UploadFile] = File(...),
    mode: str = Form("reorder"),  # reorder | drop
    spec: str = Form(""),
):
    files = file or []
    if not files: raise HTTPException(400, "沒有檔案")
    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
    bdir = settings.temp_dir / f"pg_{bid}"; bdir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")
        data = await f.read()
        if not data: raise HTTPException(400, f"空檔：{f.filename}")
        sp = bdir / f"{i:03d}_{Path(f.filename).name}"; sp.write_bytes(data)
        saved.append((sp, f.filename))

    def run(job):
        outs: list[Path] = []
        for fi, (sp, orig) in enumerate(saved):
            job.message = f"處理 {orig}"; job.progress = (fi/len(saved)) * 0.95
            with fitz.open(str(sp)) as src:
                n = src.page_count
                if mode == "reorder":
                    order = _parse_order(spec, n) if spec.strip() else list(range(n))
                else:
                    drop = _parse_drop(spec, n)
                    order = [i for i in range(n) if i not in drop]
                if not order:
                    raise HTTPException(400, "結果頁數為 0")
                out = fitz.open()
                for i in order:
                    out.insert_pdf(src, from_page=i, to_page=i)
                op = bdir / f"{Path(orig).stem}_pages.pdf"
                out.save(str(op), garbage=3, deflate=True); out.close()
                outs.append(op)
        if len(outs) == 1:
            job.result_path = outs[0]; job.result_filename = outs[0].name
        else:
            zname = f"pages_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = bdir / zname
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in outs: zf.write(p, arcname=p.name)
            job.result_path = zp; job.result_filename = zname
        job.progress = 1.0; job.message = f"完成（{len(outs)} 份）"

    job = job_manager.submit("pdf-pages", run, meta={"count": len(saved)})
    return {"job_id": job.id}
