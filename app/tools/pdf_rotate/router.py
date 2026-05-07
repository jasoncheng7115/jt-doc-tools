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
async def load(request: Request, file: UploadFile = File(...)):
    """Stash the upload + return page count and thumbnail URLs (single file)."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data: raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
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


_VALID_MODES = {"rotate-90", "rotate-180", "rotate-270", "flip-h", "flip-v"}


@router.get("/thumb/{upload_id}/{page}")
async def thumb(upload_id: str, page: int, large: bool = False, mode: str = ""):
    src = settings.temp_dir / f"rotL_{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(404, "upload not found (expired?)")
    suffix = "_large" if large else ""
    base = settings.temp_dir / f"rotL_{upload_id}_thumb{suffix}_{page}.png"
    if not base.exists():
        pdf_preview.render_page_png(src, base, page - 1, dpi=160 if large else 64)
    mode = mode if mode in _VALID_MODES else ""
    if not mode:
        return FileResponse(str(base), media_type="image/png",
                            headers={"Cache-Control": "max-age=300"})
    out = settings.temp_dir / f"rotL_{upload_id}_thumb{suffix}_{mode}_{page}.png"
    if not out.exists():
        from PIL import Image
        with Image.open(str(base)) as im:
            if mode == "rotate-90":      # clockwise 90
                im2 = im.transpose(Image.Transpose.ROTATE_270)
            elif mode == "rotate-180":
                im2 = im.transpose(Image.Transpose.ROTATE_180)
            elif mode == "rotate-270":   # counter-clockwise 90
                im2 = im.transpose(Image.Transpose.ROTATE_90)
            elif mode == "flip-h":
                im2 = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            elif mode == "flip-v":
                im2 = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            else:
                im2 = im
            im2.save(str(out))
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
    request: Request,
    file: List[UploadFile] = File(...),
    mode: str = Form("rotate-90"),   # rotate-90/180/270, flip-h, flip-v
    pages: str = Form("all"),
    # Legacy: older clients may still send `angle`. Kept for back-compat.
    angle: int | None = Form(None),
    # JSON object {pageNum(str): mode|"none"} for per-page override.
    # Empty / missing => 走 mode + pages 全頁套用語意（既有行為）。
    per_page: str = Form(""),
):
    # Back-compat: translate legacy angle param.
    if angle is not None and mode.startswith("rotate-"):
        mode = f"rotate-{angle}"
    valid = {"rotate-90", "rotate-180", "rotate-270", "flip-h", "flip-v"}
    if mode not in valid:
        raise HTTPException(400, f"mode 必須是 {valid}")
    per_page_map: dict[int, str] = {}
    if per_page:
        import json as _json
        try:
            raw = _json.loads(per_page)
            if not isinstance(raw, dict):
                raise ValueError("per_page must be JSON object")
            for k, v in raw.items():
                pn = int(k)
                vs = str(v)
                if vs != "none" and vs not in valid:
                    raise ValueError(f"per_page mode 無效：{vs}")
                per_page_map[pn] = vs
        except Exception as e:
            raise HTTPException(400, f"per_page 解析失敗：{e}")
    files = file or []
    if not files: raise HTTPException(400, "沒有檔案")
    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
    bdir = settings.temp_dir / f"rot_{bid}"; bdir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")
        data = await f.read()
        if not data: raise HTTPException(400, f"空檔：{f.filename}")
        sp = bdir / f"{i:03d}_{Path(f.filename).name}"
        sp.write_bytes(data); saved.append((sp, f.filename))

    def _apply_mode_to_page(page, m: str) -> None:
        if m == "none" or not m:
            return
        if m.startswith("rotate-"):
            ang = int(m.split("-")[1])
            page.set_rotation((page.rotation + ang) % 360)
        elif m == "flip-h":
            _flip_page(page, horizontal=True)
        elif m == "flip-v":
            _flip_page(page, horizontal=False)

    def run(job):
        outs: list[Path] = []
        any_flip = mode.startswith("flip-") or any(
            v.startswith("flip-") for v in per_page_map.values())
        any_rotate = mode.startswith("rotate-") or any(
            v.startswith("rotate-") for v in per_page_map.values())
        # 命名 suffix：純 flip → flipped；其他 → rotated（含混合）
        if any_rotate or not any_flip:
            suffix = "rotated"
        else:
            suffix = "flipped"
        for fi, (sp, orig) in enumerate(saved):
            job.message = f"處理 {orig}"; job.progress = (fi/len(saved)) * 0.95
            with fitz.open(str(sp)) as doc:
                target = _parse_pages(pages, doc.page_count)
                for i in range(doc.page_count):
                    page_num_1based = i + 1
                    # per-page 覆寫優先（含 'none' = 此頁明確不轉）
                    if page_num_1based in per_page_map:
                        _apply_mode_to_page(doc[i], per_page_map[page_num_1based])
                    elif i in target:
                        _apply_mode_to_page(doc[i], mode)
                op = bdir / f"{Path(orig).stem}_{suffix}.pdf"
                doc.save(str(op), garbage=3, deflate=True)
                outs.append(op)
        if len(outs) == 1:
            job.result_path = outs[0]; job.result_filename = outs[0].name
        else:
            prefix = suffix
            zname = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = bdir / zname
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in outs: zf.write(p, arcname=p.name)
            job.result_path = zp; job.result_filename = zname
        job.progress = 1.0; job.message = f"完成（{len(outs)} 份）"

    job = job_manager.submit("pdf-rotate", run,
                             meta={"count": len(saved), "mode": mode})
    return {"job_id": job.id}


# ----------------------------------------------------------------------
# v1.4.53 redesigned UX: synchronous /finalize and /finalize-png that
# operate on the file already stashed by /load. Replaces the multi-step
# job-based /submit flow for the new UI (no batch — single file).
# ----------------------------------------------------------------------

def _apply_perpage(doc: "fitz.Document", per_page_map: dict[int, str]) -> None:
    """Apply per-page mode override to the document in-place."""
    for page_num_1based, m in per_page_map.items():
        if m == "none" or not m:
            continue
        idx = page_num_1based - 1
        if not (0 <= idx < doc.page_count):
            continue
        page = doc[idx]
        if m.startswith("rotate-"):
            ang = int(m.split("-")[1])
            page.set_rotation((page.rotation + ang) % 360)
        elif m == "flip-h":
            _flip_page(page, horizontal=True)
        elif m == "flip-v":
            _flip_page(page, horizontal=False)


def _parse_per_page(per_page: str) -> dict[int, str]:
    if not per_page:
        return {}
    import json as _json
    try:
        raw = _json.loads(per_page)
        if not isinstance(raw, dict):
            raise ValueError("per_page must be JSON object")
        out: dict[int, str] = {}
        for k, v in raw.items():
            pn = int(k)
            vs = str(v)
            if vs != "none" and vs not in _VALID_MODES:
                raise ValueError(f"per_page mode 無效：{vs}")
            out[pn] = vs
        return out
    except Exception as e:
        raise HTTPException(400, f"per_page 解析失敗：{e}")


_UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{32}$")


@router.post("/finalize")
async def finalize(
    upload_id: str = Form(...),
    per_page: str = Form(""),
):
    """Build the rotated PDF from the file stashed by /load + per-page map."""
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        raise HTTPException(400, "invalid upload_id")
    src = settings.temp_dir / f"rotL_{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(410, "上傳已過期，請重新上傳")
    per_page_map = _parse_per_page(per_page)
    out = settings.temp_dir / f"rotL_{upload_id}_rotated.pdf"
    with fitz.open(str(src)) as doc:
        _apply_perpage(doc, per_page_map)
        doc.save(str(out), garbage=3, deflate=True)
    from ...core.http_utils import content_disposition
    download_name = "rotated.pdf"
    return FileResponse(
        str(out), media_type="application/pdf",
        filename=download_name,
        headers={"Content-Disposition": content_disposition(download_name)},
    )


@router.post("/finalize-png")
async def finalize_png(
    upload_id: str = Form(...),
    per_page: str = Form(""),
):
    """Bake rotation, render each page to PNG, return ZIP."""
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        raise HTTPException(400, "invalid upload_id")
    src = settings.temp_dir / f"rotL_{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(410, "上傳已過期，請重新上傳")
    per_page_map = _parse_per_page(per_page)
    # Bake rotation to a temp PDF, then render each page as PNG
    baked = settings.temp_dir / f"rotL_{upload_id}_baked_for_png.pdf"
    with fitz.open(str(src)) as doc:
        _apply_perpage(doc, per_page_map)
        doc.save(str(baked), garbage=3, deflate=True)
    zip_path = settings.temp_dir / f"rotL_{upload_id}_pages.zip"
    import zipfile as _zf
    with _zf.ZipFile(zip_path, "w", _zf.ZIP_DEFLATED) as zf:
        with fitz.open(str(baked)) as doc:
            for i in range(doc.page_count):
                png_path = settings.temp_dir / f"rotL_{upload_id}_pg_{i+1}.png"
                # 150 DPI is a reasonable balance for "看得清楚 + 檔不太大"
                pdf_preview.render_page_png(baked, png_path, i, dpi=150)
                zf.write(png_path, arcname=f"page_{i+1:03d}.png")
                try:
                    png_path.unlink()
                except Exception:
                    pass
    try:
        baked.unlink()
    except Exception:
        pass
    from ...core.http_utils import content_disposition
    download_name = f"rotated_pages_{int(time.time())}.zip"
    return FileResponse(
        str(zip_path), media_type="application/zip",
        filename=download_name,
        headers={"Content-Disposition": content_disposition(download_name)},
    )
