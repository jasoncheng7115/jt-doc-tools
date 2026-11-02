"""掃描拼合 (scan-merge) — 把多張掃描中「有內容的區塊」依原位置疊到單張 A4 白底。

典型場景：事務機分兩次掃身分證正 / 反面 → 兩個檔，內容各在 A4 不同位置。
本工具自動抓出每張的內容區塊、保留原彩色，合成到同一張乾淨白底 A4。

流程（analyze-then-bake，避免重複上傳）：
  1. POST /upload          → 一個檔（PDF / 圖片），render 每頁 → 偵測內容區塊
                             → 存彩色 crop（原版 + 淨白版）→ 回每塊建議位置
  2. GET  /crop/{cid}/{v}  → 取 crop PNG（v = raw | white），ACL 保護
  3. POST /delete/{cid}    → 清掉某 crop
  4. POST /layout          → 「自動排列」：給各塊大小，回不重疊的位置（不碰檔案）
  5. POST /generate        → 收最終位置清單 → 合成單張 A4 白底 PDF → job_id
  6. POST /api/scan-merge  → 對外一次性：上傳多檔，依原始位置或自動排列合成，直接回 PDF

身分證等高敏感 PII：本地處理、暫存自動清除、預設不留歷史。
"""
from __future__ import annotations

import asyncio
import io
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from PIL import Image

from ...config import settings
from ...core import upload_owner as _uo
from ...core.http_utils import content_disposition
from ...core.job_manager import job_manager
from ...core.safe_paths import require_uuid_hex
from .detector import crop_card, detect_regions, to_rgb
from .layout import LayoutError, auto_layout

router = APIRouter()

# A4 直式（points，1pt = 1/72 in）
A4_W_PT = 595.276
A4_H_PT = 841.890

#: 排列方式：依原始位置（預設）/ 自動排列（不重疊）
LAYOUT_MODES = ("original", "auto")

_ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
_MAX_BYTES = 50 * 1024 * 1024          # 單檔 50 MB
_MAX_PAGES = 20                        # 單檔最多處理 20 頁
_MAX_CROPS_PER_UPLOAD = 30            # 單次上傳最多回 30 塊（避免雜訊頁爆量）
_PDF_RENDER_DPI = 200                  # PDF 頁面 render 解析度
#: 一個檔裡的各頁同時處理的上限（縮圖、偵測、裁切）。PDF 畫頁面仍是一頁一頁來
#: （PyMuPDF 不支援多執行緒）。
_MAX_PAGE_WORKERS = 4


def _page_workers(n: int) -> int:
    return max(1, min(_MAX_PAGE_WORKERS, os.cpu_count() or 2, n))


def _save_png(img: Image.Image, path: Path) -> None:
    """存 PNG。**不用 `optimize=True`**：掃描頁帶雜訊，optimize 要多試好幾種壓縮，
    一張 600 px 的預覽圖從 0.3 秒變 1 秒，一份 8 頁的 PDF 就多等十幾秒，
    客戶等到以為當機（2026-10-28）。換來的只是暫存檔小一點。"""
    img.save(str(path), format="PNG")


def _work_dir() -> Path:
    d = settings.temp_dir / "scan_merge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _crop_path(cid: str, variant: str) -> Path:
    suffix = "_w" if variant == "white" else ""
    return _work_dir() / f"crop_{cid}{suffix}.png"


def _source_path(sid: str) -> Path:
    """來源頁縮圖（供上傳區預覽用，最長邊 ~600px）。"""
    return _work_dir() / f"source_{sid}.png"


_SOURCE_THUMB_MAX = 600


def _render_pages(raw: bytes, ext: str) -> list[Image.Image]:
    """把一個輸入檔轉成一串 PIL 影像（PDF 逐頁 render，圖片就是單張）。"""
    pages: list[Image.Image] = []
    if ext == ".pdf":
        doc = fitz.open(stream=raw, filetype="pdf")
        try:
            mat = fitz.Matrix(_PDF_RENDER_DPI / 72.0, _PDF_RENDER_DPI / 72.0)
            for i, page in enumerate(doc):
                if i >= _MAX_PAGES:
                    break
                pix = page.get_pixmap(matrix=mat, alpha=False)
                pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        finally:
            doc.close()
    else:
        opened = Image.open(io.BytesIO(raw))
        n_frames = getattr(opened, "n_frames", 1) or 1
        for i in range(min(n_frames, _MAX_PAGES)):
            try:
                opened.seek(i)
            except (EOFError, OSError):
                break
            pages.append(to_rgb(opened.copy()))
    return pages


def _store_crop(cid: str, page_img: Image.Image, region, request: Request) -> dict:
    """存兩版 RGBA crop（raw / 淨白），padding 區透明，回前端 metadata。"""
    _uo.record(cid, request)
    raw = crop_card(page_img, region, whiten=False)
    _save_png(raw, _crop_path(cid, "raw"))
    _save_png(crop_card(page_img, region, whiten=True), _crop_path(cid, "white"))
    return {
        "crop_id": cid,
        "natural_w": raw.width,
        "natural_h": raw.height,
        "url_raw": f"/tools/scan-merge/crop/{cid}/raw",
        "url_white": f"/tools/scan-merge/crop/{cid}/white",
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "scan_merge.html", {"request": request})


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    """上傳一個檔，render → 偵測內容區塊 → 存彩色 crop，回每塊建議位置。"""
    name = (file.filename or "").strip()
    ext = Path(name).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(400, f"不支援的檔案格式：{ext or '未知'}")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空檔")
    if len(raw) > _MAX_BYTES:
        raise HTTPException(413, "檔案過大（單檔上限 50 MB）")
    if ext == ".pdf" and raw[:4] != b"%PDF":
        raise HTTPException(400, "不是有效的 PDF")

    # 畫頁面、偵測、存圖都是吃 CPU 的同步工作，**一定要丟到執行緒**：
    # 原本直接在 async 函式裡做，一份多頁 PDF 處理的那幾十秒整台服務的事件迴圈
    # 都被佔住，別人的頁面也跟著卡（2026-10-28 客戶回報從工作區載入要等 20～40 秒）。
    return await asyncio.to_thread(_analyze_upload, raw, ext, name, request)


def _source_thumb(sid: str, page_img: Image.Image) -> None:
    src_thumb = page_img.copy()
    src_thumb.thumbnail((_SOURCE_THUMB_MAX, _SOURCE_THUMB_MAX), Image.LANCZOS)
    _save_png(src_thumb, _source_path(sid))


def _analyze_upload(raw: bytes, ext: str, name: str, request: Request) -> dict:
    """render → 偵測內容區塊 → 存彩色 crop。各頁同時處理，回傳順序照頁次。"""
    try:
        pages = _render_pages(raw, ext)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"無法讀取檔案：{e}")
    if not pages:
        raise HTTPException(400, "檔案無內容")

    def page_job(page_img: Image.Image):
        sid = uuid.uuid4().hex
        _uo.record(sid, request)
        _source_thumb(sid, page_img)
        return sid, detect_regions(page_img)

    with ThreadPoolExecutor(max_workers=_page_workers(len(pages))) as ex:
        analysed = list(ex.map(page_job, pages))   # map 保留頁次順序

        sources: list[dict] = []
        picked: list[tuple[int, Image.Image, object]] = []
        for page_idx, (page_img, (sid, regions)) in enumerate(zip(pages, analysed)):
            sources.append({
                "source_id": sid,
                "filename": name,
                "page": page_idx + 1,
                "natural_w": page_img.width,
                "natural_h": page_img.height,
                "url": f"/tools/scan-merge/source/{sid}",
            })
            for r in regions:
                if len(picked) >= _MAX_CROPS_PER_UPLOAD:
                    break
                picked.append((page_idx, page_img, r))

        def crop_job(item):
            page_idx, page_img, r = item
            meta = _store_crop(uuid.uuid4().hex, page_img, r, request)
            meta.update({
                "filename": name,
                "page": page_idx + 1,
                # 建議擺到 A4 的相對位置（沿用 source page 的相對座標）
                "fx": round(r.fx, 5),
                "fy": round(r.fy, 5),
                "fw": round(r.fw, 5),
                "fh": round(r.fh, 5),
            })
            return meta

        crops = list(ex.map(crop_job, picked))

    if not crops:
        raise HTTPException(422, "這個檔裡找不到內容區塊（可能整頁空白）")
    return {"filename": name, "sources": sources, "crops": crops}


@router.get("/source/{sid}")
async def source_png(sid: str, request: Request):
    """來源頁縮圖（上傳區預覽用），ACL 保護。"""
    require_uuid_hex(sid, "source_id")
    _uo.require(sid, request)
    p = _source_path(sid)
    if not p.exists():
        raise HTTPException(404, "來源縮圖不存在或已過期")
    return FileResponse(str(p), media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})


@router.get("/crop/{cid}/{variant}")
async def crop_png(cid: str, variant: str, request: Request):
    require_uuid_hex(cid, "crop_id")
    if variant not in ("raw", "white"):
        raise HTTPException(400, "variant 必須是 raw 或 white")
    _uo.require(cid, request)
    p = _crop_path(cid, variant)
    if not p.exists():
        raise HTTPException(404, "crop 不存在或已過期")
    return FileResponse(str(p), media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})


@router.post("/delete/{cid}")
async def delete_crop(cid: str, request: Request):
    require_uuid_hex(cid, "crop_id")
    _uo.require(cid, request)
    for v in ("raw", "white"):
        try:
            _crop_path(cid, v).unlink(missing_ok=True)
        except Exception:
            pass
    return {"ok": True}


def _bake_a4(items: list[dict], whiten: bool, out_path: Path) -> int:
    """把每塊 crop 依 fractional rect 畫到單張 A4 白底。回畫上的塊數。

    items: [{crop_id, x, y, w, h}]，x/y/w/h 為 A4 的 0..1 比例。
    """
    doc = fitz.open()
    try:
        page = doc.new_page(width=A4_W_PT, height=A4_H_PT)
        page.draw_rect(fitz.Rect(0, 0, A4_W_PT, A4_H_PT),
                       color=None, fill=(1, 1, 1), overlay=False)
        n = 0
        variant = "white" if whiten else "raw"
        for it in items:
            cid = it["crop_id"]
            p = _crop_path(cid, variant)
            if not p.exists():
                continue
            x = max(0.0, min(1.0, float(it.get("x", 0))))
            y = max(0.0, min(1.0, float(it.get("y", 0))))
            w = max(0.001, min(1.0, float(it.get("w", 0))))
            h = max(0.001, min(1.0, float(it.get("h", 0))))
            rect = fitz.Rect(x * A4_W_PT, y * A4_H_PT,
                             (x + w) * A4_W_PT, (y + h) * A4_H_PT)
            page.insert_image(rect, filename=str(p), keep_proportion=True)
            n += 1
        doc.save(str(out_path), garbage=3, deflate=True)
        return n
    finally:
        doc.close()


@router.post("/layout")
async def layout(request: Request):
    """「自動排列」：Body `{"items": [{"id", "w", "h"}]}`（A4 比例），回 `{"items": [{"id","x","y","w","h"}]}`。

    只做幾何計算、不讀任何檔案，所以不需要歸屬檢查；`id` 只是原樣帶回，仍限制成 32 碼十六進位，
    不讓任意字串流回畫面。
    """
    body = await request.json()
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise HTTPException(400, "items 必須是清單")
    for it in items:
        if isinstance(it, dict):
            require_uuid_hex(str(it.get("id") or ""), "id")
    try:
        return {"items": auto_layout(items)}
    except LayoutError as e:
        raise HTTPException(400, str(e))


@router.post("/generate")
async def generate(request: Request):
    """合成單張 A4 白底 PDF。

    Body: {"items": [{"crop_id","x","y","w","h"}], "whiten": bool, "filename": str}
    座標 x/y/w/h 為 A4 的 0..1 比例（前端從拖曳結果換算）。
    """
    body = await request.json()
    items = body.get("items") or []
    whiten = bool(body.get("whiten", True))
    filename = (body.get("filename") or "scan-merge.pdf").strip()
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    # 顯示用檔名：去掉任何路徑成分（防穿越），保留 unicode（可中文）。
    # 實際寫檔用固定安全名，使用者檔名只進下載 header。
    display_name = Path(filename).name or "scan-merge.pdf"
    if not items:
        raise HTTPException(400, "沒有任何內容區塊")

    valid: list[dict] = []
    for it in items:
        cid = (it.get("crop_id") or "").strip()
        require_uuid_hex(cid, "crop_id")
        _uo.require(cid, request)
        if not _crop_path(cid, "raw").exists():
            raise HTTPException(404, f"crop 已過期或不存在：{cid}")
        valid.append({
            "crop_id": cid,
            "x": it.get("x", 0), "y": it.get("y", 0),
            "w": it.get("w", 0.3), "h": it.get("h", 0.2),
        })

    bid = uuid.uuid4().hex
    _uo.record(bid, request)
    bdir = _work_dir() / f"job_{bid}"
    bdir.mkdir(parents=True, exist_ok=True)
    out_path = bdir / "scan-merge.pdf"  # 固定安全 on-disk 名

    def run(job):
        job.message = "合成中…"
        job.progress = 0.3
        n = _bake_a4(valid, whiten, out_path)
        if n == 0:
            raise RuntimeError("沒有任何可用的內容區塊")
        job.result_path = out_path
        job.result_filename = display_name
        job.progress = 1.0
        job.message = f"完成（{n} 塊）"

    job = job_manager.submit("scan-merge", run, meta={"count": len(valid)})
    return {"job_id": job.id}


# ----- 對外一次性 API ---------------------------------------------------------

def _api_bake(raws: list[tuple[str, str, bytes]], whiten: bool,
              layout: str = "original") -> Path:
    placed: list[dict] = []
    tmp_dir = _work_dir() / f"api_{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    variant = "white" if whiten else "raw"
    try:
        for fname, ext, raw in raws:
            try:
                pages = _render_pages(raw, ext)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(400, f"無法讀取 {fname}：{e}")
            for page_img in pages:
                for r in detect_regions(page_img):
                    cid = uuid.uuid4().hex
                    _save_png(crop_card(page_img, r, whiten=whiten), _crop_path(cid, variant))
                    placed.append({"crop_id": cid, "x": r.fx, "y": r.fy,
                                   "w": r.fw, "h": r.fh})
        if not placed:
            raise HTTPException(422, "所有檔都找不到內容區塊")
        if layout == "auto":
            moved = auto_layout([{"id": it["crop_id"], "w": it["w"], "h": it["h"]}
                                 for it in placed])
            placed = [{"crop_id": m["id"], "x": m["x"], "y": m["y"], "w": m["w"], "h": m["h"]}
                      for m in moved]
        out_path = tmp_dir / "scan-merge.pdf"  # 固定安全 on-disk 名
        _bake_a4(placed, whiten, out_path)
        return out_path
    finally:
        # 清掉這次 API 用過的 crop（一次性，不留）
        for it in placed:
            for v in ("raw", "white"):
                try:
                    _crop_path(it["crop_id"], v).unlink(missing_ok=True)
                except Exception:
                    pass


@router.post("/api/scan-merge")
async def api_scan_merge(
    files: List[UploadFile] = File(..., description="掃描檔（PDF / PNG / JPG），各含一塊內容"),
    whiten: bool = Form(True, description="是否把淡灰掃描背景淨白（不影響彩色內容）"),
    filename: str = Form("scan-merge.pdf"),
    layout: str = Form("original", description="排列方式：original＝依原始位置（可能重疊）；auto＝自動排列（不重疊）"),
):
    """一次性：上傳多個掃描檔 → 合成單張 A4 白底 → 直接回 PDF。

    `layout=original`（預設）：每塊擺到它在原掃描中的相對位置，重疊時不重排。
    `layout=auto`：依上傳順序由上而下、由左而右排好，互不重疊（放不下時等比例縮小）。
    需要拖曳微調請改用網頁介面。
    """
    if layout not in LAYOUT_MODES:
        raise HTTPException(400, f"layout 只能是 {' / '.join(LAYOUT_MODES)}")
    if not files:
        raise HTTPException(400, "沒有檔案")
    # 顯示用檔名去路徑成分（防穿越）、保留 unicode；on-disk 用固定安全名。
    display_name = Path(filename).name or "scan-merge.pdf"
    if not display_name.lower().endswith(".pdf"):
        display_name += ".pdf"

    raws: list[tuple[str, str, bytes]] = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in _ALLOWED_EXT:
            raise HTTPException(400, f"不支援的檔案格式：{ext or '未知'}")
        raw = await f.read()
        if not raw:
            raise HTTPException(400, f"空檔：{f.filename}")
        if len(raw) > _MAX_BYTES:
            raise HTTPException(413, f"檔案過大：{f.filename}")
        raws.append((f.filename or "", ext, raw))
    # 吃 CPU 的部分丟到執行緒（同 /upload）
    out_path = await asyncio.to_thread(_api_bake, raws, whiten, layout)
    return FileResponse(
        str(out_path), media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(display_name)},
    )
