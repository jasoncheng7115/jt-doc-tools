from __future__ import annotations

import json
import re
import time
import uuid
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core.asset_manager import asset_manager
from ...core.job_manager import job_manager
from ...core import pdf_preview
from . import service

router = APIRouter()


def _eligible_assets():
    # Only true 浮水印 assets — stamps / signatures / logos are excluded so
    # users don't accidentally print a stamp as a tiled watermark.
    return asset_manager.list(type="watermark")


# ---- 個人臨時資產（與 pdf-stamp 相同模式，v1.4.11） ----
# UI 端傳 asset_id == "__temp__" + multipart 內帶 temp_asset_file 時，
# 把上傳檔案落地到 temp_dir 用一次後丟。圖只放在使用者瀏覽器 sessionStorage，
# server 不長存；audit 寫一筆 `temp_asset_used`/pdf-watermark。
_TEMP_ASSET_SENTINEL = "__temp__"
_TEMP_ASSET_MAX_BYTES = 5 * 1024 * 1024
_TEMP_ASSET_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}


async def _resolve_watermark_source(
    asset_id: Optional[str],
    temp_asset_file: Optional[UploadFile],
    request: Optional[Request] = None,
    actor_username: str = "",
) -> Optional[Path]:
    """Return the watermark image path, or None if the caller is using TEXT
    watermark (no image needed). Raises HTTPException(400) on bad input."""
    if asset_id == _TEMP_ASSET_SENTINEL:
        if not temp_asset_file:
            raise HTTPException(400, "temp asset selected but no file uploaded")
        fname = (temp_asset_file.filename or "").strip()
        ext = Path(fname).suffix.lower()
        if ext and ext not in _TEMP_ASSET_ALLOWED_EXT:
            raise HTTPException(400, f"unsupported temp asset extension: {ext}")
        data = await temp_asset_file.read()
        if not data:
            raise HTTPException(400, "empty temp asset")
        if len(data) > _TEMP_ASSET_MAX_BYTES:
            raise HTTPException(
                400, f"temp asset too large: {len(data)/1024/1024:.1f} MB > 5 MB")
        try:
            from PIL import Image as _PILImage
            from io import BytesIO as _BytesIO
            with _PILImage.open(_BytesIO(data)) as im:
                im.verify()
        except Exception as e:
            raise HTTPException(400, f"temp asset is not a valid image: {e}")
        out = settings.temp_dir / f"wm_temp_{uuid.uuid4().hex}{ext or '.png'}"
        out.write_bytes(data)
        # Audit (best-effort)
        try:
            from ...core import audit_db as _audit
            from ...core import client_ip as _cip
            import hashlib as _hl
            ip = ""
            if request is not None:
                ip = _cip.real_client_ip(request)
            _audit.log_event(
                event_type="temp_asset_used",
                username=actor_username or "",
                ip=ip,
                target="pdf-watermark",
                details={
                    "filename": fname or "(unnamed)",
                    "size_bytes": len(data),
                    "sha256_8": _hl.sha256(data).hexdigest()[:16],
                    "tool": "pdf-watermark",
                },
            )
        except Exception:
            import logging as _lg
            _lg.getLogger(__name__).debug(
                "temp_asset_used audit write failed", exc_info=True)
        return out
    # Normal asset path
    if not asset_id:
        return None  # caller may be using text watermark
    asset = asset_manager.get(asset_id)
    if not asset:
        raise HTTPException(400, "asset not found")
    return asset_manager.file_path(asset)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    items = _eligible_assets()
    default = asset_manager.get_default("watermark") or (items[0] if items else None)
    return templates.TemplateResponse(request, 
        "pdf_watermark.html",
        {
            "request": request,
            "assets": [a.to_dict() for a in items],
            "default_id": default.id if default else None,
        },
    )


def _parse_params(payload: str) -> service.WatermarkParams:
    try:
        d = json.loads(payload)
    except Exception:
        raise HTTPException(400, "params 格式錯誤")
    p = service.WatermarkParams(
        mode=str(d.get("mode") or "tile"),
        opacity=max(0.05, min(1.0, float(d.get("opacity", 0.25)))),
        rotation_deg=float(d.get("rotation_deg", 30.0)),
        x_mm=float(d.get("x_mm", 80.0)),
        y_mm=float(d.get("y_mm", 130.0)),
        width_mm=float(d.get("width_mm", 50.0)),
        height_mm=float(d.get("height_mm", 50.0)),
        tile_size_mm=float(d.get("tile_size_mm", d.get("tile_w_mm", 60.0))),
        tile_w_mm=float(d.get("tile_w_mm", 0.0)),
        tile_h_mm=float(d.get("tile_h_mm", 0.0)),
        gap_mm=max(0.0, float(d.get("gap_mm", 30.0))),
        text=str(d.get("text") or ""),
        text_color=str(d.get("text_color") or "#cc0000"),
        text_size_pt=float(d.get("text_size_pt", 48.0)),
        text_bold=bool(d.get("text_bold")),
        text_italic=bool(d.get("text_italic")),
        text_underline=bool(d.get("text_underline")),
    )
    if p.mode not in ("tile", "single"):
        p.mode = "tile"
    return p


@router.post("/preview")
async def preview(request: Request, file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)  # owner can fetch their own preview (#28)
    src = settings.temp_dir / f"wm_{upload_id}.pdf"
    src.write_bytes(data)
    png = settings.temp_dir / f"wm_{upload_id}_p1.png"
    pdf_preview.render_page_png(src, png, 0, dpi=110)

    import fitz
    with fitz.open(str(src)) as doc:
        r = doc[0].rect
        from ...core.unit_convert import pt_to_mm
        w_mm = pt_to_mm(r.width); h_mm = pt_to_mm(r.height)
        page_count = doc.page_count  # read inside the with-block; doc is closed on exit
    return {
        "upload_id": upload_id,
        "preview_url": f"/tools/pdf-watermark/preview/{png.name}",
        "paper_w_mm": round(w_mm, 2),
        "paper_h_mm": round(h_mm, 2),
        "page_count": page_count,
    }


@router.post("/preview-watermarked")
async def preview_watermarked(
    request: Request,
    file: UploadFile = File(...),
    params: str = Form(...),
    asset_id: Optional[str] = Form(None),
    temp_asset_file: Optional[UploadFile] = File(None),
    page: int = Form(0),
):
    p = _parse_params(params)
    wm_path: Optional[Path] = None
    if not (p.text and p.text.strip()):
        if not asset_id:
            raise HTTPException(400, "需要 asset_id 或 text")
        from ...core import sessions as _sessions
        actor = _sessions.user_label(getattr(request.state, "user", None))
        wm_path = await _resolve_watermark_source(
            asset_id, temp_asset_file, request=request, actor_username=actor)
        if wm_path is None:
            raise HTTPException(400, "asset not found")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)  # owner can fetch their own result (#28)
    src = settings.temp_dir / f"wm_{upload_id}_in.pdf"
    out = settings.temp_dir / f"wm_{upload_id}_marked.pdf"
    png = settings.temp_dir / f"wm_{upload_id}_preview.png"
    src.write_bytes(data)

    import fitz
    with fitz.open(str(src)) as doc:
        page_count = doc.page_count
    # Clamp the requested page so the editor's page nav can't go out of range.
    page = max(0, min(int(page), page_count - 1))

    p.pages = [page]
    service.apply_watermark(src, out, wm_path, p)
    pdf_preview.render_page_png(out, png, page, dpi=120)

    for f in (src, out):
        try: f.unlink()
        except OSError: pass

    return {
        "preview_url": f"/tools/pdf-watermark/preview/{png.name}",
        "page_count": page_count,
        "page": page,
    }


def _build_run(batch_dir: Path, saved: "list[tuple[Path, str]]",
               base_params, page_mode: str, wm_png: Optional[Path],
               asset_id: Optional[str], actor: str):
    """產生 job 的 run(job) closure。/submit（單發）與 /batch/process（逐檔
    累積）共用同一套處理 + 打包 ZIP 邏輯。"""
    def run(job):
        total = len(saved)
        results: list[tuple[Path, str]] = []
        import fitz
        for i, (sp, orig) in enumerate(saved):
            job.message = f"處理第 {i + 1}/{total} 份：{orig}"
            job.progress = (i / max(1, total)) * 0.95
            pages: Optional[list[int]] = None
            if page_mode != "all":
                with fitz.open(str(sp)) as d:
                    n = d.page_count
                pages = [0] if page_mode == "first" else [max(0, n - 1)]
            local = service.WatermarkParams(**{**base_params.__dict__, "pages": pages})
            dst = batch_dir / f"{sp.stem}_watermarked.pdf"
            service.apply_watermark(sp, dst, wm_png, local)
            results.append((dst, _result_filename(orig)))

        if len(results) == 1:
            result_path, result_name = results[0]
        else:
            zip_name = f"watermarked_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = batch_dir / zip_name
            used: dict[str, int] = {}
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for dst, name in results:
                    k = used.get(name, 0) + 1
                    used[name] = k
                    arc = name if k == 1 else f"{Path(name).stem}_{k}{Path(name).suffix}"
                    zf.write(dst, arcname=arc)
            result_path = zp; result_name = zip_name
        job.progress = 1.0
        job.message = f"完成（{total} 份）"
        job.result_path = result_path
        job.result_filename = result_name

        # ---- v1.1.0: archive into watermark_history ----
        try:
            from ...core.history_manager import watermark_history
            for sp, orig_name in saved:
                stem = Path(sp).stem
                dst = batch_dir / f"{stem}_watermarked.pdf"
                if dst.exists():
                    watermark_history.save(
                        original_path=sp,
                        filled_path=dst,
                        preview_path=None,
                        original_filename=orig_name,
                        username=actor or "",
                        extra={"asset_id": asset_id, "page_mode": page_mode},
                    )
        except Exception:
            import logging as _lg
            _lg.getLogger(__name__).exception("watermark_history.save failed")
    return run


@router.post("/submit")
async def submit(
    request: Request,
    file: List[UploadFile] = File(...),
    params: str = Form(...),
    page_mode: str = Form("all"),
    asset_id: Optional[str] = Form(None),
    temp_asset_file: Optional[UploadFile] = File(None),
):
    base_params = _parse_params(params)
    # Capture actor up top so the background job's history.save can attribute
    # the entry to the right user. v1.4.43 bug fix: previously only captured
    # in the asset-mode branch, so text-mode watermarks always logged
    # username="" → 歷史記錄全變「(匿名)」.
    from ...core import sessions as _sessions
    actor = _sessions.user_label(getattr(request.state, "user", None))
    wm_png: Optional[Path] = None
    if not (base_params.text and base_params.text.strip()):
        if not asset_id:
            raise HTTPException(400, "需要 asset_id 或 text")
        wm_png = await _resolve_watermark_source(
            asset_id, temp_asset_file, request=request, actor_username=actor)
        if wm_png is None:
            raise HTTPException(400, "asset not found")
    files = file or []
    if not files:
        raise HTTPException(400, "沒有檔案")
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")

    batch_id = uuid.uuid4().hex
    batch_dir = settings.temp_dir / f"wm_batch_{batch_id}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        data = await f.read()
        if not data:
            raise HTTPException(400, f"空檔：{f.filename}")
        safe = Path(f.filename).name or f"input_{i}.pdf"
        sp = batch_dir / f"{i:03d}_{safe}"
        sp.write_bytes(data)
        saved.append((sp, safe))

    job = job_manager.submit(
        "pdf-watermark",
        _build_run(batch_dir, saved, base_params, page_mode, wm_png, asset_id, actor),
        meta={"asset_id": asset_id, "count": len(saved)},
    )
    return {"job_id": job.id}


# ===== 逐檔順序上傳（issue #27）=====
# 大批次（數十~上百份）一次塞進單一 multipart 會撞反向代理 / 伺服器的 body
# 上限而失敗卻無任何提示。改成：create → 逐檔 add（每請求只一個小 PDF）→ process。
# 每個請求 body 都很小，永遠不撞上限，也不靠使用者改 proxy。
_BATCH_RE = re.compile(r"^[a-f0-9]{32}$")


def _batch_dir(batch_id: str) -> Path:
    if not _BATCH_RE.match(batch_id or ""):
        raise HTTPException(400, "invalid batch_id")
    return settings.temp_dir / f"wm_batch_{batch_id}"


@router.post("/batch/create")
async def batch_create(
    request: Request,
    params: str = Form(...),
    page_mode: str = Form("all"),
    asset_id: Optional[str] = Form(None),
    temp_asset_file: Optional[UploadFile] = File(None),
):
    base_params = _parse_params(params)
    from ...core import sessions as _sessions
    actor = _sessions.user_label(getattr(request.state, "user", None))
    wm_name = None
    if not (base_params.text and base_params.text.strip()):
        if not asset_id:
            raise HTTPException(400, "需要 asset_id 或 text")
        wm_png = await _resolve_watermark_source(
            asset_id, temp_asset_file, request=request, actor_username=actor)
        if wm_png is None:
            raise HTTPException(400, "asset not found")
    batch_id = uuid.uuid4().hex
    bdir = settings.temp_dir / f"wm_batch_{batch_id}"
    bdir.mkdir(parents=True, exist_ok=True)
    # 浮水印來源圖搬進 batch 目錄，避免暫存清理把它清掉
    if not (base_params.text and base_params.text.strip()):
        wm_dst = bdir / "_wm.png"
        try:
            wm_dst.write_bytes(Path(wm_png).read_bytes())
            wm_name = wm_dst.name
        except Exception:
            raise HTTPException(400, "watermark source unavailable")
    meta = {
        "params": params, "page_mode": page_mode, "asset_id": asset_id,
        "actor": actor, "wm_name": wm_name,
    }
    (bdir / "_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    try:
        from ...core import upload_owner
        upload_owner.record(batch_id, request)
    except Exception:
        pass
    return {"batch_id": batch_id}


@router.post("/batch/{batch_id}/add")
async def batch_add(
    batch_id: str, request: Request,
    file: UploadFile = File(...), index: int = Form(...),
):
    bdir = _batch_dir(batch_id)
    if not bdir.is_dir():
        raise HTTPException(410, "batch 已過期，請重新上傳")
    from ...core import upload_owner
    upload_owner.require(batch_id, request)
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, f"只支援 PDF：{file.filename}")
    data = await file.read()
    if not data:
        raise HTTPException(400, f"空檔：{file.filename}")
    if index < 0 or index > 9998:
        raise HTTPException(400, "index 超出範圍")
    safe = Path(file.filename).name or f"input_{index}.pdf"
    sp = bdir / f"{index:03d}_{safe}"
    sp.write_bytes(data)
    return {"ok": True, "saved": sp.name}


@router.post("/batch/{batch_id}/process")
async def batch_process(batch_id: str, request: Request):
    bdir = _batch_dir(batch_id)
    if not bdir.is_dir():
        raise HTTPException(410, "batch 已過期，請重新上傳")
    from ...core import upload_owner
    upload_owner.require(batch_id, request)
    try:
        meta = json.loads((bdir / "_meta.json").read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(400, "batch meta 毀損，請重新上傳")
    base_params = _parse_params(meta.get("params") or "{}")
    page_mode = meta.get("page_mode") or "all"
    asset_id = meta.get("asset_id")
    actor = meta.get("actor") or ""
    wm_png = (bdir / meta["wm_name"]) if meta.get("wm_name") else None
    # 收集已上傳的輸入 PDF（依數字前綴排序；排除浮水印圖 / 產出檔）
    inputs = sorted(
        [p for p in bdir.glob("*.pdf")
         if re.match(r"^\d{3}_", p.name) and not p.name.endswith("_watermarked.pdf")],
        key=lambda p: int(re.match(r"^(\d+)_", p.name).group(1)),
    )
    if not inputs:
        raise HTTPException(400, "沒有已上傳的檔案")
    saved = [(p, re.sub(r"^\d+_", "", p.name)) for p in inputs]
    job = job_manager.submit(
        "pdf-watermark",
        _build_run(bdir, saved, base_params, page_mode, wm_png, asset_id, actor),
        meta={"asset_id": asset_id, "count": len(saved)},
    )
    return {"job_id": job.id}


@router.get("/preview/{name}")
async def serve_preview(name: str, request: Request):
    from app.core.safe_paths import safe_join
    from ...core import upload_owner
    p = safe_join(settings.temp_dir, name)
    # Watermark temp files are named `wm_<uid>_...`; strip the `wm_` prefix so
    # the uuid prefix is recognised (otherwise extract_upload_id sees "wm" and
    # the ACL silently no-ops, leaking previews across users).
    base = name[3:] if name.startswith("wm_") else name
    uid = upload_owner.extract_upload_id(base)
    if uid:
        upload_owner.require(uid, request)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type="image/png")


@router.get("/text-png")
async def text_png(
    text: str,
    color: str = "#cc0000",
    size: float = 48.0,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
):
    """Render the given text to a transparent PNG. Used by the position
    editor in single mode when source=text — the editor needs an image to
    display as the draggable element."""
    if not text.strip():
        raise HTTPException(400, "text required")
    from fastapi.responses import Response
    png_bytes, _w_mm, _h_mm = service._render_text_png(
        text, color, float(size), "",
        bold=bold, italic=italic, underline=underline,
    )
    return Response(
        content=png_bytes, media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


def _result_filename(orig: str) -> str:
    return f"{Path(orig).stem}_watermarked.pdf"


# ---- 對外 API：單次 upload + 文字浮水印 + 直接回 PDF ----
@router.post("/api/pdf-watermark", include_in_schema=True)
async def api_pdf_watermark(
    request: Request,
    file: UploadFile = File(...),
    text: str = Form(...),
    opacity: float = Form(0.25),
    rotation_deg: float = Form(30.0),
    mode: str = Form("tile"),
    text_color: str = Form("#cc0000"),
    text_size_pt: float = Form(48.0),
):
    """單次上傳 PDF + 文字浮水印，回 PDF。mode: tile（鋪滿）/ single（單點）。"""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    if not (text or "").strip():
        raise HTTPException(400, "text 必填")
    data = await file.read()
    if not data or data[:4] != b"%PDF":
        raise HTTPException(400, "不是有效的 PDF")
    uid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(uid, request)
    src = settings.temp_dir / f"wm_api_{uid}_in.pdf"
    out = settings.temp_dir / f"wm_api_{uid}_out.pdf"
    src.write_bytes(data)
    stem = Path(file.filename or "document.pdf").stem
    params = service.WatermarkParams(
        mode=("single" if mode == "single" else "tile"),
        opacity=max(0.05, min(1.0, float(opacity))),
        rotation_deg=float(rotation_deg),
        text=text,
        text_color=text_color,
        text_size_pt=float(text_size_pt),
    )
    import asyncio as _asyncio
    await _asyncio.to_thread(service.apply_watermark, src, out, None, params)
    return FileResponse(str(out), media_type="application/pdf",
                        filename=f"{stem}_watermarked.pdf")
