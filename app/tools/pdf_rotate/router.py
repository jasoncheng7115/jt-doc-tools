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


def _parse_pages(text: str, n: int) -> set[int]:
    out: set[int] = set()
    text = text.strip()
    if not text or text.lower() == "all":
        return set(range(n))
    for chunk in re.split(r"[,，;；\s]+", text):
        if not chunk: continue
        m = re.match(r"^(\d+)?\s*-\s*(\d+)?$", chunk)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else n
            for i in range(max(1, a), min(n, b) + 1): out.add(i - 1)
        elif chunk.isdigit():
            i = int(chunk);
            if 1 <= i <= n: out.add(i - 1)
        else:
            raise HTTPException(400, f"頁面語法錯誤：{chunk}")
    return out


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_rotate.html", {"request": request})


@router.post("/load")
async def load(file: UploadFile = File(...)):
    """Stash the upload + return page count and thumbnail URLs (single file)."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data: raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"rotL_{upload_id}.pdf"
    src.write_bytes(data)
    with fitz.open(str(src)) as doc:
        n = doc.page_count
    return {
        "upload_id": upload_id, "filename": file.filename, "page_count": n,
        "pages": [
            {"page": i + 1, "thumb": f"/tools/pdf-rotate/thumb/{upload_id}/{i + 1}"}
            for i in range(n)
        ],
    }


@router.get("/thumb/{upload_id}/{page}")
async def thumb(upload_id: str, page: int, large: bool = False):
    src = settings.temp_dir / f"rotL_{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(404, "upload not found (expired?)")
    suffix = "_large" if large else ""
    out = settings.temp_dir / f"rotL_{upload_id}_thumb{suffix}_{page}.png"
    if not out.exists():
        pdf_preview.render_page_png(src, out, page - 1, dpi=160 if large else 64)
    return FileResponse(str(out), media_type="image/png",
                        headers={"Cache-Control": "max-age=300"})


def _flip_page(page, *, horizontal: bool) -> None:
    """Flip a page horizontally or vertically by prepending a CTM to its
    content stream (preserves vector fidelity)."""
    w = page.rect.width
    h = page.rect.height
    page.wrap_contents()  # puts existing content inside q/Q
    contents = page.get_contents()
    if not contents:
        return
    xref = contents[0]
    old = page.parent.xref_stream(xref) or b""
    if horizontal:
        cm = f"-1 0 0 1 {w} 0 cm\n".encode()
    else:
        cm = f"1 0 0 -1 0 {h} cm\n".encode()
    # Inject the CTM right after the opening `q\n`.
    if old.startswith(b"q\n"):
        new = b"q\n" + cm + old[2:]
    elif old.startswith(b"q "):
        new = b"q " + cm + old[2:]
    else:
        # Fallback: wrap again explicitly.
        new = b"q\n" + cm + old + b"\nQ\n"
    page.parent.update_stream(xref, new)


@router.post("/submit")
async def submit(
    file: List[UploadFile] = File(...),
    mode: str = Form("rotate-90"),   # rotate-90/180/270, flip-h, flip-v
    pages: str = Form("all"),
    # Legacy: older clients may still send `angle`. Kept for back-compat.
    angle: int | None = Form(None),
):
    # Back-compat: translate legacy angle param.
    if angle is not None and mode.startswith("rotate-"):
        mode = f"rotate-{angle}"
    valid = {"rotate-90", "rotate-180", "rotate-270", "flip-h", "flip-v"}
    if mode not in valid:
        raise HTTPException(400, f"mode 必須是 {valid}")
    files = file or []
    if not files: raise HTTPException(400, "沒有檔案")
    bid = uuid.uuid4().hex
    bdir = settings.temp_dir / f"rot_{bid}"; bdir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")
        data = await f.read()
        if not data: raise HTTPException(400, f"空檔：{f.filename}")
        sp = bdir / f"{i:03d}_{Path(f.filename).name}"
        sp.write_bytes(data); saved.append((sp, f.filename))

    def run(job):
        outs: list[Path] = []
        for fi, (sp, orig) in enumerate(saved):
            job.message = f"處理 {orig}"; job.progress = (fi/len(saved)) * 0.95
            with fitz.open(str(sp)) as doc:
                target = _parse_pages(pages, doc.page_count)
                for i in range(doc.page_count):
                    if i not in target:
                        continue
                    if mode.startswith("rotate-"):
                        ang = int(mode.split("-")[1])
                        doc[i].set_rotation((doc[i].rotation + ang) % 360)
                    elif mode == "flip-h":
                        _flip_page(doc[i], horizontal=True)
                    elif mode == "flip-v":
                        _flip_page(doc[i], horizontal=False)
                suffix = "flipped" if mode.startswith("flip-") else "rotated"
                op = bdir / f"{Path(orig).stem}_{suffix}.pdf"
                doc.save(str(op), garbage=3, deflate=True)
                outs.append(op)
        if len(outs) == 1:
            job.result_path = outs[0]; job.result_filename = outs[0].name
        else:
            prefix = "flipped" if mode.startswith("flip-") else "rotated"
            zname = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = bdir / zname
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in outs: zf.write(p, arcname=p.name)
            job.result_path = zp; job.result_filename = zname
        job.progress = 1.0; job.message = f"完成（{len(outs)} 份）"

    job = job_manager.submit("pdf-rotate", run,
                             meta={"count": len(saved), "mode": mode})
    return {"job_id": job.id}
