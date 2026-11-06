"""騎縫章 —— 一個印章切成數片，蓋在連續幾頁上。

## 這支工具解決的事

合約、標案的實務作法：整疊文件蓋一個跨頁的章，**任何一頁被抽換或掉頁都看得出來**
（那一片對不起來）。紙本世界防抽換最直接的手段，數位化之後仍然被要求。

## 預覽為什麼要「拼回去」

只看單頁的預覽沒有意義 —— 使用者看到的是一條細片，判斷不出對不對。
所以預覽有兩種：①單頁看位置 ②**把整組的片拼回去**看是不是一個完整的章。
第二種才是使用者真正要確認的東西。
"""
from __future__ import annotations

import asyncio as _asyncio
import io
import uuid
from pathlib import Path
from typing import Optional

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from ...config import settings
from ...core import office_convert, pdf_preview, upload_owner as _uo
from ...core.asset_manager import asset_manager
from ...core.job_manager import job_manager
from ...core.pdf_guard import ensure_readable_pdf
from ...core.safe_paths import require_uuid_hex
from . import seam_core as SC
from . import stamp_source as SS

router = APIRouter()

#: 暫存檔前綴。**不可以含底線以外的分隔** —— `upload_owner.extract_upload_id`
#: 靠它切 id，切錯歸屬檢查會整個失效（pdf-watermark 的 `wm_` 踩過，v1.11.80）。
_PREFIX = "sm"


def _src(uid: str) -> Path:
    return settings.temp_dir / f"{_PREFIX}_{uid}.pdf"


def _out(uid: str) -> Path:
    return settings.temp_dir / f"{_PREFIX}_{uid}_out.pdf"


def _stamp_file(uid: str) -> Path:
    return settings.temp_dir / f"{_PREFIX}_{uid}_stamp.png"


def _spec_from_form(**kw) -> SC.SeamSpec:
    """表單 → SeamSpec。範圍夾在 `SeamSpec.normalized()`（伺服器端，API 也吃得到）。"""
    return SC.SeamSpec(
        mode=kw.get("mode") or "side",
        group=int(kw.get("group") or 2),
        edge=kw.get("edge") or "right",
        size_mm=float(kw.get("size_mm") or 40),
        offset_mm=float(kw.get("offset_mm") or 3),
        pos_mm=float(kw.get("pos_mm") or 0),
        angle_deg=float(kw.get("angle_deg") or 0),
        opacity=float(kw.get("opacity") or 1.0),
        jitter_pos=bool(kw.get("jitter_pos")),
        jitter_pos_mm=float(kw.get("jitter_pos_mm") or 12),
        jitter_angle=bool(kw.get("jitter_angle")),
        jitter_angle_deg=float(kw.get("jitter_angle_deg") or 4),
        seed=int(kw.get("seed") or 0),
    )


def _resolve_stamp(uid: str, *, source: str, asset_id: str, text: str,
                   shape: str, color: str, style: str,
                   double_ring: bool) -> bytes:
    """三種來源收斂成一份 PNG。上傳的那份先前已存進 `_stamp_file`。"""
    if source == "asset":
        if not asset_id:
            raise HTTPException(400, "請選擇一個印章資產")
        try:
            return SS.normalize_upload(SS.load_asset(asset_id))
        except ValueError as e:
            from ...core import asset_access
            # 受管制的章跟其他「找不到 / 壞掉」分開：是「不准」，不是請求錯
            raise HTTPException(403 if str(e) == asset_access.CONTROLLED_MESSAGE else 400, str(e))
    if source == "upload":
        p = _stamp_file(uid)
        if not p.exists():
            raise HTTPException(400, "還沒上傳印章圖")
        return p.read_bytes()
    return SS.generate(text, shape=shape, color=color, style=style,
                       double_ring=double_ring)


@router.get("/", response_class=HTMLResponse)
async def page(request: Request):
    """工具頁。印章資產清單先撈好，畫面才不用再打一次 API。"""
    from ...core import asset_access
    # 用印申請管制的章（僅限核准案件使用）不列出
    assets = [{"id": a.id, "name": a.name}
              for a in asset_manager.list(type="stamp") if not asset_access.is_controlled_asset(a)]
    return request.app.state.templates.TemplateResponse(
        request, "pdf_seam_stamp.html",
        {"request": request, "title": "騎縫章", "assets": assets})


@router.post("/load")
async def load(request: Request, file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "檔案是空的")
    name = file.filename or ""
    uid = uuid.uuid4().hex
    _uo.record(uid, request)
    dst = _src(uid)
    if data[:5] == b"%PDF-":
        dst.write_bytes(data)
    elif office_convert.is_office_file(name):
        raw = settings.temp_dir / f"{_PREFIX}raw_{uid}{Path(name).suffix}"
        raw.write_bytes(data)
        try:
            await office_convert.convert_to_pdf_async(raw, dst)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "文書檔轉換失敗，請確認檔案是否完整")
        finally:
            raw.unlink(missing_ok=True)
    else:
        raise HTTPException(400, "只支援 PDF 與文書檔")
    if not dst.exists():
        raise HTTPException(400, "檔案讀取失敗")
    n = ensure_readable_pdf(dst, min_pages=2)
    return {"upload_id": uid, "filename": name, "page_count": n,
            "converted": data[:5] != b"%PDF-"}


@router.post("/stamp-upload")
async def stamp_upload(request: Request, upload_id: str = Form(...),
                       file: UploadFile = File(...)):
    """上傳自己的印章圖。"""
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    data = await file.read()
    if not data:
        raise HTTPException(400, "印章圖是空的")
    try:
        # **不可以在事件迴圈上解圖**。解碼一張大圖要數秒，這幾秒內全站每個
        # 請求都在排隊（v1.14.31 實測 `/healthz` 從 74 ms 變成 19.6 秒）。
        from starlette.concurrency import run_in_threadpool
        png = await run_in_threadpool(SS.normalize_upload, data)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "印章圖讀取失敗（支援 PNG / JPG）")
    _stamp_file(upload_id).write_bytes(png)
    return {"ok": True}


@router.post("/stamp-preview")
async def stamp_preview(request: Request, upload_id: str = Form(...),
                        source: str = Form("generate"),
                        asset_id: str = Form(""), text: str = Form("騎縫章"),
                        shape: str = Form("circle"), color: str = Form("#c81414"),
                        style: str = Form("hei"), double_ring: bool = Form(True)):
    """印章本身長什麼樣（還沒切）。"""
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    png = _resolve_stamp(upload_id, source=source, asset_id=asset_id, text=text,
                         shape=shape, color=color, style=style,
                         double_ring=double_ring)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.post("/assembled")
async def assembled(request: Request, upload_id: str = Form(...),
                    source: str = Form("generate"), asset_id: str = Form(""),
                    text: str = Form("騎縫章"), shape: str = Form("circle"),
                    color: str = Form("#c81414"), style: str = Form("hei"),
                    double_ring: bool = Form(True), group: int = Form(2),
                    angle_deg: float = Form(0.0)):
    """把切片**拼回去**的預覽 —— 使用者真正要確認的是這個。

    只看單頁只會看到一條細片，判斷不出對不對。
    """
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    png = _resolve_stamp(upload_id, source=source, asset_id=asset_id, text=text,
                         shape=shape, color=color, style=style,
                         double_ring=double_ring)
    with fitz.open(str(src)) as doc:
        spec = SC.SeamSpec(group=group, angle_deg=angle_deg).normalized(doc.page_count)
        n = len(SC.make_groups(doc.page_count, spec.group)[0])
    return Response(content=SC.reconstruct(png, spec, n),
                    media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.post("/preview")
async def preview(request: Request, upload_id: str = Form(...),
                  page_no: int = Form(1), source: str = Form("generate"),
                  asset_id: str = Form(""), text: str = Form("騎縫章"),
                  shape: str = Form("circle"), color: str = Form("#c81414"),
                  style: str = Form("hei"), double_ring: bool = Form(True),
                  mode: str = Form("side"), group: int = Form(2),
                  edge: str = Form("right"), size_mm: float = Form(40),
                  offset_mm: float = Form(3), pos_mm: float = Form(0),
                  angle_deg: float = Form(0), opacity: float = Form(1.0),
                  jitter_pos: bool = Form(False),
                  jitter_pos_mm: float = Form(12),
                  jitter_angle: bool = Form(False),
                  jitter_angle_deg: float = Form(4),
                  seed: int = Form(0), large: bool = Form(False)):
    """單頁預覽：**真的把章蓋進 PDF 再算圖**（與送出走同一段程式）。"""
    def _work():
        # 這裡算的是預覽圖（純 CPU）。直接跑在事件迴圈上會讓**全站**
        # 在這段時間都不回應 —— 頁數多或 DPI 高時特別明顯。包成閉包
        # 丟到執行緒之後，慢的只有按下去的那個人自己。
        require_uuid_hex(upload_id, "upload_id")
        _uo.require(upload_id, request)
        src = _src(upload_id)
        if not src.exists():
            raise HTTPException(404, "檔案不存在（可能已過期）")
        png = _resolve_stamp(upload_id, source=source, asset_id=asset_id, text=text,
                             shape=shape, color=color, style=style,
                             double_ring=double_ring)
        spec = _spec_from_form(mode=mode, group=group, edge=edge, size_mm=size_mm,
                               offset_mm=offset_mm, pos_mm=pos_mm,
                               angle_deg=angle_deg, opacity=opacity,
                               jitter_pos=jitter_pos, jitter_pos_mm=jitter_pos_mm,
                               jitter_angle=jitter_angle,
                               jitter_angle_deg=jitter_angle_deg, seed=seed)
        png = SS.apply_opacity(png, spec.opacity)
        with fitz.open(str(src)) as doc:
            if page_no < 1 or page_no > doc.page_count:
                raise HTTPException(400, "頁碼超出範圍")
            # **只蓋要看的那一頁**。版位仍照整份文件算（切片編號取決於該頁
            # 在組裡的位置），但影像合成只做這一頁 —— 原本每看一頁就蓋滿
            # 整份，52 頁的文件配上前端同時發 20 個請求，每個要 90 秒。
            SC.apply_seam(doc, png, spec, only_pages={page_no - 1})
            pix = doc[page_no - 1].get_pixmap(dpi=150 if large else 78, alpha=False)
            return Response(content=pix.tobytes("png"), media_type="image/png",
                            headers={"Cache-Control": "no-store"})

    return await _asyncio.to_thread(_work)


@router.post("/submit")
async def submit(request: Request, upload_id: str = Form(...),
                 source: str = Form("generate"), asset_id: str = Form(""),
                 text: str = Form("騎縫章"), shape: str = Form("circle"),
                 color: str = Form("#c81414"), style: str = Form("hei"),
                 double_ring: bool = Form(True), mode: str = Form("side"),
                 group: int = Form(2), edge: str = Form("right"),
                 size_mm: float = Form(40), offset_mm: float = Form(3),
                 pos_mm: float = Form(0), angle_deg: float = Form(0),
                 opacity: float = Form(1.0), jitter_pos: bool = Form(False),
                 jitter_pos_mm: float = Form(12),
                 jitter_angle: bool = Form(False),
                 jitter_angle_deg: float = Form(4), seed: int = Form(0),
                 filename: str = Form("seam.pdf")):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    png = _resolve_stamp(upload_id, source=source, asset_id=asset_id, text=text,
                         shape=shape, color=color, style=style,
                         double_ring=double_ring)
    spec = _spec_from_form(mode=mode, group=group, edge=edge, size_mm=size_mm,
                           offset_mm=offset_mm, pos_mm=pos_mm,
                           angle_deg=angle_deg, opacity=opacity,
                           jitter_pos=jitter_pos, jitter_pos_mm=jitter_pos_mm,
                           jitter_angle=jitter_angle,
                           jitter_angle_deg=jitter_angle_deg, seed=seed)
    png = SS.apply_opacity(png, spec.opacity)
    out = _out(upload_id)
    stem = Path(filename).stem or "seam"

    def run(job):
        job.message = "蓋騎縫章…"
        job.progress = 0.2
        with fitz.open(str(src)) as doc:
            p = SC.apply_seam(doc, png, spec)
            doc.save(str(out), garbage=3, deflate=True)
        job.result_path = out
        job.result_filename = f"{stem}_seam.pdf"
        # 摘要要放 **meta** —— `to_public()` 只送 meta 出去，放 result 前端讀不到
        job.meta = dict(job.meta or {}, seam_result={
            "groups": len(p.groups), "seed": p.seed,
            "per_group": len(p.groups[0]) if p.groups else 0,
            "warnings": p.warnings,
        })
        job.progress = 1.0
        job.message = f"完成（{len(p.groups)} 組騎縫章）"

    job = job_manager.submit("pdf-seam-stamp", run, request=request,
                             meta={"filename": f"{stem}.pdf"})
    return {"job_id": job.id}


# ------------------------------------------------------------ 對外 API --

@router.post("/api/pdf-seam-stamp")
async def api(request: Request, file: UploadFile = File(...),
              text: str = Form("騎縫章"), shape: str = Form("circle"),
              color: str = Form("#c81414"), mode: str = Form("side"),
              group: int = Form(2), edge: str = Form("right"),
              size_mm: float = Form(40), offset_mm: float = Form(3),
              pos_mm: float = Form(0), angle_deg: float = Form(0),
              opacity: float = Form(1.0), jitter_pos: bool = Form(False),
              jitter_angle: bool = Form(False), seed: int = Form(0),
              stamp: Optional[UploadFile] = File(None)):
    """單次呼叫：上傳 PDF → 直接回蓋好騎縫章的 PDF。

    不給 `stamp` 就由系統依 `text` / `shape` / `color` 產生印章。
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "請提供檔案")
    uid = uuid.uuid4().hex
    src, out = _src(uid), _out(uid)
    try:
        if data[:5] == b"%PDF-":
            src.write_bytes(data)
        elif office_convert.is_office_file(file.filename or ""):
            raw = settings.temp_dir / f"{_PREFIX}raw_{uid}"
            raw.write_bytes(data)
            try:
                await office_convert.convert_to_pdf_async(raw, src)
            finally:
                raw.unlink(missing_ok=True)
        else:
            raise HTTPException(400, "只支援 PDF 與文書檔")
        # **公開 API 也要驗** —— 與網頁 `/load` 是不同的一段程式碼。
        ensure_readable_pdf(src, min_pages=2)
        png = (SS.normalize_upload(await stamp.read()) if stamp
               else SS.generate(text, shape=shape, color=color))
        spec = _spec_from_form(mode=mode, group=group, edge=edge,
                               size_mm=size_mm, offset_mm=offset_mm,
                               pos_mm=pos_mm, angle_deg=angle_deg,
                               opacity=opacity, jitter_pos=jitter_pos,
                               jitter_angle=jitter_angle, seed=seed)
        png = SS.apply_opacity(png, spec.opacity)
        with fitz.open(str(src)) as doc:
            if doc.page_count < 2:
                raise HTTPException(400, "騎縫章至少需要 2 頁")
            SC.apply_seam(doc, png, spec)
            doc.save(str(out), garbage=3, deflate=True)
        return FileResponse(str(out), media_type="application/pdf",
                            filename="seam.pdf")
    finally:
        src.unlink(missing_ok=True)


@router.get("/thumb/{upload_id}/{page_no}")
async def thumb(upload_id: str, page_no: int, request: Request):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    out = settings.temp_dir / f"{_PREFIX}_{upload_id}_t_{page_no}.png"
    if not out.exists():
        try:
            await pdf_preview.render_page_png_async(src, out, page_no - 1, dpi=70)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return FileResponse(str(out), media_type="image/png",
                        headers={"Cache-Control": "max-age=300"})
