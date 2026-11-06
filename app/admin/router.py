from __future__ import annotations

import asyncio as _asyncio
import asyncio
import io
import json
import re
import time
import uuid
from datetime import datetime, timezone
import zipfile
from pathlib import Path
from typing import Optional
from dataclasses import asdict

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

# **模組層要有 `settings`**。設定匯出 / 匯入的三個端點用了 `settings.temp_dir`，
# 但這個模組從來沒有 import 過它 —— 於是那三支一律 `NameError` 變成 500，
# 而頁面本身打得開，使用者只會以為是暫時性錯誤（v1.14.31 對抗式驗證抓到，
# 整組設定備份 / 還原是死的）。檔案裡別處寫的是函式內 `import ... as _s`，
# 所以 grep 得到字串卻沒有這個名字。
from ..config import settings
from ..logging_setup import get_logger
from ..core.asset_manager import PositionPreset, asset_manager
from ..core.conv_settings import BUILTIN_PATHS, conv_settings
from ..core.profile_manager import profile_manager
from ..core.synonym_manager import synonym_manager
from ..core import translation_glossary as _tg
from ..core import atomic_json
from ..core.template_manager import template_manager
from ..web.deps import require_admin

from fastapi import Depends
from ..core import safe_fetch as _safe_fetch

logger = get_logger(__name__)


def _export_stamp() -> str:
    """匯出檔裡的時間戳，用 ISO 8601 文字不用 Unix 秒數。

    純數字的 epoch 會被弱點掃描工具判為「Timestamp Disclosure」(Low) —— 它無法
    分辨那是「伺服器內部時間外洩」還是「這份檔案何時匯出的」。這裡本來就只是給人
    看的匯出時間，寫成人看得懂的格式既消掉告警，也比一串數字有用。
    """
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def build_router(templates) -> APIRouter:
    # Router-level dependency: every admin endpoint requires admin role when
    # auth is on (no-op when auth is off — require_admin returns synthetic).
    router = APIRouter(dependencies=[Depends(require_admin)])
    from . import seal_request_admin
    seal_request_admin.register(router, templates)   # 設定 → 用印申請

    @router.get("/", response_class=HTMLResponse)
    async def admin_home(request: Request):
        return RedirectResponse("/admin/assets", status_code=302)

    @router.get("/assets", response_class=HTMLResponse)
    async def assets_page(request: Request, type: Optional[str] = None):
        items = asset_manager.list(type=type) if type else asset_manager.list()
        return templates.TemplateResponse(request, 
            "asset_list.html",
            {"request": request, "assets": items, "type_filter": type},
        )

    @router.post("/assets/upload")
    async def assets_upload(
        request: Request,
        name: str = Form(...),
        type: str = Form("stamp"),
        remove_bg: bool = Form(False),
        file: UploadFile = File(...),
    ):
        data = await file.read()
        if not data:
            raise HTTPException(400, "empty file")
        # Accept PNG / JPEG directly; a PDF is rendered to PNG (first page) so a
        # scanned-stamp PDF can be used as an asset. Anything else → friendly 400
        # (rather than a 500 deep inside image processing). Validate by magic
        # bytes, not the (spoofable) content-type / filename.
        is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
        is_jpeg = data[:3] == b"\xff\xd8\xff"
        is_pdf = data[:4] == b"%PDF"
        # PDF 轉圖 + 自動裁邊是純 CPU 的重活（3 倍解析度算整頁）。直接跑在
        # 事件迴圈上會讓全站在這段時間不回應，所以丟到執行緒。這支端點有
        # `await file.read()`，不能像其他預覽端點那樣整段包起來。
        def _render_pdf_asset(data):
            if is_pdf:
                try:
                    import fitz
                    from PIL import Image as _Img
                    with fitz.open(stream=data, filetype="pdf") as _doc:
                        if _doc.page_count == 0:
                            raise HTTPException(400, "PDF 沒有任何頁面")
                        # Render at high DPI so the cropped stamp stays crisp.
                        pix = _doc[0].get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
                        data = pix.tobytes("png")
                    # A scanned stamp PDF is mostly paper with a small mark — auto-
                    # crop the margins so the asset is just the stamp, not a full A4
                    # page. A real chop is COLOURED ink (high chroma) or solid black
                    # (much darker than paper); scan artefacts (fold/scratch lines,
                    # shadows, paper noise) are near-grey and only slightly off the
                    # paper tone. So detect content as "high chroma OR clearly dark"
                    # and ignore the rest — this drops faint diagonal scan lines that
                    # a plain brightness threshold would keep. (Transparency is still
                    # handled separately by remove_bg.)
                    import numpy as _np
                    im = _Img.open(io.BytesIO(data)).convert("RGB")
                    w, h = im.size
                    arr = _np.asarray(im).astype(_np.int16)
                    chroma = arr.max(axis=2) - arr.min(axis=2)
                    bright = arr.max(axis=2)
                    cc = max(4, min(w, h) // 40)
                    corners_b = _np.concatenate([
                        bright[:cc, :cc].ravel(), bright[:cc, -cc:].ravel(),
                        bright[-cc:, :cc].ravel(), bright[-cc:, -cc:].ravel()])
                    paper_b = float(_np.median(corners_b))
                    content = (chroma > 40) | ((paper_b - bright) > 90)
                    ys, xs = _np.where(content)
                    if xs.size and ys.size:
                        x0, x1 = int(xs.min()), int(xs.max())
                        y0, y1 = int(ys.min()), int(ys.max())
                        if (x1 - x0) < w * 0.96 or (y1 - y0) < h * 0.96:
                            pad = max(6, min(w, h) // 80)
                            crop = (max(0, x0 - pad), max(0, y0 - pad),
                                    min(w, x1 + 1 + pad), min(h, y1 + 1 + pad))
                            buf = io.BytesIO()
                            im.crop(crop).save(buf, format="PNG")
                            data = buf.getvalue()
                except HTTPException:
                    raise
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("asset pdf render failed")
                    raise HTTPException(400, "PDF 轉圖片失敗，請確認是有效的 PDF。")

            return data

        if is_pdf:
            data = await _asyncio.to_thread(_render_pdf_asset, data)
        elif not (is_png or is_jpeg):
            raise HTTPException(
                400, "資產只接受 PNG / JPEG 圖片或 PDF（會自動取第一頁轉圖片）。請重新選擇檔案。")
        try:
            asset = asset_manager.create_from_bytes(
                name=name, type=type, png_bytes=data, remove_bg=remove_bg
            )
        except HTTPException:
            raise
        except Exception:
            import logging
            logging.getLogger(__name__).exception("asset upload failed")
            raise HTTPException(400, "圖片處理失敗，請確認是有效的 PNG / JPEG 圖片。")
        edit_url = f"/admin/assets/{asset.id}/edit"
        # AJAX caller (fetch) → JSON so errors stay on-page; plain form → 303.
        if "application/json" in (request.headers.get("accept") or ""):
            return JSONResponse({"ok": True, "edit_url": edit_url})
        return RedirectResponse(edit_url, status_code=303)

    @router.get("/assets/export")
    async def assets_export():
        """Bundle assets.json + every asset PNG into one zip for移轉/備份用。"""
        from ..config import settings as _s
        from ..core.http_utils import content_disposition

        meta = json.loads(_s.assets_meta_path.read_text(encoding="utf-8")) \
            if _s.assets_meta_path.exists() else {"assets": []}
        wrapped = {
            "_kind": "jt-doc-tools assets",
            "_version": 1,
            "_exported_at": _export_stamp(),
            "assets": meta.get("assets", []),
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("assets.json",
                        json.dumps(wrapped, ensure_ascii=False, indent=2))
            for a in wrapped["assets"]:
                aid = a.get("id", "")
                for fname in (f"{aid}.png", f"{aid}_thumb.png"):
                    fp = _s.assets_files_dir / fname
                    if fp.exists():
                        zf.write(fp, arcname=f"files/{fname}")
        ts = time.strftime("%Y%m%d_%H%M%S")
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition":
                     content_disposition(f"assets_export_{ts}.zip")},
        )

    @router.post("/assets/import")
    async def assets_import(
        file: UploadFile = File(...),
        mode: str = Form("merge"),  # "merge" | "replace"
    ):
        from ..config import settings as _s

        raw = await file.read()
        if not raw:
            raise HTTPException(400, "empty file")
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
            # zip 炸彈：判斷集中在 `zip_guard`，全站一份
            from ..core.zip_guard import check as _zip_check, ZipBombError
            _zip_check(zf)
        except zipfile.BadZipFile:
            raise HTTPException(400, "不是合法的 ZIP 檔")
        except ZipBombError:
            # 管理區一律**通用訊息**，細節進日誌（v1.12.86 的規矩）——
            # 即使這個例外的文字是我們自己寫的，也不要開這個口子。
            logger.warning("asset import rejected: zip bomb heuristics tripped")
            raise HTTPException(
                400, "這個備份檔解開後異常龐大，為了避免耗盡伺服器資源而拒絕匯入。")
        # 找 assets.json 在 zip 裡的位置（可能在 root，也可能在 assets/ 之類的
        # 子資料夾裡——使用者用 `zip -r assets/` 打包就會有 prefix）。
        # 找到後抓它的 parent dir 當所有檔案的 base prefix。
        meta_entry = next(
            (n for n in zf.namelist()
             if n.endswith("assets.json") and not n.startswith("__MACOSX/")),
            None,
        )
        if not meta_entry:
            raise HTTPException(400, "ZIP 內找不到 assets.json")
        prefix = meta_entry[: -len("assets.json")]   # "" 或 "assets/" 之類
        try:
            payload = json.loads(zf.read(meta_entry).decode("utf-8"))
        except Exception:
            raise HTTPException(400, "assets.json 解析失敗")
        # 同時接受我們的匯出格式 (有 _kind wrapper) 與裸 dict
        incoming = payload.get("assets") if isinstance(payload, dict) and "assets" in payload else payload
        if not isinstance(incoming, list):
            raise HTTPException(400, "assets.json 格式錯誤：應為 list 或 {assets:[...]}")

        existing = (json.loads(_s.assets_meta_path.read_text(encoding="utf-8"))
                    if _s.assets_meta_path.exists() else {"assets": []})

        if mode == "replace":
            # 砍掉現有的所有 PNG / thumb
            for old in existing.get("assets", []):
                aid = old.get("id", "")
                for fname in (f"{aid}.png", f"{aid}_thumb.png"):
                    fp = _s.assets_files_dir / fname
                    fp.unlink(missing_ok=True)
            existing = {"assets": []}

        # 為避免 id 撞到既有 asset，merge 模式下重新分配 id
        existing_ids = {a["id"] for a in existing["assets"]}
        added = 0
        for a in incoming:
            if not isinstance(a, dict) or "id" not in a:
                continue
            old_id = a["id"]
            new_id = old_id if mode == "replace" else (
                old_id if old_id not in existing_ids else uuid.uuid4().hex
            )
            # 檔案抽出來（path 用一開始偵測到的 prefix）
            try:
                png_bytes = zf.read(f"{prefix}files/{old_id}.png")
            except KeyError:
                continue  # PNG 缺失就跳過這筆
            (_s.assets_files_dir / f"{new_id}.png").write_bytes(png_bytes)
            try:
                thumb_bytes = zf.read(f"{prefix}files/{old_id}_thumb.png")
                (_s.assets_files_dir / f"{new_id}_thumb.png").write_bytes(thumb_bytes)
            except KeyError:
                pass  # 沒 thumb 就算了
            new_meta = dict(a)
            new_meta["id"] = new_id
            # 檔案是以 new_id 寫入磁碟（{new_id}.png / {new_id}_thumb.png），
            # file_key/thumb_key 必須同步,否則登錄指向舊 uuid 檔→縮圖 404 破圖。
            new_meta["file_key"] = f"{new_id}.png"
            new_meta["thumb_key"] = f"{new_id}_thumb.png"
            new_meta["updated_at"] = time.time()
            existing["assets"].append(new_meta)
            existing_ids.add(new_id)
            added += 1

        atomic_json.write_json(_s.assets_meta_path, existing)
        return {"ok": True, "mode": mode, "added": added,
                "total": len(existing["assets"])}

    @router.get("/assets/{asset_id}/edit", response_class=HTMLResponse)
    async def asset_edit_page(asset_id: str, request: Request):
        asset = asset_manager.get(asset_id)
        if not asset:
            raise HTTPException(404, "asset not found")
        # Self-heal: if the preset's aspect drifts from the image's natural
        # aspect by >10% the editor will visibly stretch the stamp. Snap it
        # back so users don't have to think about this.
        try:
            from PIL import Image
            file_path = asset_manager.file_path(asset)
            with Image.open(file_path) as im:
                w_px, h_px = im.size
            if w_px > 0 and h_px > 0:
                img_aspect = w_px / h_px
                p = asset.preset
                preset_aspect = (
                    (p.width_mm / p.height_mm) if p.height_mm > 0 else img_aspect
                )
                if abs(img_aspect - preset_aspect) / max(img_aspect, preset_aspect) > 0.1:
                    fixed = asset_manager.match_preset_aspect(asset_id)
                    if fixed:
                        asset = fixed
        except Exception:
            pass
        return templates.TemplateResponse(request, 
            "asset_edit.html",
            {"request": request, "asset": asset, "preset": asset.preset},
        )

    @router.post("/assets/{asset_id}/save")
    async def asset_save(asset_id: str, request: Request):
        body = await request.json()
        name = body.get("name")
        type_ = body.get("type")
        preset_dict = body.get("preset")
        preset = PositionPreset(**preset_dict) if preset_dict else None
        asset = asset_manager.update(asset_id, name=name, type=type_, preset=preset)
        if not asset:
            raise HTTPException(404, "asset not found")
        return {"ok": True, "asset": asset.to_dict()}

    @router.get("/assets/{asset_id}/watermark-preview")
    async def asset_watermark_preview(
        asset_id: str,
        opacity: float = 0.25,
        rotation: float = 30.0,
        size: float = 60.0,
        gap: float = 30.0,
    ):
        """Render a sample A4 page with the asset tiled as a watermark.
        Used by the asset edit page when the asset's type is ``watermark``."""
        def _work():
            # 這裡算的是預覽圖（純 CPU）。直接跑在事件迴圈上會讓**全站**
            # 在這段時間都不回應 —— 頁數多或 DPI 高時特別明顯。包成閉包
            # 丟到執行緒之後，慢的只有按下去的那個人自己。
            from pathlib import Path
            import uuid
            import fitz
            from ..config import settings as _s
            from ..tools.pdf_watermark import service as wm_service
            asset = asset_manager.get(asset_id)
            if not asset:
                raise HTTPException(404, "asset not found")
            wm_path = asset_manager.file_path(asset)

            tmp_id = uuid.uuid4().hex
            sample_pdf = _s.temp_dir / f"wm_sample_{tmp_id}.pdf"
            out_pdf = _s.temp_dir / f"wm_sample_{tmp_id}_out.pdf"
            out_png = _s.temp_dir / f"wm_sample_{tmp_id}.png"
            try:
                # Build a blank A4 PDF as the canvas — caption it so the user can
                # see this is a sample, not their real document.
                doc = fitz.open()
                page = doc.new_page(width=595.28, height=841.89)  # A4 pt
                page.insert_text(
                    fitz.Point(40, 60), "Sample Page · 範例頁面",
                    fontsize=18, color=(0.55, 0.6, 0.7),
                )
                page.insert_text(
                    fitz.Point(40, 90),
                    "This is a preview of how your watermark will look "
                    "when applied to a document.",
                    fontsize=11, color=(0.6, 0.65, 0.75),
                )
                doc.save(str(sample_pdf)); doc.close()

                params = wm_service.WatermarkParams(
                    mode="tile",
                    opacity=max(0.05, min(1.0, float(opacity))),
                    rotation_deg=float(rotation),
                    tile_size_mm=max(10.0, float(size)),
                    gap_mm=max(0.0, float(gap)),
                )
                wm_service.apply_watermark(sample_pdf, out_pdf, wm_path, params)
                doc = fitz.open(str(out_pdf))
                pix = doc[0].get_pixmap(dpi=110, alpha=False)
                pix.save(str(out_png)); doc.close()
            finally:
                for p in (sample_pdf, out_pdf):
                    try: p.unlink()
                    except OSError: pass
            return FileResponse(
                str(out_png), media_type="image/png",
                headers={"Cache-Control": "no-store"},
            )

        return await _asyncio.to_thread(_work)

    @router.post("/assets/{asset_id}/crop")
    async def asset_crop(asset_id: str, request: Request):
        body = await request.json()
        # Crop rect is posted as fractions of the image (0..1) so the client
        # never has to read the pixel dimensions.
        try:
            x = float(body["x"]); y = float(body["y"])
            w = float(body["w"]); h = float(body["h"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "x, y, w, h required")
        asset = asset_manager.get(asset_id)
        if not asset:
            raise HTTPException(404, "asset not found")
        updated = asset_manager.crop(asset_id, x, y, w, h)
        if not updated:
            raise HTTPException(500, "crop failed")
        return {"ok": True, "asset": updated.to_dict()}

    @router.post("/assets/{asset_id}/match-aspect")
    async def asset_match_aspect(asset_id: str):
        updated = asset_manager.match_preset_aspect(asset_id)
        if not updated:
            raise HTTPException(404, "asset not found or image missing")
        return {"ok": True, "asset": updated.to_dict()}

    @router.post("/assets/{asset_id}/default")
    async def asset_set_default(asset_id: str):
        asset = asset_manager.set_default(asset_id)
        if not asset:
            raise HTTPException(404, "asset not found")
        return {"ok": True}

    @router.post("/assets/{asset_id}/delete")
    async def asset_delete(asset_id: str):
        ok = asset_manager.delete(asset_id)
        if not ok:
            raise HTTPException(404, "asset not found")
        from ..core import seal_policy
        seal_policy.forget_asset(asset_id)   # 用印申請的管制設定一起清掉
        return {"ok": True}

    @router.get("/assets/{asset_id}/file")
    async def asset_file(asset_id: str):
        asset = asset_manager.get(asset_id)
        if not asset:
            raise HTTPException(404, "asset not found")
        return FileResponse(str(asset_manager.file_path(asset)), media_type="image/png")

    @router.get("/assets/{asset_id}/thumb")
    async def asset_thumb(asset_id: str):
        asset = asset_manager.get(asset_id)
        if not asset:
            raise HTTPException(404, "asset not found")
        return FileResponse(str(asset_manager.thumb_path(asset)), media_type="image/png")

    # Public API used by tool pages (e.g. the stamp tool lists stamps)
    @router.get("/api/assets")
    async def api_assets_list(type: Optional[str] = None):
        items = asset_manager.list(type=type) if type else asset_manager.list()
        return {"assets": [a.to_dict() for a in items]}

    # ---------- Company profile ----------
    @router.get("/profile", response_class=HTMLResponse)
    async def profile_page(request: Request, cid: Optional[str] = None):
        companies = profile_manager.list_companies()
        edit_id = cid or profile_manager.active_id()
        profile = profile_manager.get(edit_id)
        sections = profile_manager.get_sections_for_edit(edit_id)
        return templates.TemplateResponse(request, 
            "profile_edit.html",
            {
                "request": request,
                "profile": profile,
                "sections": sections,
                "companies": companies,
                "edit_id": edit_id,
                "active_id": profile_manager.active_id(),
            },
        )

    @router.post("/profile/save")
    async def profile_save(request: Request):
        body = await request.json()
        cid = (body.get("cid") or "").strip()
        if not cid:
            raise HTTPException(400, "cid required")
        name = (body.get("name") or "").strip()
        keys = body.get("keys") or []
        labels = body.get("labels") or {}
        values = body.get("values") or {}
        fields = {k: values.get(k, "") for k in keys}
        label_map = {k: labels.get(k, k) for k in keys}
        profile_manager.save(cid, name, fields, label_map)
        return {"ok": True}

    @router.post("/profile/create")
    async def profile_create(request: Request):
        body = await request.json()
        name = (body.get("name") or "新公司").strip()
        copy_from = body.get("copy_from") or None
        company = profile_manager.create(name, copy_from_id=copy_from)
        return {"ok": True, "id": company["id"]}

    @router.post("/profile/{cid}/activate")
    async def profile_activate(cid: str):
        ok = profile_manager.set_active(cid)
        if not ok:
            raise HTTPException(404, "company not found")
        return {"ok": True}

    @router.post("/profile/{cid}/delete")
    async def profile_delete(cid: str):
        ok = profile_manager.delete(cid)
        if not ok:
            raise HTTPException(400, "cannot delete (not found or last remaining)")
        return {"ok": True}

    @router.get("/profile/{cid}/export")
    async def profile_export(cid: str):
        company = profile_manager.get(cid)
        if not company:
            raise HTTPException(404, "company not found")
        payload = {
            "_kind": "jt-doc-tools company profile",
            "_version": 1,
            "_exported_at": _export_stamp(),
            "name": company.get("name", cid),
            "fields": company.get("fields", {}),
            "labels": company.get("labels", {}),
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        from ..core.http_utils import content_disposition
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": content_disposition(f"profile_{payload['name']}.json")},
        )

    @router.post("/profile/import")
    async def profile_import(
        file: UploadFile = File(...),
        mode: str = Form("create"),    # "create" | "overwrite"
        target_cid: str = Form(""),    # used when mode == "overwrite"
    ):
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "empty file")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            raise HTTPException(400, "檔案不是合法 JSON")
        fields = payload.get("fields")
        labels = payload.get("labels") or {}
        if not isinstance(fields, dict):
            raise HTTPException(400, "JSON 缺少 'fields' 欄位或格式錯誤")
        name = (payload.get("name") or "匯入的公司").strip()
        if mode == "overwrite" and target_cid:
            profile_manager.save(target_cid, name, fields, labels)
            return {"ok": True, "id": target_cid, "name": name}
        # Default: create new company
        new_co = profile_manager.create(name)
        profile_manager.save(new_co["id"], name, fields, labels)
        return {"ok": True, "id": new_co["id"], "name": name}

    # ---------- 翻譯對照字典 ----------
    @router.get("/mfp", response_class=HTMLResponse)
    async def mfp_shared_page(request: Request):
        """共用事務機（事務機掃描取件 Beta）：管理員建立事務機、指定成員。跟工具頁同一份模板。"""
        from ..tools.mfp_fetch.router import page_context
        return templates.TemplateResponse(request, "mfp_fetch.html", page_context(request, "shared"))

    @router.get("/translation-glossary", response_class=HTMLResponse)
    async def glossary_page(request: Request):
        terms = _tg.load()
        return templates.TemplateResponse(request, "translation_glossary.html", {
            "request": request,
            "terms": [asdict(t) for t in terms],
            "max_terms": _tg.MAX_TERMS,
            "langs": _tg.lang_choices(),
        })

    @router.post("/translation-glossary/save")
    async def glossary_save(request: Request):
        body = await request.json()
        rows = body.get("terms")
        if not isinstance(rows, list):
            raise HTTPException(400, "terms 要是陣列")
        if len(rows) > _tg.MAX_TERMS:
            raise HTTPException(400, f"字典最多 {_tg.MAX_TERMS} 條")
        terms, seen = [], set()
        for i, raw in enumerate(rows, start=1):
            try:
                t = _tg.normalise(raw if isinstance(raw, dict) else {})
            except ValueError as e:
                # **指出是第幾條** —— 只說「格式錯誤」的話，一份幾百條的
                # 字典要自己一條一條找。
                raise HTTPException(400, f"第 {i} 條：{e}") from None
            k = _tg.key_of(t)
            if k in seen:
                raise HTTPException(400, f"第 {i} 條：同一個語言對裡「{t.source}」重複了")
            seen.add(k)
            terms.append(t)
        _tg.save(terms)
        from ..core import audit_db, client_ip as _cip
        user = getattr(request.state, "user", None)
        audit_db.log_event("glossary_change",
                           username=(user or {}).get("username", ""),
                           ip=_cip.real_client_ip(request),
                           details={"terms": len(terms)})
        return {"ok": True, "terms": len(terms)}

    @router.post("/translation-glossary/preview")
    async def glossary_preview(request: Request):
        """貼一段文字，看會命中哪幾條。

        **設完看不出有沒有生效，等於沒設** —— 詞邊界、大小寫、最長優先
        這些規則從表格上看不出來，要能當場試。
        """
        body = await request.json()
        text = str(body.get("text") or "")[:5000]
        src = str(body.get("src_lang") or "")
        tgt = str(body.get("tgt_lang") or "")
        m = _tg.matcher_for(src, tgt)
        masked, mapping = _tg.protect(text, m)
        return {"hits": [{"source": t.source, "target": t.replacement(),
                          "mode": t.mode} for t in m.find(text)],
                "masked": masked, "count": len(mapping)}

    @router.get("/translation-glossary/export")
    async def glossary_export():
        """CSV 匯出。企業一定有現成的對照表，要進得來也出得去。"""
        import csv
        import io as _io
        from ..core import csv_safe as _csv_safe
        from ..core.http_utils import content_disposition
        buf = _io.StringIO()
        w = csv.writer(buf)
        w.writerow(["source", "target", "src_lang", "tgt_lang",
                    "mode", "case_sensitive", "note", "enabled"])
        for t in _tg.load():
            w.writerow(_csv_safe.row([t.source, t.target,
                                      t.src_lang, t.tgt_lang, t.mode,
                                      "1" if t.case_sensitive else "0",
                                      t.note, "1" if t.enabled else "0"]))
        return Response(
            content=buf.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     content_disposition("translation_glossary.csv")})

    # ---------- PDF label synonyms ----------
    @router.get("/synonyms", response_class=HTMLResponse)
    async def synonyms_page(request: Request):
        syns = synonym_manager.get_map()
        # Ensure every profile key has a row even if empty — gives user a
        # scaffold to add synonyms for newly-added profile fields.
        profile_keys = list(profile_manager.get()["fields"].keys())
        rows = []
        for k in profile_keys:
            rows.append({"key": k, "synonyms": syns.get(k, [])})
        # Append keys present only in the synonyms file (e.g. pre-seeded)
        for k, v in syns.items():
            if k not in profile_keys:
                rows.append({"key": k, "synonyms": v})
        return templates.TemplateResponse(request, 
            "synonyms_edit.html",
            {"request": request, "rows": rows},
        )

    @router.post("/synonyms/save")
    async def synonyms_save(request: Request):
        body = await request.json()
        rows = body.get("rows") or []
        mapping: dict[str, list[str]] = {}
        for r in rows:
            key = (r.get("key") or "").strip()
            if not key:
                continue
            raw = r.get("synonyms") or ""
            if isinstance(raw, str):
                syns = [s.strip() for s in raw.split(",") if s.strip()]
            else:
                syns = [str(s).strip() for s in raw if str(s).strip()]
            mapping[key] = syns
        synonym_manager.save_map(mapping)
        return {"ok": True}

    @router.post("/synonyms/add")
    async def synonyms_add(request: Request):
        body = await request.json()
        key = (body.get("key") or "").strip()
        syn = (body.get("synonym") or "").strip()
        if not key or not syn:
            raise HTTPException(400, "key and synonym required")
        changed = synonym_manager.add_synonym(key, syn)
        return {"ok": True, "changed": changed}

    @router.get("/synonyms/export")
    async def synonyms_export():
        payload = {
            "_kind": "jt-doc-tools synonyms",
            "_version": 1,
            "_exported_at": _export_stamp(),
            "synonyms": synonym_manager.get_map(),
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        from ..core.http_utils import content_disposition
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": content_disposition("label_synonyms.json")},
        )

    @router.post("/synonyms/import")
    async def synonyms_import(
        file: UploadFile = File(...),
        mode: str = Form("merge"),    # "merge" (新增 + 覆蓋現有 key) | "replace" (整個換掉)
    ):
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "empty file")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            raise HTTPException(400, "檔案不是合法 JSON")
        # 支援兩種輸入格式：
        #   1. 我們自己的匯出格式：{"_kind": ..., "synonyms": {...}}
        #   2. 直接的 dict[str, list[str]]（給人手寫 / 從別處來的最小檔）
        incoming = payload.get("synonyms") if isinstance(payload, dict) and "synonyms" in payload else payload
        if not isinstance(incoming, dict):
            raise HTTPException(400, "JSON 結構錯誤：應為 {key: [同義詞...]} 或包成 {synonyms: {...}}")
        # 正規化：每個 value 都要是 list[str]
        clean: dict[str, list[str]] = {}
        for k, v in incoming.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if isinstance(v, list):
                syns = [str(s).strip() for s in v if str(s).strip()]
            elif isinstance(v, str):
                syns = [s.strip() for s in v.split(",") if s.strip()]
            else:
                continue
            clean[k.strip()] = syns
        if not clean:
            raise HTTPException(400, "沒有可匯入的有效條目")
        if mode == "replace":
            synonym_manager.save_map(clean)
            return {"ok": True, "mode": "replace", "count": len(clean)}
        # merge: keep existing, override on key collision, union list values
        existing = synonym_manager.get_map()
        added, overridden = 0, 0
        for k, syns in clean.items():
            if k in existing:
                # union — 不丟掉舊有同義詞
                merged = list(dict.fromkeys(existing[k] + syns))
                if merged != existing[k]:
                    existing[k] = merged
                    overridden += 1
            else:
                existing[k] = syns
                added += 1
        synonym_manager.save_map(existing)
        return {"ok": True, "mode": "merge", "added": added, "overridden": overridden}

    # ---------- Form templates ----------
    @router.get("/templates", response_class=HTMLResponse)
    async def templates_page(request: Request):
        items = template_manager.list_all()
        return templates.TemplateResponse(request, 
            "templates_list.html",
            {"request": request, "templates": items},
        )

    @router.post("/templates/{tid}/delete")
    async def templates_delete(tid: str):
        ok = template_manager.delete(tid)
        if not ok:
            raise HTTPException(404, "template not found")
        return {"ok": True}

    @router.post("/templates/{tid}/rename")
    async def templates_rename(tid: str, request: Request):
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name required")
        ok = template_manager.rename(tid, name)
        if not ok:
            raise HTTPException(404, "template not found")
        return {"ok": True}

    # ---------- Conversion settings (LibreOffice / OxOffice paths) ----------
    @router.get("/conversion", response_class=HTMLResponse)
    async def conversion_page(request: Request):
        from ..core.office_convert import find_soffice
        all_paths = conv_settings.list_paths()
        active = find_soffice()
        active_version = ""
        if active:
            for p in all_paths:
                if p["path"] == active:
                    active_version = p.get("version", "")
                    break
        return templates.TemplateResponse(request, 
            "conversion_edit.html",
            {
                "request": request,
                "paths": all_paths,
                "active": active,
                "active_version": active_version,
            },
        )

    @router.post("/conversion/save")
    async def conversion_save(request: Request):
        body = await request.json()
        order = body.get("builtin_order") or []
        custom = body.get("custom") or []
        if not isinstance(order, list) or not isinstance(custom, list):
            raise HTTPException(400, "格式錯誤")
        conv_settings.save_order([str(x) for x in order], [str(x) for x in custom])
        return {"ok": True}

    # ---------- 語音服務（jtlw）整合設定 ----------
    #
    # **沒設定好 → 工具反灰**（使用者 2026-09-21 指示）。判準在
    # `jtlw_settings.is_configured()`，側欄與首頁由 `main._SETUP_CHECKS` 取用 ——
    # 這裡不要再寫第二份判斷。

    @router.get("/jtlw", response_class=HTMLResponse)
    async def jtlw_page(request: Request):
        from ..core import jtlw_settings as _j
        return templates.TemplateResponse(
            request, "admin_jtlw.html",
            {"request": request, "s": _j.get(),
             # 指紋是算出來的不是存的 —— 存兩份一定會漂
             "ca_fingerprint": _j.ca_fingerprint(),
             # 側欄的反灰是**伺服器渲染**的。存檔之後設定齊不齊變了的話
             # 要重新載入，不然使用者得自己按重新整理（2026-09-22 回報）。
             # 前端拿它跟存檔回傳的 `configured` 比，**只在真的翻轉時才重載**。
             "configured": _j.is_configured()})

    @router.post("/api/jtlw/settings")
    async def api_jtlw_save(request: Request):
        from ..core import jtlw_settings as _j
        from app.core.url_safety import safe_remote_base_url
        body = await request.json() or {}
        out: dict = {}
        if "enabled" in body:
            out["enabled"] = bool(body["enabled"])
        for key in ("base_url", "audio_base_url"):
            if key not in body:
                continue
            raw = (body.get(key) or "").strip()
            if raw:
                try:
                    # 管理員填的位址是 SSRF 的入口 —— 走既有那一份判斷，
                    # 不要在這裡另寫一套（遠端 OCR 走的同一道）。
                    safe_remote_base_url(raw)
                except ValueError as exc:
                    label = "送件位址" if key == "base_url" else "錄音檔對外位址"
                    return JSONResponse(
                        {"ok": False, "error": f"{label}不合法：{exc}"}, status_code=400)
            out[key] = raw
        if "profile_id" in body:
            out["profile_id"] = (body.get("profile_id") or "").strip() or _j.DEFAULT_PROFILE
        if "ca_cert_pem" in body:
            pem = (body.get("ca_cert_pem") or "").strip()
            if pem:
                # **貼進來的東西要先確認是一張憑證** —— 貼錯的話連線會在
                # 送件當下才失敗，而訊息是 ssl 的內部錯誤，看不出是這裡貼壞了。
                import ssl as _ssl
                try:
                    _ssl.PEM_cert_to_DER_cert(pem + "\n")
                except (ValueError, TypeError):
                    return JSONResponse(
                        {"ok": False, "error": "這不是一張 PEM 憑證（要以 "
                                               "-----BEGIN CERTIFICATE----- 開頭）"},
                        status_code=400)
            out["ca_cert_pem"] = pem
        if "verify_tls" in body:
            out["verify_tls"] = bool(body["verify_tls"])
        if "request_timeout" in body:
            try:
                out["request_timeout"] = max(5, min(300, int(body["request_timeout"])))
            except (TypeError, ValueError):
                pass
        if "retry_window_hours" in body:
            # 最多 24 小時（JTLW 的條件：那是會議內容）；0 ＝ 存好就請它刪除
            try:
                out["retry_window_hours"] = max(0, min(24, int(body["retry_window_hours"])))
            except (TypeError, ValueError):
                pass
        # 金鑰：空字串代表「不更動」（管理頁不會把已存的金鑰顯示出來），
        # 要清掉請用專屬的按鈕 —— 不然手滑清空欄位按儲存就把金鑰弄丟了。
        key_in = body.get("api_key_enc")
        if key_in:
            out["api_key_enc"] = key_in if key_in == _j.SECRET_KEPT else str(key_in).strip()
        _j.save(out)
        # **設定變更要留稽核紀錄**（其他設定頁都有，這一頁原本沒有 ——
        # 2026-09-29 要查「有沒有人切過辨識模式」時才發現查不到）。
        # 金鑰與憑證**本身絕不寫進紀錄**，只記有沒有動到。
        from ..core import audit_db, client_ip as _cip
        detail = {k: out[k] for k in ("enabled", "base_url", "audio_base_url", "profile_id",
                                      "verify_tls", "request_timeout", "retry_window_hours")
                  if k in out}
        if "ca_cert_pem" in out:
            detail["ca_cert"] = "已更新" if out["ca_cert_pem"] else "已清除"
        if "api_key_enc" in out:
            detail["api_key_status"] = "不變" if out["api_key_enc"] == _j.SECRET_KEPT else "已更新"
        user = getattr(request.state, "user", None)
        audit_db.log_event("settings_change",
                           username=(user or {}).get("username", ""),
                           ip=_cip.real_client_ip(request), target="jtlw",
                           details=detail)
        return {"ok": True, "configured": _j.is_configured(),
                "ca_fingerprint": _j.ca_fingerprint()}

    def _jtlw_pick(value, locale: str) -> str:
        """對方的 `name` / `description` 是**逐語言的物件**，不是字串。

        ```json
        "name": {"zh-Hant": "會議（平衡）", "en": "Meeting (balanced)", "ja": "…"}
        ```

        第一版直接 `str(...)` —— 那會把整個 dict 的 repr 印到下拉選單上
        （`{'zh-Hant': '會議（平衡）', …}`）。**跟 `progress` 那次同一個病：
        我照自己以為的形狀寫，而對方的文件早就寫著真正的形狀。**
        """
        if isinstance(value, dict):
            for key in (locale, "zh-Hant", "en"):
                if value.get(key):
                    return str(value[key])
            for v in value.values():
                if v:
                    return str(v)
            return ""
        return str(value or "")

    @router.get("/api/jtlw/profiles")
    async def api_jtlw_profiles():
        """對方提供的處理設定清單。

        **不在我們這邊抄一份** —— 抄了就會漂，而且管理員打錯要等到送件才失敗。
        讀不到時回 `ok: false` 與原因，讓設定頁**保留目前的值**並講出為什麼，
        不要變成一個空的下拉（那看起來像「沒有可選的」）。
        """
        from ..core import jtlw_client as _c
        try:
            rows = _c.JtlwClient(timeout=10).profiles()
        except _c.JtlwError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)
        from ..core.ui_locale import current_locale
        loc = current_locale() or "zh-Hant"
        out = []
        for r in rows:
            if isinstance(r, str):
                out.append({"id": r, "label": r, "description": "", "languages": []})
            elif isinstance(r, dict) and r.get("id"):
                out.append({"id": str(r["id"]),
                            "label": _jtlw_pick(r.get("name"), loc) or str(r["id"]),
                            "description": _jtlw_pick(r.get("description"), loc),
                            "deprecated": bool(r.get("deprecated")),
                            "replacement_profile_id": r.get("replacement_profile_id") or "",
                            "languages": r.get("languages") or [],
                            # 設定頁要講得出「這個模式不做發言者分離」（台語模式）
                            "capabilities": r.get("capabilities") or []})
        # **停用的模式不列進下拉**（對方 v2.22：`meeting.detailed` 標為停用，仍收件、照替代模式處理）
        # —— 例外是**目前存著的就是它**：藏起來的話下拉會無聲換成別的值，管理員一按儲存就改掉了設定。
        # 那時留著並講出該改用哪一個。判斷放在伺服器端（前端只照著畫），才測得到。
        from ..core import jtlw_settings as _j
        saved = str(_j.get().get("profile_id") or "")
        labels = {p["id"]: p["label"] for p in out}
        visible = [p for p in out if not p["deprecated"] or p["id"] == saved]
        cur = next((p for p in out if p["id"] == saved and p["deprecated"]), None)
        current_deprecated = None
        if cur:
            rep_id = cur["replacement_profile_id"]
            current_deprecated = {"id": saved, "replacement_id": rep_id,
                                  "replacement_label": labels.get(rep_id, rep_id)}
        return {"ok": True, "profiles": visible,
                "hidden_deprecated": len(out) - len(visible),
                "current_deprecated": current_deprecated}

    @router.post("/api/jtlw/test")
    async def api_jtlw_test():
        """連線測試。

        **兩段都要跑**：`/health` 免認證（位址對不對），`/capabilities` 要金鑰
        （身分對不對）。只打 health 的話，金鑰錯的時候也會回「連得上」。
        """
        from ..core import jtlw_client as _c
        try:
            client = _c.JtlwClient(timeout=10)
            client.health()
            cap = client.capabilities()
        except _c.JtlwError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)
        return {"ok": True, "api_revision": cap.api_revision, "limits": cap.limits}

    # ---------- LLM 校驗附加功能設定 ----------
    # All endpoints fail-soft: never raise to break the admin page even if
    # the LLM backend is unreachable.

    @router.get("/llm-settings", response_class=HTMLResponse)
    async def llm_settings_page(request: Request):
        from ..core.llm_settings import llm_settings, DEFAULT_SETTINGS, LLMSettingsManager
        # **工具數從資料算，不寫死在文字裡** —— 這一頁原本寫著 10、側欄寫 12、
        # 實際是 13，三處三個數字。`KNOWN_LLM_TOOLS` 裡有子項（`pdf-ocr-vision`
        # 是 `pdf-ocr` 的視覺模型設定，不是另一支工具），所以只算真的註冊過的工具。
        registered = {t["id"]: t for t in (templates.env.globals.get("nav_tools") or [])}
        llm_ids = [k["id"] for k in LLMSettingsManager.KNOWN_LLM_TOOLS if k["id"] in registered]
        llm_only = [registered[i]["name"] for i in registered
                    if registered[i].get("requires_setup") == "llm"]
        return templates.TemplateResponse(request, 
            "llm_settings.html",
            {
                "request": request,
                "settings": llm_settings.get(),
                "defaults": DEFAULT_SETTINGS,
                "known_llm_tools": LLMSettingsManager.KNOWN_LLM_TOOLS,
                "llm_tool_count": len(llm_ids),
                "llm_only_tool_names": llm_only,
                # 翻譯並行數旁邊要講出**實際**同時送出幾個：還受「外部服務同時呼叫數」限制，
                # 兩個設定在不同頁、小的那個說了算（客戶 2026-09-30 問「未來能平行處理嗎」——
                # 其實早就支援，只是被這個預設 1 卡住，畫面上沒講）。
                "remote_limit_now": _remote_limit_now(),
            },
        )

    def _remote_limit_now() -> int:
        try:
            from ..core import concurrency_settings as _cs
            return int(_cs.get().get("max_remote_concurrent") or 1)
        except Exception:      # noqa: BLE001 — 讀不到就不顯示那句
            return 0

    @router.get("/api/llm/settings")
    async def api_llm_settings_get():
        from ..core.llm_settings import llm_settings
        return llm_settings.get()

    @router.post("/api/llm/settings")
    async def api_llm_settings_save(request: Request):
        from ..core.llm_settings import llm_settings
        from ..core.llm_client import _validate_llm_base_url
        body = await request.json() or {}
        bu = (body.get("base_url") or "").strip()
        if bu:
            try:
                _validate_llm_base_url(bu)
            except ValueError as exc:
                # v1.5.8: 把 controlled ValueError 映射到 fixed-string 訊息
                msg = str(exc)
                user_msg = (
                    "Base URL 必須是 http(s):// 開頭" if "scheme" in msg else
                    "Base URL 不可為空" if "non-empty" in msg else
                    "Base URL 必須包含 host" if "include a host" in msg else
                    "Base URL host 已被列入黑名單" if "blocked" in msg else
                    "Base URL 格式錯誤"
                )
                return JSONResponse({"ok": False, "error": user_msg}, status_code=400)
        # 數值欄位 clamp 防呆（避免 0 / 負數 / 超大值）。
        def _clamp_int(key, lo, hi):
            if key in body:
                try:
                    body[key] = max(lo, min(hi, int(body[key])))
                except (TypeError, ValueError):
                    body.pop(key, None)  # 非數值就丟掉，保留原值
        _clamp_int("translate_max_sentences", 100, 200000)
        _clamp_int("translate_page_size", 20, 5000)
        _clamp_int("translate_concurrency", 1, 64)
        _clamp_int("doctr_batch_segments", 1, 100)
        _clamp_int("doctr_batch_chars", 200, 20000)
        _clamp_int("doctr_max_units", 100, 100000)
        # 開關一律存成布林 —— 字串 "false" 在 Python 裡是真值，存進去就等於打開
        for flag in ("enabled", "hide_when_disabled", "debug_log"):
            if flag in body and not isinstance(body[flag], bool):
                body[flag] = str(body[flag]).strip().lower() in ("1", "true", "on", "yes")
        # 其他 LLM 伺服器與各工具的伺服器：檢查（位址、名稱、有工具指到不存在的那一台）在
        # `llm_settings.update()` 裡做，不合格就**整筆不存**、回固定訊息（不回例外字串）。
        from ..core.llm_settings import LLMSettingsError, SERVER_ERRORS
        rep: dict = {}
        try:
            out = llm_settings.update(body, report=rep)
        except LLMSettingsError as exc:
            code = next((c for c in SERVER_ERRORS if c == exc.code), "server_list")
            resp = {"ok": False, "error": SERVER_ERRORS[code], "code": code}
            if code == "server_in_use":
                # 哪幾支工具：照送來的設定重算（不從例外帶出去）
                srv_ids = {str((x or {}).get("id") or "") for x in (body.get("servers") or [])
                           if isinstance(x, dict)}
                spt = body.get("server_per_tool") or {}
                resp["tools"] = sorted(str(t) for t, v in (spt.items() if isinstance(spt, dict) else [])
                                       if v and str(v) not in srv_ids)
            return JSONResponse(resp, status_code=400)
        # admin 改了 LLM 設定（base_url / model / 其他）— 把 model profile cache
        # 全部清掉，下次 LLM call 會重抓 capabilities
        try:
            from ..core import llm_model_profile as _lmp
            _lmp.invalidate(None)
        except Exception:
            pass
        # **設定變更要留稽核紀錄**（同語音服務設定頁）。金鑰本身絕不寫進紀錄，
        # 只記「不變 / 已更新 / 已清除」—— 全站那一把與每一台伺服器的都一樣。
        try:
            from ..core import audit_db, client_ip as _cip
            detail = {k: out.get(k) for k in ("enabled", "base_url", "model", "timeout_seconds")
                      if k in body}
            for k in ("model_per_tool", "server_per_tool"):
                if k in body:
                    detail[k] = out.get(k) or {}
            detail.update(rep)
            user = getattr(request.state, "user", None)
            audit_db.log_event("settings_change",
                               username=(user or {}).get("username", ""),
                               ip=_cip.real_client_ip(request), target="llm",
                               details=detail)
        except Exception:      # noqa: BLE001 — 稽核寫不進去不可以讓存檔失敗
            logger.warning("LLM 設定的稽核紀錄寫入失敗", exc_info=True)
        return out

    @router.post("/api/llm/test-connection")
    async def api_llm_test_connection(request: Request):
        """測試 LLM 連線。

        管理頁的「測試連線」按鈕送的是**還沒存檔**的那組設定（所以要收 body）。
        但**沒有 body 時要退回已存檔的設定** —— 從命令列問「我的 LLM 通不通」
        的人不會知道要自己把 base_url 抄一遍，而 `await request.json()` 對空
        body 會丟例外，一路變成 `400 Invalid JSON body`：一個看起來像
        「你送錯東西」的錯誤，其實是我們的文件沒寫參數（v1.15.34 照 API.md
        逐條實跑時抓到）。
        """
        from ..core.llm_client import LLMClient
        from ..core.llm_settings import llm_settings, SECRET_KEPT as _LLM_KEPT
        try:
            body = await request.json()
        except Exception:      # noqa: BLE001 — 空 body / 非 JSON 都退回存檔值
            body = {}
        if not isinstance(body, dict):
            body = {}
        # `server_id`：測的是「其他 LLM 伺服器」裡的那一台（沒給＝全站那一台）。
        sid = str(body.get("server_id") or "").strip().lower()
        if sid:
            saved_srv = llm_settings.server(sid)
            if saved_srv is None and not body.get("base_url"):
                return {"ok": False, "error": "找不到這台 LLM 伺服器（可能還沒儲存）"}
            saved_target = saved_srv or {}
        else:
            saved_target = llm_settings.get()
        saved = saved_target if not body.get("base_url") else {}
        base_url = ((body.get("base_url") or saved.get("base_url") or "")).strip()
        # 金鑰：頁面送來的是 `SECRET_KEPT`（沒改）或新的明文。**存著的金鑰只送給存檔時
        # 的那個位址** —— 不然在表單上改一個還沒存的位址按「測試連線」，
        # 就等於把金鑰交給任意一台主機（而頁面本身已經看不到金鑰了）。
        # 「其他 LLM 伺服器」每一台也一樣：只有位址跟**那一台**存檔時的一樣才帶它的金鑰。
        raw_key = body.get("api_key") if body.get("base_url") else None
        if raw_key == _LLM_KEPT or not body.get("base_url"):
            same_host = (base_url.rstrip("/")
                         == (saved_target.get("base_url") or "").strip().rstrip("/"))
            if not same_host:
                api_key = None
            elif sid:
                api_key = llm_settings.server_api_key(sid)
            else:
                api_key = llm_settings.api_key()
        else:
            api_key = (str(raw_key or "")).strip() or None
        timeout = float(body.get("timeout_seconds")
                        or saved.get("timeout_seconds") or 10)
        if not base_url:
            return {"ok": False, "error": "Base URL 未填"}
        # Cap test timeout at 30s so admin page doesn't hang
        try:
            client = LLMClient(base_url=base_url, api_key=api_key, timeout=min(timeout, 30))
        except ValueError as exc:
            # v1.5.8: 把 controlled ValueError 訊息映射到 fixed-string 表,不直接
            # 把 exception 訊息當回傳值（消 CodeQL py/stack-trace-exposure）
            msg = str(exc)
            user_msg = (
                "Base URL 必須是 http(s):// 開頭" if "scheme" in msg else
                "Base URL 不可為空" if "non-empty" in msg else
                "Base URL 必須包含 host" if "include a host" in msg else
                "Base URL host 已被列入黑名單" if "blocked" in msg else
                "Base URL 格式錯誤"
            )
            return {"ok": False, "error": user_msg}
        result = await _asyncio.to_thread(client.test_connection)
        # **順便檢查模型是不是還在「思考」**（只有管理員按按鈕時才做，開頁面不做 ——
        # 會讓對方把模型載進 GPU）。思考沒被關掉時翻譯會慢很多倍，而畫面上完全看不出來。
        thinking_check = None
        probe_model = str(body.get("probe_model") or "").strip()
        if result.ok and probe_model:
            import asyncio as _aio
            try:
                thinking_check = await _aio.to_thread(client.thinking_probe, probe_model)
            except Exception:  # noqa: BLE001 — 模型不存在、逾時…：不影響連線結果
                # 模型名稱是頁面送來的：換行拿掉，不然可以在記錄裡偽造一行
                logger.warning("思考檢查失敗（模型 %s）",
                               probe_model[:80].replace("\r", " ").replace("\n", " "),
                               exc_info=True)
                thinking_check = {"error": "檢查失敗（原因記在服務記錄）"}
        # 上下文長度（Ollama 才看得到）：上面那一問已經把模型載入，這時 `/api/ps` 讀得到實際大小。
        # 太小時長一點的文件會被**安靜地截掉開頭的指令**，結果亂掉而沒有錯誤（2026-10-08 使用者要求提示）。
        context_check = None
        if result.ok and probe_model:
            import asyncio as _aio
            try:
                context_check = await _aio.to_thread(client.context_probe, probe_model)
                context_check["recommended"] = client.CONTEXT_RECOMMENDED
            except Exception:  # noqa: BLE001
                logger.warning("上下文長度檢查失敗", exc_info=True)
                context_check = None
        return {
            "ok": result.ok,
            "latency_ms": result.latency_ms,
            "error": result.error,
            "thinking_check": thinking_check,
            "context_check": context_check,
            "models": [
                {
                    "id": m.id,
                    "owned_by": m.owned_by,
                    "size_bytes": m.size_bytes,
                    "looks_vision": m.looks_vision,
                }
                for m in result.models
            ],
        }

    @router.post("/api/llm/context-variant")
    async def api_llm_context_variant(request: Request):
        """在 Ollama 上建一個上下文較大的版本（`<模型>-ctx32k`），回新名字。

        **只對已存檔的伺服器**（全站那一台，或 `server_id` 那一台）—— 不收頁面送來的位址：
        這支會在對方伺服器上建東西，不可以變成「替任意主機建模型」。原本的模型不動。
        建好之後要不要改用、存不存檔由管理員在頁面上決定。"""
        from ..core.llm_client import LLMClient, LLMError
        from ..core.llm_settings import llm_settings
        try:
            body = await request.json()
        except Exception:      # noqa: BLE001
            body = {}
        if not isinstance(body, dict):
            body = {}
        sid = str(body.get("server_id") or "").strip().lower()
        if sid:
            srv = llm_settings.server(sid)
            if srv is None:
                return JSONResponse({"ok": False, "error": "找不到這台 LLM 伺服器（可能還沒儲存）"}, 404)
            base_url, api_key = (srv.get("base_url") or "").strip(), llm_settings.server_api_key(sid)
        else:
            base_url, api_key = (llm_settings.get().get("base_url") or "").strip(), llm_settings.api_key()
        if not base_url:
            return JSONResponse({"ok": False, "error": "LLM 伺服器位址還沒存檔"}, 400)
        model = str(body.get("model") or "").strip()
        try:
            num_ctx = int(body.get("num_ctx") or 0)
        except (TypeError, ValueError):
            num_ctx = 0
        try:
            client = LLMClient(base_url=base_url, api_key=api_key, timeout=120)
            new = await _asyncio.to_thread(client.make_context_variant, model, num_ctx)
        except (ValueError, LLMError) as exc:
            # 訊息是我們自己寫的固定句子（make_context_variant 裡），不含位址與例外內容
            return JSONResponse({"ok": False, "error": str(exc) if isinstance(exc, LLMError)
                                 else "LLM 伺服器位址不合法"}, 400)
        try:
            from ..core import audit_db, client_ip as _cip
            user = getattr(request.state, "user", None)
            audit_db.log_event(
                "settings_change", username=(user or {}).get("username", ""),
                ip=_cip.real_client_ip(request), target="llm_context_variant",
                details={"model": model[:200], "num_ctx": num_ctx, "created": new,
                         "server_id": sid or "default"})
        except Exception:      # noqa: BLE001 — 稽核失敗不影響結果
            logger.warning("寫稽核紀錄失敗", exc_info=True)
        return {"ok": True, "model": new}

    @router.get("/api/llm/models")
    async def api_llm_models(server: str = ""):
        """List models from the *currently saved* settings. Returns empty
        list if not enabled / connection fails.

        `?server=<編號>`：列「其他 LLM 伺服器」裡那一台（**已存檔**的位址與金鑰）的模型 ——
        各工具選了那一台之後，模型下拉要列的是那一台的清單。這個不看 LLM 是否啟用：
        管理員在設定的時候就要看得到。
        """
        import asyncio as _aio
        from ..core.llm_settings import llm_settings
        if server:
            sid = server.strip().lower()
            if not re.fullmatch(r"[0-9a-f]{12}", sid):
                return {"ok": False, "error": "找不到這台 LLM 伺服器（可能還沒儲存）", "models": []}
            try:
                client = llm_settings.make_server_client(sid, timeout=30)
            except ValueError:
                return {"ok": False, "error": "這台 LLM 伺服器的位址不合格", "models": []}
            if client is None:
                return {"ok": False, "error": "找不到這台 LLM 伺服器（可能還沒儲存）", "models": []}
        else:
            client = llm_settings.make_client()
            if client is None:
                return {"ok": False, "error": "LLM 未啟用", "models": []}
        result = await _aio.to_thread(client.test_connection)
        return {
            "ok": result.ok,
            "error": result.error,
            "models": [
                {
                    "id": m.id,
                    "size_bytes": m.size_bytes,
                    "looks_vision": m.looks_vision,
                }
                for m in result.models
            ],
        }

    # ---------- Fonts ----------
    @router.get("/fonts", response_class=HTMLResponse)
    async def fonts_page(request: Request):
        from ..core import font_catalog
        # admin 頁要看到隱藏的字型才能取消隱藏，所以 include_hidden=True
        fonts = font_catalog.list_fonts(include_hidden=True)
        groups: dict[str, list] = {}
        for f in fonts:
            groups.setdefault(f["category"], []).append(f)
        titles = {
            "custom":   "自訂上傳字型",
            "taiwan":   "台灣系統字型",
            "free-cjk": "開源 CJK 字型",
            "cjk":      "其他 CJK 字型",
            "latin":    "西文開源字型",
            "pymupdf":  "PyMuPDF 內建",
        }
        ordered = []
        for key in ("custom", "taiwan", "free-cjk", "cjk", "latin", "pymupdf"):
            if key in groups:
                ordered.append({"key": key, "title": titles[key], "fonts": groups[key]})
        visible_count = sum(1 for f in fonts if not f.get("hidden"))
        hidden_count = sum(1 for f in fonts if f.get("hidden"))
        return templates.TemplateResponse(request, 
            "fonts.html",
            {
                "request": request,
                "groups": ordered,
                "total": len(fonts),
                "visible_count": visible_count,
                "hidden_count": hidden_count,
            },
        )

    @router.post("/fonts/refresh")
    async def fonts_refresh():
        from ..core import font_catalog
        font_catalog.refresh_cache()
        return {"ok": True, "total": len(font_catalog.list_fonts(include_hidden=True))}

    @router.post("/fonts/toggle-hidden")
    async def fonts_toggle_hidden(request: Request):
        """切換指定 font id 的隱藏狀態。隱藏後工具的字型選單看不到，
        但檔案仍保留（取消隱藏立刻復原）。"""
        from ..core import font_catalog
        body = await request.json()
        font_id = str(body.get("id") or "")
        if not font_id:
            raise HTTPException(400, "id required")
        hidden = font_catalog.get_hidden_ids()
        if font_id in hidden:
            hidden.discard(font_id)
            new_state = False
        else:
            hidden.add(font_id)
            new_state = True
        font_catalog.set_hidden_ids(list(hidden))
        return {"ok": True, "id": font_id, "hidden": new_state,
                "hidden_count": len(hidden)}

    @router.post("/fonts/bulk-hidden")
    async def fonts_bulk_hidden(request: Request):
        """批次設定一組 font id 的隱藏狀態（給「全部隱藏 / 全部顯示」用）。
        body: {ids: [...], hidden: bool}"""
        from ..core import font_catalog
        body = await request.json()
        ids = body.get("ids") or []
        if not isinstance(ids, list):
            raise HTTPException(400, "ids must be a list")
        target_hidden = bool(body.get("hidden"))
        hidden = font_catalog.get_hidden_ids()
        for fid in ids:
            sid = str(fid or "")
            if not sid:
                continue
            if target_hidden:
                hidden.add(sid)
            else:
                hidden.discard(sid)
        font_catalog.set_hidden_ids(list(hidden))
        return {"ok": True, "hidden_count": len(hidden), "applied": len(ids)}

    @router.post("/fonts/upload")
    async def fonts_upload(file: UploadFile = File(...)):
        from ..core import font_catalog
        if not file.filename:
            raise HTTPException(400, "missing filename")
        ext = Path(file.filename).suffix.lower()
        if ext not in (".ttf", ".otf", ".ttc"):
            raise HTTPException(400, "只支援 .ttf / .otf / .ttc")
        data = await file.read()
        if not data:
            raise HTTPException(400, "空檔")
        cdir = font_catalog.custom_fonts_dir()
        # Sanitize filename — keep stem + extension, strip path components.
        safe_name = Path(file.filename).name.replace("/", "_").replace("\\", "_")
        # 防 ascii filesystem encoding（host 沒設 UTF-8 locale）— 若原檔名是
        # CJK 但 sys.getfilesystemencoding() 是 ascii，write_bytes 會炸。
        # 偵測到就改用 hash + 副檔名做檔名，但 family 顯示名仍從 TTF name table
        # 讀（list_fonts 目前用 stem 顯示，未來再改）。新版 install.sh 已強制
        # LANG=C.UTF-8，這裡只是給舊安裝的 fallback。
        import sys as _sys
        if _sys.getfilesystemencoding().lower() not in ("utf-8", "utf8"):
            try:
                safe_name.encode(_sys.getfilesystemencoding())
            except UnicodeEncodeError:
                import hashlib as _hl
                ext = Path(safe_name).suffix.lower() or ".ttf"
                safe_name = _hl.sha256(safe_name.encode("utf-8")).hexdigest()[:16] + ext
        dst = cdir / safe_name
        # Avoid overwriting different files with the same name.
        if dst.exists():
            # Deduplicate by appending -1, -2, ... if content differs.
            existing = dst.read_bytes()
            if existing != data:
                stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
                i = 1
                while True:
                    cand = cdir / f"{stem}-{i}{suffix}"
                    if not cand.exists():
                        dst = cand
                        break
                    i += 1
        dst.write_bytes(data)
        font_catalog.refresh_cache()
        return {"ok": True, "filename": dst.name, "size": len(data)}

    @router.post("/fonts/rename")
    async def fonts_rename(request: Request):
        """改自訂上傳字型在介面上的顯示名稱。

        沒有這個功能時顯示的是檔名（`NotoSansTC-Regular`、`微軟正黑體`…），
        跟公司內部怎麼稱呼那套字型未必一樣 —— 而這個名稱會出現在 PDF 編輯器
        的字型下拉裡，是使用者每天看到的東西。

        只動顯示名稱，**不碰檔案本身** —— 改名不該有弄壞字型檔的風險。
        """
        from ..core import font_catalog
        body = await request.json()
        font_id = str(body.get("id") or "")
        if not font_id.startswith("custom:"):
            raise HTTPException(400, "只有自訂上傳的字型可以變更名稱")
        fname = font_id.split(":", 1)[1]
        # 只准動自訂字型資料夾裡真的存在的檔 —— 否則等於讓人往設定檔塞任意鍵
        cdir = font_catalog.custom_fonts_dir().resolve()
        target = (cdir / fname).resolve()
        if cdir not in target.parents and target != cdir:
            raise HTTPException(400, "invalid path")
        if not target.exists():
            raise HTTPException(404, "font not found")
        name = str(body.get("name") or "")
        if len(name) > font_catalog.CUSTOM_NAME_MAX:
            raise HTTPException(
                400, f"名稱不得超過 {font_catalog.CUSTOM_NAME_MAX} 個字")
        font_catalog.set_custom_name(target.name, name)
        return {"ok": True, "name": font_catalog.get_custom_names().get(target.name, "")}

    @router.post("/fonts/delete")
    async def fonts_delete(request: Request):
        from ..core import font_catalog
        body = await request.json()
        font_id = str(body.get("id") or "")
        if not font_id.startswith("custom:"):
            raise HTTPException(400, "只能刪除自訂字型")
        fname = font_id.split(":", 1)[1]
        # Resolve safely: must live inside custom fonts dir.
        cdir = font_catalog.custom_fonts_dir().resolve()
        target = (cdir / fname).resolve()
        if cdir not in target.parents and target != cdir:
            raise HTTPException(400, "invalid path")
        if not target.exists():
            raise HTTPException(404, "font not found")
        target.unlink()
        font_catalog.refresh_cache()
        return {"ok": True}

    # ---------- API Tokens ----------
    def _token_owner_labels() -> dict:
        """Token 擁有者的顯示名稱：`帳號@來源`（本機、LDAP、SSO 同名的是不同帳號，只寫帳號會認錯人）。"""
        from ..core import auth_db, auth_settings
        if not auth_settings.is_enabled():
            return {}
        rows = auth_db.conn().execute("SELECT id, username, source, display_name FROM users").fetchall()
        return {r["id"]: {"label": f"{r['username']}@{r['source']}", "name": r["display_name"] or ""} for r in rows}

    def _token_audit(request: Request, action: str, label: str, **extra) -> None:
        # Token 本身一律不記（記名稱與動作）；遮罩也不記
        from ..core import audit_db, client_ip as _cip
        user = getattr(request.state, "user", None)
        audit_db.log_event("api_token_change", username=(user or {}).get("username", ""),
                           ip=_cip.real_client_ip(request), target=label or "",
                           details={"action": action, **extra})

    @router.get("/api-tokens", response_class=HTMLResponse)
    async def api_tokens_page(request: Request):
        from ..core.api_tokens import api_tokens
        from ..core import auth_settings
        return templates.TemplateResponse(request, 
            "api_tokens.html",
            {
                "request": request,
                "tokens": api_tokens.list_full(),
                "enforce": api_tokens.is_enforced(),
                "auth_enabled": auth_settings.is_enabled(),
                "owners": _token_owner_labels(),
            },
        )

    @router.post("/api/tokens/create")
    async def api_tokens_create(request: Request):
        from ..core.api_tokens import api_tokens
        from ..core import auth_settings
        body = await request.json()
        label = str(body.get("label") or "").strip()
        t = api_tokens.create(label or "unnamed")
        # 啟用認證時，新 Token 預設綁給建立的人（之後可以改綁）。沒有擁有者的 Token 連不了 MCP，
        # 而且網頁上原本沒有地方可以綁（v1.17.6 以前），建好就卡住
        owner = None
        user = getattr(request.state, "user", None) or {}
        if auth_settings.is_enabled() and user.get("user_id"):
            owner = int(user["user_id"])
            api_tokens.assign_owner(t.token, owner)
        _token_audit(request, "create", t.label, owner_user_id=owner)
        return {"ok": True, "token": t.token, "label": t.label, "owner_user_id": owner}

    @router.post("/api/tokens/revoke")
    async def api_tokens_revoke(request: Request):
        from ..core.api_tokens import api_tokens
        body = await request.json()
        tok = str(body.get("token") or "")
        row = api_tokens.find(tok)
        ok = api_tokens.revoke(tok)
        if ok:
            _token_audit(request, "revoke", (row or {}).get("label", ""))
        return {"ok": ok}

    @router.post("/api/tokens/enforce")
    async def api_tokens_enforce(request: Request):
        from ..core.api_tokens import api_tokens
        body = await request.json()
        api_tokens.set_enforce(bool(body.get("enforce")))
        _token_audit(request, "enforce", "", enforce=api_tokens.is_enforced())
        return {"ok": True, "enforce": api_tokens.is_enforced()}

    @router.get("/api/tokens/owner-candidates")
    async def api_tokens_owner_candidates(request: Request, q: str = ""):
        """綁定使用者用：依帳號、名稱、信箱搜尋啟用中的使用者（最多 20 筆）。"""
        from ..core import auth_settings, user_manager
        if not auth_settings.is_enabled():
            return {"ok": True, "users": []}
        page = user_manager.list_users_page(q=q.strip()[:64], limit=20, enabled=True)
        return {"ok": True, "users": [
            {"id": r["id"], "label": f"{r['username']}@{r['source']}", "name": r.get("display_name") or ""}
            for r in page["rows"]]}

    @router.post("/api/tokens/owner")
    async def api_tokens_owner(request: Request):
        """把 Token 綁給一個使用者（`user_id`），或解除綁定（`null`）。Token 的權限就是那個人的權限。"""
        from ..core.api_tokens import api_tokens
        from ..core import auth_db, auth_settings
        if not auth_settings.is_enabled():
            raise HTTPException(400, "未啟用認證時 Token 不綁使用者")
        body = await request.json()
        tok = str(body.get("token") or "")
        uid = body.get("user_id")
        if uid is not None:
            if isinstance(uid, bool) or not isinstance(uid, int):
                raise HTTPException(400, "user_id 要是整數或 null")
            row = auth_db.conn().execute("SELECT id, enabled FROM users WHERE id=?", (uid,)).fetchone()
            if not row:
                raise HTTPException(400, "找不到這個使用者")
            if not row["enabled"]:
                raise HTTPException(400, "這個使用者已停用")
        row = api_tokens.find(tok)
        if row is None or not api_tokens.assign_owner(tok, uid):
            raise HTTPException(404, "找不到這張 Token")
        owner = _token_owner_labels().get(uid, {}).get("label") if uid is not None else None
        _token_audit(request, "owner", row.get("label") or "", owner=owner)
        return {"ok": True, "owner_user_id": uid, "owner": owner}

    # ---- 系統相依套件檢查 -----------------------------------------------------
    @router.get("/sys-deps", response_class=HTMLResponse)
    async def sys_deps_page(request: Request):
        # 不在這裡 collect — 改由前端載完頁面後 fetch /admin/api/sys-deps，避免
        # 探測 binaries / 跑 subprocess 阻塞首屏（之前進頁要等 ~1-2 秒）。
        from ..main import VERSION as _app_version
        return templates.TemplateResponse(request, 
            "sys_deps.html",
            {
                "request": request,
                "deps": [],  # placeholder — JS will populate
                "app_version": _app_version,
            },
        )

    # v1.7.48：probe 結果記憶體快取，60s TTL。慢機器（單一 probe 5-10s）
    # 開頁多次 refresh / admin 在不同 tab 開不會重跑全套 probe。「重新檢查」
    # 按鈕會帶 ?force=1 強制重跑。
    _sys_deps_cache: dict = {"ts": 0.0, "data": None}

    @router.get("/api/sys-deps")
    async def sys_deps_api(request: Request):
        """JSON 版本，給外部監控 / API token 呼叫者用 (符合「所有功能須有 API」規範)。

        Query string `?force=1` 跳過快取，強制重跑 probes。
        """
        from ..core.sys_deps import collect_sys_deps
        import time as _time
        force = request.query_params.get("force") in ("1", "true", "yes")
        now = _time.time()
        cache_age = now - _sys_deps_cache["ts"]
        if not force and _sys_deps_cache["data"] is not None and cache_age < 60.0:
            return {"deps": _sys_deps_cache["data"], "cached": True,
                    "cache_age": round(cache_age, 1)}
        try:
            data = await asyncio.to_thread(collect_sys_deps)
            _sys_deps_cache["data"] = data
            _sys_deps_cache["ts"] = now
            return {"deps": data, "cached": False}
        except Exception:
            # v1.5.8: 不漏 stack trace（CodeQL py/stack-trace-exposure）
            import logging as _lg
            _lg.getLogger("app.admin").exception("collect_sys_deps failed")
            return {"deps": [], "error": "系統相依套件收集失敗,請查 server log"}

    @router.post("/api/check-latest-version")
    async def check_latest_version():
        """Admin 主動點按時才呼叫，從 GitHub raw 抓 main branch 的
        pyproject.toml 解析 version。比 Releases API 可靠（repo 沒打 release
        tag 時 /releases/latest 會 404）。timeout 8 秒避免卡住。"""
        from ..main import VERSION as _app_version
        import urllib.request
        import urllib.error
        import re as _re
        import logging as _lg
        url = "https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/pyproject.toml"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": f"jt-doc-tools/{_app_version} (sys-deps version check)"},
            )
            with _safe_fetch.urlopen(req, timeout=8) as resp:
                content = resp.read().decode("utf-8", errors="replace")
            # 解析 [project] version = "x.y.z"
            m = _re.search(r'^\s*version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', content, _re.M)
            if not m:
                return {"ok": False, "error": "解析 pyproject.toml 失敗（找不到 version 欄位）",
                        "current": _app_version}
            tag = m.group(1).strip()

            def _parse(v: str):
                try:
                    return tuple(int(x) for x in v.split(".")[:3])
                except Exception:
                    return ()
            cur_t = _parse(_app_version)
            latest_t = _parse(tag)
            if cur_t and latest_t:
                if latest_t > cur_t:    is_outdated = True; status = "outdated"
                elif latest_t < cur_t:  is_outdated = False; status = "newer"  # dev build
                else:                    is_outdated = False; status = "current"
            else:
                is_outdated = False
                status = "unknown"
            return {
                "ok": True,
                "current": _app_version,
                "latest": tag,
                "html_url": "https://github.com/jasoncheng7115/jt-doc-tools/blob/main/CHANGELOG.md",
                "published_at": "",
                "is_outdated": is_outdated,
                "status": status,
            }
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"GitHub raw HTTP {e.code}", "current": _app_version}
        except Exception:
            _lg.getLogger("app.admin").exception("check_latest_version failed")
            return {"ok": False, "error": "無法連到 GitHub（網路 / DNS 問題）", "current": _app_version}

    # ---- 企業 logo / 識別 -----------------------------------------------------
    def _br_default_app_name() -> str:
        from ..config import settings as _s
        return _s.app_name

    @router.get("/branding", response_class=HTMLResponse)
    async def branding_page(request: Request):
        from ..core import branding
        return templates.TemplateResponse(request, 
            "admin_branding.html",
            {
                "request": request,
                "has_custom": branding.has_custom_logo(),
                "logo_url": branding.custom_logo_url(),
                "max_mb": branding.MAX_LOGO_BYTES // 1024 // 1024,
                "max_dim": branding.MAX_LOGO_DIMENSION,
                "default_site_name": _br_default_app_name(),
                "current_site_name": branding.get_site_name(default=_br_default_app_name()),
                "has_custom_site_name": branding.has_custom_site_name(),
                "appearance": branding.get_appearance(),
                "sidebar_themes": branding.theme_list(),
                "builtin_logos": branding.builtin_logo_list(),
                "hero_glows": branding.hero_glow_list(),
                "hero_titles": branding.hero_title_list(),
            },
        )

    @router.post("/branding/upload")
    async def branding_upload(file: UploadFile = File(...)):
        from ..core import branding
        data = await file.read()
        try:
            branding.save_logo(data, file.filename or "")
        except ValueError:
            # v1.5.8: 不漏 stack trace,通用訊息（CodeQL py/stack-trace-exposure）
            raise HTTPException(400, "Logo 檔案格式錯誤,僅支援 PNG / JPG / SVG")
        from ..core import notify_email_assets
        notify_email_assets.invalidate()      # 通知信裡夾的 Logo 跟著換（原本要重啟才換）
        return {"ok": True, "url": branding.custom_logo_url()}

    @router.post("/branding/reset")
    async def branding_reset():
        from ..core import branding, notify_email_assets
        removed = branding.reset_logo()
        notify_email_assets.invalidate()
        return {"ok": True, "had_custom": removed}

    @router.post("/branding/appearance")
    async def branding_appearance(request: Request):
        """內建 Logo（`builtin_logo`：`jt` / `doc`）與左側選單配色（`sidebar_theme`），
        送其中一項或兩項。回改完之後的設定與側欄現在該用的 Logo 網址（畫面上立即換）。"""
        from ..core import audit_db, branding, notify_email_assets
        from ..core import client_ip as _cip
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, "需要 JSON 物件")
        theme, logo = body.get("sidebar_theme"), body.get("builtin_logo")
        glow, title = body.get("hero_glow"), body.get("hero_title")
        if theme is None and logo is None and glow is None and title is None:
            raise HTTPException(400, "請指定 sidebar_theme、builtin_logo、hero_glow 或 hero_title")
        try:
            out = branding.set_appearance(
                sidebar_theme=str(theme) if theme is not None else None,
                builtin_logo=str(logo) if logo is not None else None,
                hero_glow=str(glow) if glow is not None else None,
                hero_title=str(title) if title is not None else None)
        except ValueError:
            raise HTTPException(400, "不認得的配色或 Logo 代號")
        notify_email_assets.invalidate()
        user = getattr(request.state, "user", None)
        audit_db.log_event("settings_change", username=(user or {}).get("username", ""),
                           ip=_cip.real_client_ip(request), target="branding",
                           details={k: v for k, v in (("sidebar_theme", theme), ("builtin_logo", logo),
                                                      ("hero_glow", glow), ("hero_title", title))
                                    if v is not None})
        return {"ok": True, **out, "sidebar_tone": branding.sidebar_tone(),
                "sidebar_logo": branding.logo_url("sidebar"), "icon_logo": branding.logo_url("icon")}

    @router.get("/api/branding")
    async def branding_api():
        from ..core import branding
        return {
            "has_custom": branding.has_custom_logo(),
            "logo_url": branding.custom_logo_url(),
            "site_name": branding.get_site_name(default=_br_default_app_name()),
            "has_custom_site_name": branding.has_custom_site_name(),
            **branding.get_appearance(),
            "sidebar_themes": branding.theme_list(),
            "builtin_logos": branding.builtin_logo_list(),
            "hero_glows": branding.hero_glow_list(),
            "hero_titles": branding.hero_title_list(),
        }

    @router.post("/branding/site-name")
    async def branding_site_name(request: Request):
        from ..core import branding
        body = await request.json()
        name = str(body.get("name") or "").strip()
        try:
            branding.set_site_name(name)
        except ValueError:
            # v1.5.8: 通用訊息防 stack-trace-exposure
            raise HTTPException(400, "站名格式錯誤(限 1-64 字元)")
        return {
            "ok": True,
            "site_name": branding.get_site_name(default=_br_default_app_name()),
            "is_custom": branding.has_custom_site_name(),
        }

    # ---- 全站設定匯出 / 匯入 -------------------------------------------------
    @router.get("/settings-export", response_class=HTMLResponse)
    async def settings_export_page(request: Request):
        from ..core import settings_export, scheduled_export
        return templates.TemplateResponse(request,
            "admin_settings_export.html",
            {
                "request": request,
                "categories": settings_export.list_categories(),
                "sched": scheduled_export.get_settings(),
            },
        )

    @router.post("/settings-export/download")
    async def settings_export_download(request: Request):
        from ..core import settings_export
        from ..main import VERSION
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        selected = body.get("selected")
        selected = list(selected) if selected is not None else None
        out_name = (
            f"jtdt-settings-{time.strftime('%Y%m%d-%H%M%S')}-v{VERSION}.zip"
        )
        out_path = settings.temp_dir / out_name
        result = settings_export.export_to_zip(out_path, selected, app_version=VERSION)
        return FileResponse(
            str(out_path), media_type="application/zip",
            filename=out_name, headers={"X-File-Count": str(result["file_count"])},
        )

    def _stash_import_zip(data: bytes) -> Path:
        if not data:
            raise HTTPException(400, "empty file")
        if len(data) > 200 * 1024 * 1024:
            raise HTTPException(400, "import file too large (>200 MB)")
        zip_path = settings.temp_dir / f"settings_import_{uuid.uuid4().hex}.zip"
        zip_path.write_bytes(data)
        return zip_path

    @router.post("/settings-export/preview")
    async def settings_export_preview(file: UploadFile = File(...)):
        """Read the uploaded backup's manifest so the UI can show which
        categories are inside for the admin to pick from. Keeps the zip in
        temp and returns its token so import can reuse it without re-upload."""
        from ..core import settings_export
        zip_path = _stash_import_zip(await file.read())
        try:
            manifest = settings_export.read_manifest(zip_path)
        except (ValueError, FileNotFoundError):
            import logging as _lg
            _lg.getLogger("app.admin").exception("preview_settings failed")
            try: zip_path.unlink()
            except OSError: pass
            raise HTTPException(400, "無法讀取備份檔（可能不是 jt-doc-tools 設定備份）")
        return {"token": zip_path.stem, "manifest": manifest}

    @router.post("/settings-export/import")
    async def settings_export_import(request: Request):
        """Restore selected categories from a previously-previewed backup.
        Body: {token, selected:[category ids]}."""
        from ..core import settings_export
        from ..main import VERSION
        body = await request.json()
        token = (body.get("token") or "").strip()
        if not re.fullmatch(r"settings_import_[0-9a-f]{32}", token):
            raise HTTPException(400, "無效的匯入 token")
        selected = body.get("selected")
        selected = list(selected) if selected is not None else None
        zip_path = settings.temp_dir / f"{token}.zip"
        if not zip_path.exists():
            raise HTTPException(410, "匯入檔已過期，請重新上傳")
        try:
            result = settings_export.import_from_zip(
                zip_path, selected, app_version=VERSION)
        except (ValueError, FileNotFoundError):
            import logging as _lg
            _lg.getLogger("app.admin").exception("import_settings failed")
            raise HTTPException(400, "匯入設定失敗，請檢查 server log 取詳細錯誤")
        finally:
            try: zip_path.unlink()
            except OSError: pass
        return result

    @router.post("/settings-export/schedule")
    async def settings_export_schedule(request: Request):
        from ..core import scheduled_export
        body = await request.json()
        try:
            saved = scheduled_export.save_settings(body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "settings": saved}

    @router.post("/settings-export/run-now")
    async def settings_export_run_now():
        from ..core import scheduled_export
        try:
            res = scheduled_export.run_export_now()
        except Exception as e:  # noqa: BLE001
            import logging as _lg
            _lg.getLogger("app.admin").exception("run_export_now failed")
            raise HTTPException(400, f"排程匯出測試失敗：{type(e).__name__}")
        return {"ok": True, "result": res}

    @router.get("/api/settings-export/categories")
    async def settings_export_categories_api():
        from ..core import settings_export
        return {"categories": settings_export.list_categories()}

    # ---------- OCR 訓練檔（tesseract trained data） ----------
    @router.get("/ocr-langs", response_class=HTMLResponse)
    async def ocr_langs_page(request: Request):
        from ..core import tessdata_manager as _tm
        from ..core import ocr_engine as _oe
        from ..core import ocr_remote_settings as _ors
        from ..core import sys_deps as _sd
        def _ors_is_configured() -> bool:
            d = _ors.get()
            return bool(d.get("enabled")) or (bool(d.get("url")) and bool(d.get("token")))
        catalog = _tm.catalog_with_status()
        tessdata_dir = _tm.get_tessdata_dir()
        groups: dict[str, list] = {}
        for item in catalog:
            groups.setdefault(item["group"], []).append(item)
        group_titles = [
            ("cjk",     "中日韓 CJK"),
            ("western", "西方語言"),
            ("other",   "東南亞 / 其他"),
        ]
        ordered = [{"key": k, "title": t, "items": groups.get(k, [])} for k, t in group_titles]
        installed_count = sum(1 for c in catalog if c["installed"])
        # 加總含 fast + best 變體的實際 disk 用量
        total_size_mb = round(
            sum(c.get("fast_size_mb", 0) + c.get("best_size_mb", 0) for c in catalog),
            1,
        )
        catalog_codes = {c["code"] for c in catalog}
        extra_installed = sorted([c for c in _tm.get_installed_langs() if c not in catalog_codes])
        return templates.TemplateResponse(request, 
            "admin_ocr_langs.html",
            {
                "request": request,
                "groups": ordered,
                "tessdata_dir": str(tessdata_dir) if tessdata_dir else "",
                "tessdata_writable": _tm.can_write_tessdata(),
                "installed_count": installed_count,
                "total_count": len(catalog),
                "total_size_mb": total_size_mb,
                "extra_installed": extra_installed,
                "default_quality": _tm.get_default_quality(),
                "default_engine": _oe.get_default_engine(),
                "easyocr_available": _oe.is_easyocr_available(),
                "tesseract_available": _oe.is_tesseract_available(),
                "external_ocr_configured": _ors_is_configured(),
                "cpu_simd": _sd.probe_cpu_simd(),
            },
        )

    @router.post("/api/ocr-langs/set-engine")
    async def ocr_langs_set_engine(request: Request):
        from ..core import ocr_engine as _oe
        body = await request.json()
        e = str(body.get("engine") or "").strip().lower()
        if e not in ("easyocr", "tesseract"):
            raise HTTPException(400, "engine must be 'easyocr' or 'tesseract'")
        ok = _oe.set_default_engine(e)
        return {"ok": ok, "engine": e}

    @router.post("/api/ocr-langs/set-quality")
    async def ocr_langs_set_quality(request: Request):
        """切換 OCR 預設 quality（fast / best）。對所有已下載兩變體的 lang
        自動 swap active variant。回 ok / 不行的 lang 清單。"""
        from ..core import tessdata_manager as _tm
        body = await request.json()
        q = str(body.get("quality") or "").strip().lower()
        if q not in ("fast", "best"):
            raise HTTPException(400, "quality must be 'fast' or 'best'")
        ok = _tm.set_default_quality(q)
        return {"ok": ok, "quality": q}

    @router.post("/api/ocr-langs/switch-active")
    async def ocr_langs_switch_active(request: Request):
        """單一 lang 切 active variant。"""
        from ..core import tessdata_manager as _tm
        body = await request.json()
        code = str(body.get("code") or "").strip()
        quality = str(body.get("quality") or "").strip().lower()
        if not _tm.is_valid_lang_code(code):
            raise HTTPException(400, "invalid lang code")
        if quality not in ("fast", "best"):
            raise HTTPException(400, "quality must be 'fast' or 'best'")
        result = _tm.switch_active_quality(code, quality)
        return {"ok": result.ok, "code": result.code, "error": result.error}

    @router.post("/ocr-langs/install")
    async def ocr_langs_install(request: Request):
        from ..core import tessdata_manager as _tm
        body = await request.json()
        code = str(body.get("code") or "").strip()
        if not _tm.is_valid_lang_code(code):
            raise HTTPException(400, "invalid lang code")
        result = _tm.install_lang(code)
        return {
            "ok": result.ok,
            "code": result.code,
            "size_mb": round(result.bytes_written / 1024 / 1024, 1) if result.bytes_written else 0,
            "error": result.error,
            "hint": result.hint,
        }

    @router.post("/ocr-langs/uninstall")
    async def ocr_langs_uninstall(request: Request):
        from ..core import tessdata_manager as _tm
        body = await request.json()
        code = str(body.get("code") or "").strip()
        if not _tm.is_valid_lang_code(code):
            raise HTTPException(400, "invalid lang code")
        result = _tm.uninstall_lang(code)
        return {"ok": result.ok, "code": result.code,
                "error": result.error, "hint": result.hint}

    # ─── 外部 GPU OCR Server 部署 (v1.11.22+) ────────────────────────
    @router.get("/ocr-langs/deploy/install.sh")
    async def ocr_langs_deploy_install_sh(request: Request):
        """Generate + serve install.sh (path B: admin downloads + SCPs to GPU host)."""
        from ..admin.ocr_remote_deploy import build_install_script
        from ..main import VERSION
        from fastapi.responses import PlainTextResponse
        script = build_install_script(jtdt_version=VERSION)
        return PlainTextResponse(
            script,
            headers={
                "Content-Disposition": 'attachment; filename="jt-ocr-server-install.sh"',
                "Content-Type": "text/x-sh; charset=utf-8",
            },
        )

    @router.get("/ocr-langs/deploy/uninstall.sh")
    async def ocr_langs_deploy_uninstall_sh(request: Request):
        from ..admin.ocr_remote_deploy import build_uninstall_script
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(
            build_uninstall_script(),
            headers={
                "Content-Disposition": 'attachment; filename="jt-ocr-server-uninstall.sh"',
                "Content-Type": "text/x-sh; charset=utf-8",
            },
        )

    @router.get("/api/ocr-langs/external/status")
    async def ocr_external_status(request: Request):
        """讀目前外部 OCR server 設定(token 已遮蔽)。"""
        from ..core import ocr_remote_settings as _ors
        d = _ors.get()
        return {
            "enabled": d.get("enabled", False),
            "url": d.get("url", ""),
            "token_set": bool(d.get("token")),
            "token_masked": _ors.masked_token(d.get("token", "")),
            "timeout_s": d.get("timeout_s", 30),
            "last_test_ok": d.get("last_test_ok", False),
            "last_test_at": d.get("last_test_at", ""),
            "last_test_info": d.get("last_test_info", {}),
        }

    @router.post("/api/ocr-langs/external/save")
    async def ocr_external_save(request: Request):
        """儲存 URL / Token / timeout / enabled。"""
        from ..core import ocr_remote_settings as _ors
        body = await request.json()
        url = body.get("url")
        token = body.get("token")
        enabled = body.get("enabled")
        timeout_s = body.get("timeout_s")
        # token=空字串 視為「保留現有 token」(避免 admin 重存表單把空 token 蓋掉)
        if isinstance(token, str) and not token.strip():
            token = None
        d = _ors.update(
            url=url if isinstance(url, str) else None,
            token=token if isinstance(token, str) else None,
            enabled=bool(enabled) if enabled is not None else None,
            timeout_s=int(timeout_s) if timeout_s is not None else None,
        )
        return {"ok": True, "enabled": d["enabled"], "url": d["url"]}

    @router.post("/api/ocr-langs/external/test")
    async def ocr_external_test(request: Request):
        """測試連接外部 OCR server — 呼叫 /healthz 取 GPU 資訊。
        允許 body 傳 url + token override(讓 admin 還沒 save 就能 test)。"""
        import httpx
        import logging as _lg
        from urllib.parse import urlparse
        from ..core import ocr_remote_settings as _ors
        _log = _lg.getLogger("app.admin.ocr_external_test")
        body = await request.json()
        d = _ors.get()
        url = (body.get("url") or d.get("url") or "").strip().rstrip("/")
        token = (body.get("token") or d.get("token") or "").strip()
        if not url:
            raise HTTPException(400, "URL 未填")
        # SSRF 防護: 走 url_safety.safe_remote_base_url（CodeQL request-forgery barrier）。
        # 只回乾淨 scheme://host[:port]，拋棄 user 的 path/query/credentials；
        # 擋 cloud metadata；路徑寫死 /healthz /version，完全切斷 taint flow。
        # 用「絕對 import」CodeQL API graph 才認得 barrier（見 jt-sanitizers.model.yml 註）。
        from app.core.url_safety import safe_remote_base_url
        try:
            clean_base = safe_remote_base_url(url)
        except ValueError as e:
            raise HTTPException(400, f"URL 不合法: {e}")
        healthz_url = clean_base + "/healthz"
        version_url = clean_base + "/version"
        try:
            with httpx.Client(timeout=10.0, follow_redirects=False) as cli:
                # /healthz 不需 auth
                r = cli.get(healthz_url)
                if r.status_code != 200:
                    return {"ok": False, "error": f"healthz HTTP {r.status_code}"}
                info = r.json()
                # 若 token 也填了,順便驗 /version(要 auth)
                if token:
                    rv = cli.get(version_url,
                                  headers={"Authorization": f"Bearer {token}"})
                    if rv.status_code == 401:
                        _ors.update_test_result(ok=False, info={"error": "token invalid"})
                        return {"ok": False, "error": "Token 無效(/version 401)", "healthz": info}
                    if rv.status_code != 200:
                        return {"ok": False, "error": f"/version HTTP {rv.status_code}", "healthz": info}
                _ors.update_test_result(ok=True, info=info)
                return {"ok": True, "healthz": info}
        except httpx.RequestError as e:
            # 完整錯誤只進 log,response 給 user 一律泛用訊息(避免 info leak)
            _log.warning("ocr_external_test request failed: %s", e)
            _ors.update_test_result(ok=False, info={"error": e.__class__.__name__})
            return {"ok": False, "error": "連線失敗(網路 / DNS / timeout)"}

    # ─── 統編資料庫管理 (M4) ─────────────────────────────────────────
    @router.get("/vat-db", response_class=HTMLResponse)
    async def vat_db_page(request: Request):
        from ..core import vat_db as _vatdb
        return templates.TemplateResponse(request, "vat_db.html", {
            "request": request,
            "meta": _vatdb.get_meta(),
            "sources": _vatdb.SOURCE_URLS,
            "categories": _vatdb.get_category_stats(),
        })

    @router.get("/vat-db/info")
    async def vat_db_info():
        from ..core import vat_db as _vatdb
        return {
            "meta": _vatdb.get_meta(),
            "sources": _vatdb.SOURCE_URLS,
            "categories": _vatdb.get_category_stats(),
        }

    @router.post("/vat-db/upload")
    async def vat_db_upload(file: UploadFile = File(...)):
        """手動上傳 CSV 或 ZIP — 1 GB 上限。**背景**解析 + 建索引（170 萬筆要數
        分鐘），立刻 return，前端透過 /admin/vat-db/progress 輪詢,網頁不卡住。"""
        from ..core import vat_db as _vatdb
        data = await file.read()
        if not data:
            raise HTTPException(400, "空檔案")
        if len(data) > 1024 * 1024 * 1024:  # 1 GB hard cap (政府資料就大)
            raise HTTPException(413, "檔案超過 1 GB 上限")
        status = _vatdb.trigger_ingest_async(
            data, source=f"manual:{file.filename or 'upload'}")
        return {
            "ok": True,
            "status": status,                 # 'started' or 'already_running'
            "started": status == "started",
            "message": ("已開始背景匯入，請看下方進度。"
                        if status == "started"
                        else "已有背景工作進行中，請稍候。"),
        }

    @router.post("/vat-db/auto-download")
    async def vat_db_auto_download():
        """啟動背景下載執行緒，立刻 return。下載 5-30 分鐘期間，前端透過
        `/vat-db/progress` polling 看進度。完成 / 失敗時 progress.stage
        會變 'done' / 'error'，前端就從那邊讀最終結果。
        絕不在 event loop 內跑同步下載 — 會卡死整站。"""
        from ..core import vat_db as _vatdb
        status = _vatdb.trigger_download_async()
        return {
            "ok": True,
            "status": status,             # 'started' or 'already_running'
            "started": status == "started",
        }

    @router.get("/vat-db/progress")
    async def vat_db_progress():
        """回目前下載 / 解析進度 — 前端 polling 用。
        stage 可能值：idle / starting / downloading_main / parsing_main /
        downloading_supplement / parsing_supplement / done / error"""
        from ..core import vat_db as _vatdb
        return _vatdb.read_progress()

    @router.get("/vat-db/schedule")
    async def vat_db_schedule_get():
        from ..core import vat_db as _vatdb
        return _vatdb.get_schedule()

    @router.post("/vat-db/schedule")
    async def vat_db_schedule_set(payload: dict):
        from ..core import vat_db as _vatdb
        # 自己驗、自己講 —— 直接把 int() 的 ValueError 往外送，等於把使用者
        # 輸入的字串原樣回吐（"invalid literal for int()..."），那不是我們
        # 自撰的驗證訊息。
        try:
            weekday = int(payload.get("weekday", 6))
            hour = int(payload.get("hour", 3))
        except (ValueError, TypeError):
            raise HTTPException(400, "參數錯誤：星期與時間必須是數字")
        if not (0 <= weekday <= 6):
            raise HTTPException(400, "參數錯誤：星期必須介於 0（週一）到 6（週日）")
        if not (0 <= hour <= 23):
            raise HTTPException(400, "參數錯誤：時間必須介於 0 到 23 時")
        try:
            _vatdb.set_schedule(
                enabled=bool(payload.get("enabled", False)),
                weekday=weekday, hour=hour,
            )
        except (ValueError, TypeError) as e:
            logger.warning("統編排程設定失敗：%s", e)
            raise HTTPException(400, "參數錯誤：排程設定無法套用")
        return {"ok": True, **_vatdb.get_schedule()}

    @router.post("/vat-db/clear")
    async def vat_db_clear():
        from ..core import vat_db as _vatdb
        _vatdb.clear_db()
        return {"ok": True}

    # 公文撰擬的資料來源（官方範本 / 機關地址簿）—— 端點在自己的檔案裡，
    # 併進來之後一樣吃這個 router 的管理員閘。
    from .official_doc_routes import build_router as _build_official_doc_router
    router.include_router(_build_official_doc_router(templates))

    return router
