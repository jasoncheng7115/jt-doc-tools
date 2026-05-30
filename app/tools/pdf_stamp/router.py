from __future__ import annotations

import asyncio
import json
import time
import uuid
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...config import settings
from ...core.asset_manager import asset_manager
from ...core.job_manager import job_manager
from ...core import pdf_preview
from . import service
from . import date_render as _date_render
from . import restrict_render as _restrict_render

router = APIRouter()


@router.post("/render-date")
async def render_date_endpoint(request: Request):
    """Generate a transparent PNG containing a date / short text with optional
    handwriting-style jitter. Returns base64 PNG + dimensions so the frontend
    can place it like a stamp asset.

    Input (JSON):
        text:       str — the literal text to render (already formatted)
        font_style: str — "klee" (default) or "system"
        font_size_px: int — render size in pixels (default 72)
        color_hex:  str — "#1a1a2e" etc.
        jitter:     bool — handwriting jitter (default True)
    Output:
        { "png_b64": str, "width_px": int, "height_px": int,
          "suggested_width_mm": float, "suggested_height_mm": float }
    """
    import base64
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text 不可為空")
    if len(text) > 80:
        raise HTTPException(400, "text 過長(最多 80 字)")
    font_style = str(body.get("font_style") or "lxgw")
    if font_style not in ("lxgw", "klee", "system"):
        font_style = "lxgw"
    weight = str(body.get("weight") or "regular").lower()
    if weight not in ("light", "regular", "medium", "bold", "heavy"):
        weight = "regular"
    try:
        font_size_px = int(body.get("font_size_px") or 72)
    except Exception:
        font_size_px = 72
    font_size_px = max(16, min(200, font_size_px))
    color_hex = str(body.get("color_hex") or "#1a1a2e")
    # Sanitize color
    import re as _re
    if not _re.fullmatch(r"#?[0-9a-fA-F]{3,8}", color_hex):
        color_hex = "#1a1a2e"
    jitter = bool(body.get("jitter", True))
    texture = str(body.get("texture") or "medium").lower()
    if texture not in ("none", "light", "medium", "heavy"):
        texture = "medium"
    try:
        png_bytes, w, h = _date_render.render_date_png(
            text,
            font_style=font_style,
            weight=weight,
            font_size_px=font_size_px,
            color_hex=color_hex,
            jitter=jitter,
            texture=texture,
        )
    except Exception as e:
        raise HTTPException(500, f"render failed: {e.__class__.__name__}") from e
    # Suggested mm size: ~6mm font height (typical handwriting on paper)
    # Convert px → mm: at 72 dpi, 72px = 25.4mm so 1px ≈ 0.353mm
    # We choose target height_mm = font_size_px * 0.353 * 0.6 (compress for handwriting feel)
    target_height_mm = font_size_px * 0.353 * 0.55
    aspect = (w / h) if h else 1.0
    target_width_mm = target_height_mm * aspect
    return {
        "png_b64": base64.b64encode(png_bytes).decode("ascii"),
        "width_px": w,
        "height_px": h,
        "suggested_width_mm": round(target_width_mm, 1),
        "suggested_height_mm": round(target_height_mm, 1),
    }


@router.post("/render-restrict-stamp")
async def render_restrict_stamp_endpoint(request: Request):
    """渲染「個資限用章」PNG。

    Input (JSON):
        purpose:      str — 用途文字(例「申請台新銀行帳戶」),必填
        date_str:     str — 日期文字(已格式化),選填
        applicant:    str — 申請人姓名，選填
        copy_label:   str — 影本份數標示(例「第 1 份 / 共 3 份」),選填
        style:        str — "rectangle" / "diagonal",預設 rectangle
        border:       str — rectangle 模式邊框「double」/「single」/「none」
        color_hex:    str — 預設 "#c00000" 深紅
        font_size_px: int — 預設 64
    Output:
        { png_b64, width_px, height_px, suggested_width_mm, suggested_height_mm }
    """
    import base64
    body = await request.json()
    purpose = str(body.get("purpose") or "").strip()
    if not purpose:
        raise HTTPException(400, "purpose 不可為空")
    if len(purpose) > 60:
        raise HTTPException(400, "purpose 過長(最多 60 字)")
    date_str = str(body.get("date_str") or "").strip()[:40]
    applicant = str(body.get("applicant") or "").strip()[:20]
    copy_label = str(body.get("copy_label") or "").strip()[:30]
    style = str(body.get("style") or "rectangle")
    if style not in ("rectangle", "diagonal"):
        style = "rectangle"
    border = str(body.get("border") or "double")
    if border not in ("double", "single", "none"):
        border = "double"
    color_hex = str(body.get("color_hex") or "#c00000")
    import re as _re
    if not _re.fullmatch(r"#?[0-9a-fA-F]{3,8}", color_hex):
        color_hex = "#c00000"
    # font_style 可為 semantic ("kaiti"/"song"/"hei"/"lxgw") 或 font_catalog id
    # ("system:..." / "custom:..."),_load_font 內部分流處理
    font_style = str(body.get("font_style") or "kaiti").strip()
    # 安全性:長度限制 + 字符白名單
    if len(font_style) > 256:
        font_style = "kaiti"
    import re as _re_fs
    if not _re_fs.fullmatch(r"[A-Za-z0-9_./:\-]+", font_style):
        font_style = "kaiti"
    try:
        font_size_px = int(body.get("font_size_px") or 64)
    except Exception:
        font_size_px = 64
    font_size_px = max(24, min(160, font_size_px))

    try:
        if style == "diagonal":
            png_bytes, w, h = _restrict_render.render_diagonal_stamp(
                purpose=purpose, date_str=date_str,
                color_hex=color_hex, font_size_px=font_size_px,
                font_style=font_style,
            )
        else:
            png_bytes, w, h = _restrict_render.render_rectangle_stamp(
                purpose=purpose, date_str=date_str,
                applicant=applicant, copy_label=copy_label,
                color_hex=color_hex, font_size_px=font_size_px,
                border_style=border, font_style=font_style,
            )
    except Exception as e:
        raise HTTPException(500, f"render failed: {e.__class__.__name__}: {e}") from e

    # 1 px @ 72 dpi ≈ 0.353 mm. 個資限用章建議寬度 ~45 mm(顯眼但不過大)
    aspect = (w / h) if h else 1.0
    target_height_mm = font_size_px * 0.353 * 1.2  # 行高 + padding 估算
    target_width_mm = target_height_mm * aspect
    return {
        "png_b64": base64.b64encode(png_bytes).decode("ascii"),
        "width_px": w,
        "height_px": h,
        "suggested_width_mm": round(target_width_mm, 1),
        "suggested_height_mm": round(target_height_mm, 1),
    }


@router.get("/restrict-templates")
async def restrict_templates_endpoint():
    """回傳「個資限用章」常用用途範本清單。"""
    return {"templates": _restrict_render.PURPOSE_TEMPLATES}


@router.get("/restrict-fonts")
async def restrict_fonts_endpoint():
    """回傳可用字型清單(用 font_catalog,跟 pdf-editor 同源)。

    上分四類 + 內建 semantic style:
    - 內建 semantic style (kaiti / song / hei / lxgw) — 自動最佳化
    - custom: 設定→字型管理上傳的公司字型
    - taiwan: 台灣常用字型 (BiauKai / DFKai / MingLiU / PMingLiU / msjh ...)
    - free-cjk: 開源 CJK (Noto, Source Han, LXGW, AR PL UKai, etc.)
    - cjk: 其他 CJK
    """
    from app.core import font_catalog
    fonts = font_catalog.list_fonts()
    # 過濾只留 CJK 能用的(個資章需中文渲染)
    cjk_fonts = [f for f in fonts
                 if f.get("category") in ("custom", "taiwan", "free-cjk", "cjk")]
    groups: dict[str, list] = {}
    for f in cjk_fonts:
        groups.setdefault(f["category"], []).append({
            "id": f["id"],
            "label": f["label"],
            "family": f["family"],
            "variant": f.get("variant", ""),
            "style": f.get("style"),
        })
    group_titles = {
        "custom": "自訂上傳字型",
        "taiwan": "台灣系統字型",
        "free-cjk": "開源 CJK 字型",
        "cjk": "其他 CJK 字型",
    }
    ordered = []
    # 先放 semantic style (使用者沒設定字型管理也能用)
    semantic = [
        {"id": "kaiti", "label": "標楷體（自動找系統最佳）", "family": "標楷體", "variant": "", "style": "serif"},
        {"id": "song",  "label": "宋體（典雅）",         "family": "宋體",   "variant": "", "style": "serif"},
        {"id": "hei",   "label": "黑體（現代）",         "family": "黑體",   "variant": "", "style": "sans"},
        {"id": "lxgw",  "label": "霞鶩文楷（內建）",     "family": "LXGW",   "variant": "", "style": "serif"},
    ]
    ordered.append({"key": "semantic", "title": "內建快選", "fonts": semantic})
    for key in ("custom", "taiwan", "free-cjk", "cjk"):
        if key in groups and groups[key]:
            ordered.append({"key": key, "title": group_titles[key], "fonts": groups[key]})
    return {"groups": ordered, "total": len(cjk_fonts) + len(semantic)}


# 臨時資產 (#7, v1.3.16)
# 使用者可以在 pdf-stamp UI 「臨時上傳」一張圖，圖只放在瀏覽器 sessionStorage，
# 送出時才隨 request 上傳到 server，server 寫到 temp_dir 用一次就丟。
# 用 stamp_id == "__temp__" 作為哨兵 — 配合 multipart 內的 temp_asset_file。
_TEMP_STAMP_SENTINEL = "__temp__"
_TEMP_ASSET_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_TEMP_ASSET_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}


_NONE_STAMP_SENTINEL = "__none__"


async def _resolve_stamp_source(
    stamp_id: str,
    temp_asset_file: Optional[UploadFile],
    request: Optional[Request] = None,
    actor_username: str = "",
) -> tuple[Optional[Path], dict]:
    """Return (stamp_image_path_on_disk, preset_dict_or_empty).

    - stamp_id == '__none__': 不蓋主印章，只跑 extras (日期 / 個資限用章)。
      回 (None, {}) 讓呼叫端 skip 主章蓋章邏輯。
    - 一般 asset：查 asset_manager,回路徑 + asset.preset (轉 dict)
    - 臨時資產：把上傳檔案落地到 temp_dir,回路徑 + 空 preset (preset 由 client
      傳的 override 決定，所以 service 層只認 override 即可)

    任何錯誤情境一律 raise HTTPException(400)。
    """
    if stamp_id == _NONE_STAMP_SENTINEL:
        return None, {}
    if stamp_id != _TEMP_STAMP_SENTINEL:
        asset = asset_manager.get(stamp_id)
        if not asset or asset.type not in ("stamp", "signature", "logo"):
            raise HTTPException(400, "stamp not found")
        # asset.preset is a PositionPreset dataclass; service layer reads via
        # PositionPreset / dict transparently. Caller treats this as opaque.
        return asset_manager.file_path(asset), {
            "x_mm": asset.preset.x_mm, "y_mm": asset.preset.y_mm,
            "width_mm": asset.preset.width_mm, "height_mm": asset.preset.height_mm,
            "rotation_deg": asset.preset.rotation_deg,
            "paper_w_mm": asset.preset.paper_w_mm,
            "paper_h_mm": asset.preset.paper_h_mm,
        }
    if not temp_asset_file:
        raise HTTPException(400, "temp asset selected but no file uploaded")
    # validate filename + size
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
    # validate it's actually an image (not just an .png-named .exe)
    try:
        from PIL import Image as _PILImage
        from io import BytesIO as _BytesIO
        with _PILImage.open(_BytesIO(data)) as im:
            im.verify()
    except Exception as e:
        raise HTTPException(400, f"temp asset is not a valid image: {e}")
    # Save to temp_dir under a unique name (this stamp_dir gets garbage
    # collected by the 2-hour temp cleanup task; not stored as a real asset)
    out = settings.temp_dir / f"stamp_temp_{uuid.uuid4().hex}{ext or '.png'}"
    out.write_bytes(data)
    # Audit (best-effort; never fail the user request because audit failed)
    try:
        from ...core import audit_db as _audit
        import hashlib as _hl
        ip = ""
        if request is not None:
            ip = (request.client.host if request.client else "") or ""
        _audit.log_event(
            event_type="temp_asset_used",
            username=actor_username or "",
            ip=ip,
            target="pdf-stamp",
            details={
                "filename": fname or "(unnamed)",
                "size_bytes": len(data),
                "sha256_8": _hl.sha256(data).hexdigest()[:16],
                "tool": "pdf-stamp",
            },
        )
    except Exception:
        import logging as _lg
        _lg.getLogger(__name__).debug("temp_asset_used audit write failed",
                                     exc_info=True)
    return out, {}


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    # Accept any printable image asset (stamp / signature / logo). All three
    # render the same way as a transparent overlay, so users can pick any
    # of them from this tool — typical workflow is "stamp + signature on
    # the same form" without needing a separate signature tool.
    items = []
    for t in ("stamp", "signature", "logo"):
        items.extend(asset_manager.list(type=t))
    default = (
        asset_manager.get_default("stamp")
        or asset_manager.get_default("signature")
        or asset_manager.get_default("logo")
    )
    stamps_dict = [a.to_dict() for a in items]
    return templates.TemplateResponse(
        "pdf_stamp.html",
        {
            "request": request,
            "stamps": stamps_dict,
            "default_id": default.id if default else None,
        },
    )


def _resolve_pages(page_mode: str, pages_json: Optional[str], n: int) -> Optional[List[int]]:
    """Resolve which 0-based page indices to stamp for an n-page PDF.

    Explicit ``pages_json`` (a JSON array of 0-based page indices, sent by the
    per-page chip picker in the web UI) takes precedence over the legacy
    ``page_mode`` ("all" / "first" / "last", kept for the public REST API).
    Out-of-range indices are dropped (so the same selection can be applied to
    multi-file batches with differing page counts). Returns None to mean
    "every page" (which ``stamp_pdf`` interprets as all pages).
    """
    if pages_json:
        try:
            raw = json.loads(pages_json)
        except Exception:
            raw = None
        if isinstance(raw, list):
            idxs = sorted({
                int(i) for i in raw
                if isinstance(i, (int, float)) and 0 <= int(i) < n
            })
            if idxs:
                return None if len(idxs) >= n else idxs
            # empty / all-out-of-range → fall through to page_mode default
    if page_mode == "first":
        return [0]
    if page_mode == "last":
        return [max(0, n - 1)]
    return None


@router.post("/submit")
async def submit(
    request: Request,
    stamp_id: str = Form(...),
    file: List[UploadFile] = File(...),
    override: Optional[str] = Form(None),
    page_mode: str = Form("all"),  # "all" | "first" | "last" (legacy / API)
    pages_json: Optional[str] = Form(None),  # explicit 0-based indices (web UI)
    temp_asset_file: Optional[UploadFile] = File(None),
    extras_json: Optional[str] = Form(None),
):
    """Stamp one or many PDFs. Single-file result → PDF; multi → ZIP.

    extras_json: optional list of additional items to stamp on top of the
    primary stamp. Each item: {png_b64, x_mm, y_mm, width_mm, height_mm,
    rotation_deg}. Used by the 日期插入 feature (1b)."""
    from ...core import sessions as _sessions
    actor = _sessions.user_label(getattr(request.state, "user", None))
    stamp_png, preset_dict = await _resolve_stamp_source(
        stamp_id, temp_asset_file, request=request, actor_username=actor)
    asset = asset_manager.get(stamp_id) if stamp_id != _TEMP_STAMP_SENTINEL else None

    files = file or []
    if not files:
        raise HTTPException(400, "沒有檔案")
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")

    # Save all uploads to temp now (the request stream can't be replayed
    # inside the background job).
    batch_id = uuid.uuid4().hex
    batch_dir = settings.temp_dir / f"stamp_batch_{batch_id}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []  # (src_path, orig_filename)
    for i, f in enumerate(files):
        data = await f.read()
        if not data:
            raise HTTPException(400, f"空檔：{f.filename}")
        safe = Path(f.filename).name or f"input_{i}.pdf"
        src_path = batch_dir / f"{i:03d}_{safe}"
        src_path.write_bytes(data)
        saved.append((src_path, safe))

    # Parse extras (date / future text items). Each saved to a temp PNG so
    # the stamp pipeline can place them just like images.
    extra_items: list[dict] = []
    if extras_json:
        try:
            import base64
            raw_items = json.loads(extras_json)
            if not isinstance(raw_items, list):
                raise ValueError("extras_json not a list")
            for idx, it in enumerate(raw_items):
                png_b64 = it.get("png_b64")
                if not png_b64 or not isinstance(png_b64, str):
                    continue
                if len(png_b64) > 5_000_000:  # ~3.5MB after b64 decode
                    raise HTTPException(400, "extras 圖過大")
                try:
                    png_bytes = base64.b64decode(png_b64, validate=True)
                except Exception:
                    raise HTTPException(400, "extras png_b64 解碼失敗")
                if not png_bytes.startswith(b"\x89PNG"):
                    raise HTTPException(400, "extras 必須是 PNG")
                extra_png = batch_dir / f"extra_{idx:02d}.png"
                extra_png.write_bytes(png_bytes)
                extra_items.append({
                    "png_path": extra_png,
                    "x_mm": float(it.get("x_mm", 150)),
                    "y_mm": float(it.get("y_mm", 250)),
                    "width_mm": float(it.get("width_mm", 50)),
                    "height_mm": float(it.get("height_mm", 12)),
                    "rotation_deg": float(it.get("rotation_deg", 0)),
                })
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"extras_json 解析失敗: {e.__class__.__name__}")

    # Resolve placement params (shared by all files). preset_dict comes
    # from _resolve_stamp_source — empty dict for temp assets means
    # override IS the only source (UI sends a default sensible preset).
    if override:
        try:
            ov = json.loads(override)
            p_x = float(ov.get("x_mm", preset_dict.get("x_mm", 105)))
            p_y = float(ov.get("y_mm", preset_dict.get("y_mm", 250)))
            p_w = float(ov.get("width_mm", preset_dict.get("width_mm", 30)))
            p_h = float(ov.get("height_mm", preset_dict.get("height_mm", 30)))
            p_rot = float(ov.get("rotation_deg", preset_dict.get("rotation_deg", 0)))
        except Exception:
            raise HTTPException(400, "override 格式錯誤")
    elif preset_dict:
        p_x = preset_dict["x_mm"]; p_y = preset_dict["y_mm"]
        p_w = preset_dict["width_mm"]; p_h = preset_dict["height_mm"]
        p_rot = preset_dict["rotation_deg"]
    else:
        # Temp asset, no override — use sensible default (centered low)
        p_x, p_y, p_w, p_h, p_rot = 105.0, 250.0, 30.0, 30.0, 0.0

    def run(job):
        total = len(saved)
        stamped_paths: list[tuple[Path, str]] = []
        import fitz
        for i, (src_path, orig_name) in enumerate(saved):
            job.message = f"處理第 {i + 1}/{total} 份：{orig_name}"
            job.progress = (i / max(1, total)) * 0.95
            # Per-file page selection depends on that file's page count.
            with fitz.open(str(src_path)) as doc:
                n = doc.page_count
            pages = _resolve_pages(page_mode, pages_json, n)
            dst = batch_dir / f"{src_path.stem}_stamped.pdf"
            if stamp_png is None:
                # 「不蓋章」模式 → 直接複製原檔當基底，只跑 extras
                import shutil as _sh
                _sh.copy(str(src_path), str(dst))
            else:
                params = service.StampParams(
                    x_mm=p_x, y_mm=p_y, width_mm=p_w, height_mm=p_h,
                    rotation_deg=p_rot, pages=pages,
                )
                service.stamp(src_path, dst, stamp_png, params)
            # Apply extra items (date, etc.) on top of the primary stamp
            for ex in extra_items:
                tmp_dst = batch_dir / f"{src_path.stem}_extra_{uuid.uuid4().hex[:8]}.pdf"
                ex_params = service.StampParams(
                    x_mm=ex["x_mm"], y_mm=ex["y_mm"],
                    width_mm=ex["width_mm"], height_mm=ex["height_mm"],
                    rotation_deg=ex["rotation_deg"], pages=pages,
                )
                service.stamp(dst, tmp_dst, ex["png_path"], ex_params)
                # Replace dst with the new file (chained stamping)
                try:
                    dst.unlink()
                except Exception:
                    pass
                tmp_dst.rename(dst)
            stamped_paths.append((dst, _result_filename(orig_name)))

        if len(stamped_paths) == 1:
            result_path, result_name = stamped_paths[0]
        else:
            zip_name = f"stamped_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zip_path = batch_dir / zip_name
            # Disambiguate duplicate names by prefixing a sequence.
            used: dict[str, int] = {}
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for dst, name in stamped_paths:
                    k = used.get(name, 0) + 1
                    used[name] = k
                    arcname = name if k == 1 else f"{Path(name).stem}_{k}{Path(name).suffix}"
                    zf.write(dst, arcname=arcname)
            result_path = zip_path
            result_name = zip_name

        job.progress = 1.0
        job.message = f"完成（{total} 份）"
        job.result_path = result_path
        job.result_filename = result_name

        # ---- v1.1.0: archive into stamp_history ----
        # One entry per stamped source file (so admin / user can revisit).
        try:
            from ...core.history_manager import stamp_history
            for src_path, orig_name in saved:
                stem = Path(src_path).stem
                dst = batch_dir / f"{stem}_stamped.pdf"
                if dst.exists():
                    stamp_history.save(
                        original_path=src_path,
                        filled_path=dst,
                        preview_path=None,
                        original_filename=orig_name,
                        username=actor or "",
                        extra={"asset_id": stamp_id,
                               "x_mm": p_x, "y_mm": p_y,
                               "width_mm": p_w, "height_mm": p_h,
                               "rotation_deg": p_rot},
                    )
        except Exception:
            # History write is best-effort; never fail the user request.
            import logging as _lg
            _lg.getLogger(__name__).exception("stamp_history.save failed")

    job = job_manager.submit(
        "pdf-stamp", run,
        meta={"stamp_id": stamp_id, "count": len(saved)},
    )
    return {"job_id": job.id}


@router.get("/pdf-preview/{upload_id}")
async def tool_preview(upload_id: str):
    """Not currently used from the client; placeholder for future previewing uploaded files."""
    raise HTTPException(404, "not implemented")


@router.post("/preview")
async def preview(file: UploadFile = File(...)):
    """Render the first page of an uploaded PDF to PNG. Also returns per-page
    dimensions so the editor mode can offer page navigation (lazy-rendered via
    /preview-bg/{upload_id}/{page_idx})."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"{upload_id}.pdf"
    src.write_bytes(data)
    png = settings.temp_dir / f"{upload_id}_p1.png"
    await asyncio.to_thread(pdf_preview.render_page_png, src, png, 0, 110)

    import fitz
    from ...core.unit_convert import pt_to_mm
    with fitz.open(str(src)) as doc:
        page_count = doc.page_count
        pages_dims = [
            {"w_mm": round(pt_to_mm(doc[i].rect.width), 2),
             "h_mm": round(pt_to_mm(doc[i].rect.height), 2)}
            for i in range(page_count)
        ]

    return {
        "upload_id": upload_id,
        "preview_url": f"/tools/pdf-stamp/preview/{upload_id}_p1.png",
        "paper_w_mm": pages_dims[0]["w_mm"],
        "paper_h_mm": pages_dims[0]["h_mm"],
        "page_count": page_count,
        "pages_dims": pages_dims,
    }


@router.get("/preview-bg/{upload_id}/{page_idx}")
async def preview_bg(upload_id: str, page_idx: int):
    """Lazily render any page of a previously-uploaded PDF (from /preview)
    so the editor mode can switch its background between pages."""
    if not upload_id.replace("_", "").isalnum():
        raise HTTPException(400, "bad upload_id")
    src = settings.temp_dir / f"{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")
    if page_idx < 0:
        raise HTTPException(400, "bad page index")
    png = settings.temp_dir / f"{upload_id}_p{page_idx + 1}.png"
    if not png.exists():
        try:
            await asyncio.to_thread(pdf_preview.render_page_png, src, png, page_idx, 110)
        except IndexError:
            raise HTTPException(404, "page out of range")
    return {"preview_url": f"/tools/pdf-stamp/preview/{png.name}"}


@router.post("/preview-all-pages")
async def preview_all_pages(
    request: Request,
    stamp_id: str = Form(...),
    file: UploadFile = File(...),
    override: Optional[str] = Form(None),
    page_mode: str = Form("all"),
    pages_json: Optional[str] = Form(None),
    temp_asset_file: Optional[UploadFile] = File(None),
    extras_json: Optional[str] = Form(None),
):
    """Render every page of the uploaded PDF with the stamp applied at the
    given position; return one PNG URL per page so the UI can stack them.

    extras_json: 額外的 overlay (date / restrict 等),會在 primary 印章之後
    依序疊上，讓「合成模式」逐頁看到完整結果。
    """
    from ...core import sessions as _sessions
    actor = _sessions.user_label(getattr(request.state, "user", None))
    stamp_png_path, preset_dict = await _resolve_stamp_source(
        stamp_id, temp_asset_file, request=request, actor_username=actor)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    stamped = settings.temp_dir / f"{upload_id}_stamped.pdf"
    src.write_bytes(data)

    if override:
        try:
            ov = json.loads(override)
            p_x = float(ov.get("x_mm", preset_dict.get("x_mm", 105)))
            p_y = float(ov.get("y_mm", preset_dict.get("y_mm", 250)))
            p_w = float(ov.get("width_mm", preset_dict.get("width_mm", 30)))
            p_h = float(ov.get("height_mm", preset_dict.get("height_mm", 30)))
            p_rot = float(ov.get("rotation_deg", preset_dict.get("rotation_deg", 0)))
        except Exception:
            raise HTTPException(400, "override 格式錯誤")
    elif preset_dict:
        p_x = preset_dict["x_mm"]; p_y = preset_dict["y_mm"]
        p_w = preset_dict["width_mm"]; p_h = preset_dict["height_mm"]
        p_rot = preset_dict["rotation_deg"]
    else:
        p_x, p_y, p_w, p_h, p_rot = 105.0, 250.0, 30.0, 30.0, 0.0

    import fitz
    with fitz.open(str(src)) as doc:
        n = doc.page_count
    pages = _resolve_pages(page_mode, pages_json, n)

    from ...core import pdf_utils as pu
    if stamp_png_path is None:
        # 「不蓋章」模式 → 複製原檔當基底
        import shutil as _sh
        _sh.copy(str(src), str(stamped))
    else:
        pu.stamp_pdf(
            src_pdf=src, dst_pdf=stamped,
            stamp_png=stamp_png_path,
            x_mm=p_x, y_mm=p_y, w_mm=p_w, h_mm=p_h,
            pages=pages, rotation_deg=p_rot,
        )

    # Apply extras (date / restrict) on top of primary stamp
    extra_tmp_files: list[Path] = []
    if extras_json:
        try:
            import base64
            raw_items = json.loads(extras_json)
            if isinstance(raw_items, list):
                for idx, it in enumerate(raw_items):
                    png_b64 = it.get("png_b64")
                    if not png_b64 or not isinstance(png_b64, str):
                        continue
                    if len(png_b64) > 5_000_000:
                        continue
                    try:
                        png_bytes = base64.b64decode(png_b64, validate=True)
                    except Exception:
                        continue
                    if not png_bytes.startswith(b"\x89PNG"):
                        continue
                    extra_png = settings.temp_dir / f"{upload_id}_extra_{idx:02d}.png"
                    extra_png.write_bytes(png_bytes)
                    extra_tmp_files.append(extra_png)
                    tmp_stamped = settings.temp_dir / f"{upload_id}_chain_{idx:02d}.pdf"
                    pu.stamp_pdf(
                        src_pdf=stamped,
                        dst_pdf=tmp_stamped,
                        stamp_png=extra_png,
                        x_mm=float(it.get("x_mm", 150)),
                        y_mm=float(it.get("y_mm", 250)),
                        w_mm=float(it.get("width_mm", 50)),
                        h_mm=float(it.get("height_mm", 12)),
                        pages=pages,
                        rotation_deg=float(it.get("rotation_deg", 0)),
                    )
                    try:
                        stamped.unlink()
                    except OSError:
                        pass
                    tmp_stamped.rename(stamped)
        except Exception:
            pass

    # Render every page (or just the affected ones if a subset was picked) to
    # PNG so the front end can stack them.
    out_pages: list[dict] = []
    indices = pages if pages is not None else list(range(n))
    for i in range(n):
        png = settings.temp_dir / f"{upload_id}_p{i + 1}.png"
        pdf_preview.render_page_png(stamped, png, i, dpi=120)
        out_pages.append({
            "index": i,
            "stamped": i in indices,
            "preview_url": f"/tools/pdf-stamp/preview/{png.name}",
        })

    try:
        src.unlink(); stamped.unlink()
        for p in extra_tmp_files:
            p.unlink(missing_ok=True)
    except OSError:
        pass

    return {"page_count": n, "pages": out_pages}


@router.post("/preview-stamped")
async def preview_stamped(
    request: Request,
    stamp_id: str = Form(...),
    file: UploadFile = File(...),
    override: Optional[str] = Form(None),
    page_mode: str = Form("all"),
    pages_json: Optional[str] = Form(None),
    temp_asset_file: Optional[UploadFile] = File(None),
    extras_json: Optional[str] = Form(None),
):
    """Stamp the first applicable page of the PDF and return a PNG preview.

    extras_json: 同 /submit,額外的 overlay 物件 (date / restrict 等),會在
    primary 印章蓋完之後依序疊上，讓「合成模式」看得到完整結果。
    """
    from ...core import sessions as _sessions
    actor = _sessions.user_label(getattr(request.state, "user", None))
    stamp_png_path, preset_dict = await _resolve_stamp_source(
        stamp_id, temp_asset_file, request=request, actor_username=actor)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    stamped = settings.temp_dir / f"{upload_id}_stamped.pdf"
    png = settings.temp_dir / f"{upload_id}_preview.png"
    src.write_bytes(data)

    # Resolve params (same rules as /submit)
    if override:
        try:
            ov = json.loads(override)
            p_x = float(ov.get("x_mm", preset_dict.get("x_mm", 105)))
            p_y = float(ov.get("y_mm", preset_dict.get("y_mm", 250)))
            p_w = float(ov.get("width_mm", preset_dict.get("width_mm", 30)))
            p_h = float(ov.get("height_mm", preset_dict.get("height_mm", 30)))
            p_rot = float(ov.get("rotation_deg", preset_dict.get("rotation_deg", 0)))
        except Exception:
            raise HTTPException(400, "override 格式錯誤")
    elif preset_dict:
        p_x = preset_dict["x_mm"]; p_y = preset_dict["y_mm"]
        p_w = preset_dict["width_mm"]; p_h = preset_dict["height_mm"]
        p_rot = preset_dict["rotation_deg"]
    else:
        p_x, p_y, p_w, p_h, p_rot = 105.0, 250.0, 30.0, 30.0, 0.0

    # Pick the page to preview: first page that will receive a stamp
    import fitz
    with fitz.open(str(src)) as doc:
        n = doc.page_count
    pages = _resolve_pages(page_mode, pages_json, n)
    preview_page = pages[0] if pages else 0

    from ...core import pdf_utils as pu
    if stamp_png_path is None:
        import shutil as _sh
        _sh.copy(str(src), str(stamped))
    else:
        pu.stamp_pdf(
            src_pdf=src,
            dst_pdf=stamped,
            stamp_png=stamp_png_path,
            x_mm=p_x, y_mm=p_y, w_mm=p_w, h_mm=p_h,
            pages=pages, rotation_deg=p_rot,
        )

    # Apply extras (date / restrict) on top of primary stamp,讓合成模式
    # 看得到實際結果。每個 extra 寫到 temp PNG → chain stamp 到當前 PDF。
    extra_tmp_files: list[Path] = []
    if extras_json:
        try:
            import base64
            raw_items = json.loads(extras_json)
            if isinstance(raw_items, list):
                for idx, it in enumerate(raw_items):
                    png_b64 = it.get("png_b64")
                    if not png_b64 or not isinstance(png_b64, str):
                        continue
                    if len(png_b64) > 5_000_000:
                        continue
                    try:
                        png_bytes = base64.b64decode(png_b64, validate=True)
                    except Exception:
                        continue
                    if not png_bytes.startswith(b"\x89PNG"):
                        continue
                    extra_png = settings.temp_dir / f"{upload_id}_extra_{idx:02d}.png"
                    extra_png.write_bytes(png_bytes)
                    extra_tmp_files.append(extra_png)
                    tmp_stamped = settings.temp_dir / f"{upload_id}_chain_{idx:02d}.pdf"
                    pu.stamp_pdf(
                        src_pdf=stamped,
                        dst_pdf=tmp_stamped,
                        stamp_png=extra_png,
                        x_mm=float(it.get("x_mm", 150)),
                        y_mm=float(it.get("y_mm", 250)),
                        w_mm=float(it.get("width_mm", 50)),
                        h_mm=float(it.get("height_mm", 12)),
                        pages=pages,
                        rotation_deg=float(it.get("rotation_deg", 0)),
                    )
                    try:
                        stamped.unlink()
                    except OSError:
                        pass
                    tmp_stamped.rename(stamped)
        except Exception:
            # 預覽用，任何 extras 錯誤都安靜跳過，不擋 primary 預覽
            pass

    pdf_preview.render_page_png(stamped, png, preview_page, dpi=120)

    # Clean up intermediates
    for fp in (src, stamped, *extra_tmp_files):
        try:
            fp.unlink()
        except OSError:
            pass

    return {
        "preview_url": f"/tools/pdf-stamp/preview/{png.name}",
        "preview_page": preview_page + 1,
        "page_count": n,
    }


@router.get("/preview/{name}")
async def serve_preview(name: str, request: Request):
    from fastapi.responses import FileResponse
    from app.core.safe_paths import safe_join
    from ...core import upload_owner
    p = safe_join(settings.temp_dir, name)
    uid = upload_owner.extract_upload_id(name)
    if uid:
        upload_owner.require(uid, request)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type="image/png")


def _result_filename(orig: str) -> str:
    stem = Path(orig).stem
    return f"{stem}_stamped.pdf"


# ---- 對外 API：單次 upload + 印章圖檔 + 直接回 PDF ----
from fastapi.responses import FileResponse as _FileResponse  # noqa: E402


@router.post("/api/pdf-stamp", include_in_schema=True)
async def api_pdf_stamp(
    request: Request,
    file: UploadFile = File(...),
    stamp_image: UploadFile = File(...),
    x_mm: float = Form(105.0),
    y_mm: float = Form(250.0),
    width_mm: float = Form(30.0),
    height_mm: float = Form(30.0),
    rotation_deg: float = Form(0.0),
    page_mode: str = Form("all"),  # all | first | last
    pages_json: Optional[str] = Form(None),  # 指定頁：JSON 陣列, 0-based index
):
    """單次上傳 PDF + 印章圖檔（PNG / JPG），蓋章後回 PDF。"""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    img_ext = Path(stamp_image.filename or "stamp.png").suffix.lower()
    if img_ext not in _TEMP_ASSET_ALLOWED_EXT:
        raise HTTPException(400, f"印章圖檔格式必須是 {_TEMP_ASSET_ALLOWED_EXT}")
    pdf_data = await file.read()
    if not pdf_data or pdf_data[:4] != b"%PDF":
        raise HTTPException(400, "不是有效的 PDF")
    img_data = await stamp_image.read()
    if not img_data:
        raise HTTPException(400, "印章圖檔為空")
    if len(img_data) > _TEMP_ASSET_MAX_BYTES:
        raise HTTPException(400, f"印章圖檔超過 {_TEMP_ASSET_MAX_BYTES // 1024 // 1024} MB")
    uid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(uid, request)
    src = settings.temp_dir / f"stamp_api_{uid}_in.pdf"
    src.write_bytes(pdf_data)
    stamp_png = settings.temp_dir / f"stamp_api_{uid}{img_ext}"
    stamp_png.write_bytes(img_data)
    out = settings.temp_dir / f"stamp_api_{uid}_out.pdf"
    stem = Path(file.filename or "document.pdf").stem
    import fitz as _fitz
    with _fitz.open(str(src)) as d:
        n = d.page_count
    pages_arg = _resolve_pages(page_mode, pages_json, n)
    params = service.StampParams(
        x_mm=x_mm, y_mm=y_mm, width_mm=width_mm, height_mm=height_mm,
        rotation_deg=rotation_deg, pages=pages_arg,
    )
    await asyncio.to_thread(service.stamp, src, out, stamp_png, params)
    return _FileResponse(str(out), media_type="application/pdf",
                         filename=f"{stem}_stamped.pdf")
