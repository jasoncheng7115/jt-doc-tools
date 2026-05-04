from __future__ import annotations

import io
import json
import re
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from ..core.asset_manager import PositionPreset, asset_manager
from ..core.conv_settings import BUILTIN_PATHS, conv_settings
from ..core.profile_manager import profile_manager
from ..core.synonym_manager import synonym_manager
from ..core.template_manager import template_manager
from ..web.deps import require_admin

from fastapi import Depends


def build_router(templates) -> APIRouter:
    # Router-level dependency: every admin endpoint requires admin role when
    # auth is on (no-op when auth is off — require_admin returns synthetic).
    router = APIRouter(dependencies=[Depends(require_admin)])

    @router.get("/", response_class=HTMLResponse)
    async def admin_home(request: Request):
        return RedirectResponse("/admin/assets", status_code=302)

    @router.get("/assets", response_class=HTMLResponse)
    async def assets_page(request: Request, type: Optional[str] = None):
        items = asset_manager.list(type=type) if type else asset_manager.list()
        return templates.TemplateResponse(
            "asset_list.html",
            {"request": request, "assets": items, "type_filter": type},
        )

    @router.post("/assets/upload")
    async def assets_upload(
        name: str = Form(...),
        type: str = Form("stamp"),
        remove_bg: bool = Form(False),
        file: UploadFile = File(...),
    ):
        data = await file.read()
        if not data:
            raise HTTPException(400, "empty file")
        asset = asset_manager.create_from_bytes(
            name=name, type=type, png_bytes=data, remove_bg=remove_bg
        )
        return RedirectResponse(f"/admin/assets/{asset.id}/edit", status_code=303)

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
            "_exported_at": time.time(),
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
        except zipfile.BadZipFile:
            raise HTTPException(400, "不是合法的 ZIP 檔")
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
            new_meta["updated_at"] = time.time()
            existing["assets"].append(new_meta)
            existing_ids.add(new_id)
            added += 1

        _s.assets_meta_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
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
        return templates.TemplateResponse(
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
        return templates.TemplateResponse(
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
            "_exported_at": time.time(),
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
        return templates.TemplateResponse(
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
            "_exported_at": time.time(),
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
        return templates.TemplateResponse(
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
        return templates.TemplateResponse(
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

    # ---------- LLM 校驗附加功能設定 ----------
    # All endpoints fail-soft: never raise to break the admin page even if
    # the LLM backend is unreachable.

    @router.get("/llm-settings", response_class=HTMLResponse)
    async def llm_settings_page(request: Request):
        from ..core.llm_settings import llm_settings, DEFAULT_SETTINGS, LLMSettingsManager
        return templates.TemplateResponse(
            "llm_settings.html",
            {
                "request": request,
                "settings": llm_settings.get(),
                "defaults": DEFAULT_SETTINGS,
                "known_llm_tools": LLMSettingsManager.KNOWN_LLM_TOOLS,
            },
        )

    @router.get("/api/llm/settings")
    async def api_llm_settings_get():
        from ..core.llm_settings import llm_settings
        return llm_settings.get()

    @router.post("/api/llm/settings")
    async def api_llm_settings_save(request: Request):
        from ..core.llm_settings import llm_settings
        body = await request.json()
        return llm_settings.update(body or {})

    @router.post("/api/llm/test-connection")
    async def api_llm_test_connection(request: Request):
        """Test arbitrary settings (not yet saved). Used by the admin page's
        「測試連線」button."""
        from ..core.llm_client import LLMClient
        body = await request.json()
        base_url = (body.get("base_url") or "").strip()
        api_key = (body.get("api_key") or "").strip() or None
        timeout = float(body.get("timeout_seconds") or 10)
        if not base_url:
            return {"ok": False, "error": "Base URL 未填"}
        # Cap test timeout at 30s so admin page doesn't hang
        client = LLMClient(base_url=base_url, api_key=api_key, timeout=min(timeout, 30))
        result = client.test_connection()
        return {
            "ok": result.ok,
            "latency_ms": result.latency_ms,
            "error": result.error,
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

    @router.get("/api/llm/models")
    async def api_llm_models():
        """List models from the *currently saved* settings. Returns empty
        list if not enabled / connection fails."""
        from ..core.llm_settings import llm_settings
        client = llm_settings.make_client()
        if client is None:
            return {"ok": False, "error": "LLM 未啟用", "models": []}
        result = client.test_connection()
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
        return templates.TemplateResponse(
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
    @router.get("/api-tokens", response_class=HTMLResponse)
    async def api_tokens_page(request: Request):
        from ..core.api_tokens import api_tokens
        return templates.TemplateResponse(
            "api_tokens.html",
            {
                "request": request,
                "tokens": api_tokens.list_full(),
                "enforce": api_tokens.is_enforced(),
            },
        )

    @router.post("/api/tokens/create")
    async def api_tokens_create(request: Request):
        from ..core.api_tokens import api_tokens
        body = await request.json()
        label = str(body.get("label") or "").strip()
        t = api_tokens.create(label or "unnamed")
        return {"ok": True, "token": t.token, "label": t.label}

    @router.post("/api/tokens/revoke")
    async def api_tokens_revoke(request: Request):
        from ..core.api_tokens import api_tokens
        body = await request.json()
        tok = str(body.get("token") or "")
        ok = api_tokens.revoke(tok)
        return {"ok": ok}

    @router.post("/api/tokens/enforce")
    async def api_tokens_enforce(request: Request):
        from ..core.api_tokens import api_tokens
        body = await request.json()
        api_tokens.set_enforce(bool(body.get("enforce")))
        return {"ok": True, "enforce": api_tokens.is_enforced()}

    # ---- 系統相依套件檢查 -----------------------------------------------------
    @router.get("/sys-deps", response_class=HTMLResponse)
    async def sys_deps_page(request: Request):
        from ..core.sys_deps import collect_sys_deps
        deps = collect_sys_deps()
        return templates.TemplateResponse(
            "sys_deps.html",
            {"request": request, "deps": deps},
        )

    @router.get("/api/sys-deps")
    async def sys_deps_api():
        """JSON 版本，給外部監控 / API token 呼叫者用 (符合「所有功能須有 API」規範)。"""
        from ..core.sys_deps import collect_sys_deps
        return {"deps": collect_sys_deps()}

    # ---- 企業 logo / 識別 -----------------------------------------------------
    @router.get("/branding", response_class=HTMLResponse)
    async def branding_page(request: Request):
        from ..core import branding
        return templates.TemplateResponse(
            "admin_branding.html",
            {
                "request": request,
                "has_custom": branding.has_custom_logo(),
                "logo_url": branding.custom_logo_url(),
                "max_mb": branding.MAX_LOGO_BYTES // 1024 // 1024,
                "max_dim": branding.MAX_LOGO_DIMENSION,
            },
        )

    @router.post("/branding/upload")
    async def branding_upload(file: UploadFile = File(...)):
        from ..core import branding
        data = await file.read()
        try:
            branding.save_logo(data, file.filename or "")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "url": branding.custom_logo_url()}

    @router.post("/branding/reset")
    async def branding_reset():
        from ..core import branding
        removed = branding.reset_logo()
        return {"ok": True, "had_custom": removed}

    @router.get("/api/branding")
    async def branding_api():
        from ..core import branding
        return {
            "has_custom": branding.has_custom_logo(),
            "logo_url": branding.custom_logo_url(),
        }

    # ---- 全站設定匯出 / 匯入 -------------------------------------------------
    @router.get("/settings-export", response_class=HTMLResponse)
    async def settings_export_page(request: Request):
        from ..core import settings_export
        return templates.TemplateResponse(
            "admin_settings_export.html",
            {
                "request": request,
                "summary": settings_export.collect_summary(),
                "optional_dirs": settings_export._OPTIONAL_DIRS,
            },
        )

    @router.post("/settings-export/download")
    async def settings_export_download(request: Request):
        from ..core import settings_export
        from ..main import VERSION
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        include_optional = list(body.get("include_optional") or [])
        out_name = (
            f"jtdt-settings-{time.strftime('%Y%m%d-%H%M%S')}-v{VERSION}.zip"
        )
        out_path = settings.temp_dir / out_name
        result = settings_export.export_to_zip(out_path, include_optional, app_version=VERSION)
        return FileResponse(
            str(out_path), media_type="application/zip",
            filename=out_name, headers={"X-File-Count": str(result["file_count"])},
        )

    @router.post("/settings-export/import")
    async def settings_export_import(
        file: UploadFile = File(...),
        overwrite_optional: str = Form("0"),
    ):
        from ..core import settings_export
        from ..main import VERSION
        data = await file.read()
        if not data:
            raise HTTPException(400, "empty file")
        if len(data) > 200 * 1024 * 1024:
            raise HTTPException(400, "import file too large (>200 MB)")
        # Save to temp then import
        zip_path = settings.temp_dir / f"settings_import_{uuid.uuid4().hex}.zip"
        zip_path.write_bytes(data)
        try:
            result = settings_export.import_from_zip(
                zip_path,
                overwrite_optional=(overwrite_optional == "1"),
                app_version=VERSION,
            )
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e))
        finally:
            try: zip_path.unlink()
            except OSError: pass
        return result

    @router.get("/api/settings-export/summary")
    async def settings_export_summary_api():
        from ..core import settings_export
        return settings_export.collect_summary()

    return router
