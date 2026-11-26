"""AI 用印（v1.17.38）：給 AI 查的兩支（可以用的章、某一件的狀態），與本人確認的網頁。

* `GET /api/ai-stamp/seals?tool=pdf-stamp|pdf-seam-stamp`：這張 Token 的使用者經 AI 可以用的章（不回圖檔）
* `GET /api/ai-stamp/requests/{id}`：某一件的狀態；核准後帶工作區的檔案編號
* `GET /api/ai-stamp/presets`、`POST /api/ai-stamp/presets`（`{name, placements}` 新增或覆蓋）、
  `POST /api/ai-stamp/presets/delete`（`{name}`）：常用組合（v1.17.54；每個人自己的，只存位置設定、不存章圖）。
  網頁登入另有 `/ai-stamp/api/presets`（看）與 `/ai-stamp/api/presets/delete`（刪）。
* 本人確認：v1.17.56 起併進「用印申請」的「AI 用印」分頁（`/seal-request#ai`；舊網址 `/ai-stamp` 轉過去）。**核准與退回只能用網頁的登入**：
  帶 API Token 的請求一律 403 —— AI 不可以自己核准（設計第 5 節）。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from ..core import ai_stamp


def _err(e: ai_stamp.AiStampError) -> JSONResponse:
    return JSONResponse(status_code=e.status, content={"detail": f"{e.message} ({e.code})", "code": e.code})


def _web_user(request: Request) -> dict:
    """網頁登入的本人；帶 API Token 的（AI）一律不給。"""
    st = request.state
    if getattr(st, "api_token_label", None) is not None or \
            (request.headers.get("Authorization") or "").lower().startswith("bearer "):
        raise HTTPException(403, "AI 用印只能由本人在網頁上確認")
    from ..core import sessions
    user = getattr(st, "user", None)
    if not user or not user.get("user_id"):
        raise HTTPException(401, "請先登入")
    return {"id": int(user["user_id"]), "label": sessions.user_label(user) or str(user["user_id"])}


def _ip(request: Request) -> str:
    from ..core.client_ip import real_client_ip
    return real_client_ip(request)


def _audit_preset(request: Request, user: dict, action: str, name: str, count: int) -> None:
    from ..core import audit_db
    audit_db.log_event("ai_stamp_preset", username=user.get("label") or "", ip=_ip(request),
                       target=name[:40], details={"action": action, "placements": count})


def build_router(templates) -> APIRouter:
    router = APIRouter()

    # ---- 給 AI（API Token）----

    @router.get("/api/ai-stamp/seals")
    async def seals(request: Request, tool: str = "pdf-stamp"):
        if tool not in ai_stamp.TOOL_TYPES:
            raise HTTPException(400, "tool 只能是 pdf-stamp 或 pdf-seam-stamp")
        try:
            user = ai_stamp.require(request, tool)
        except ai_stamp.AiStampError as e:
            return _err(e)
        items = await asyncio.to_thread(ai_stamp.eligible, user["id"], tool)
        return {"tool": tool, "seals": items, "confirm_hours": ai_stamp.CONFIRM_HOURS}

    @router.get("/api/ai-stamp/requests/{request_id}")
    async def status(request: Request, request_id: str):
        user = getattr(request.state, "user", None)
        if not user or not user.get("user_id"):
            raise HTTPException(401, "請先登入或使用綁定使用者的 API Token")
        try:
            r = ai_stamp.get_for_user(request_id, int(user["user_id"]))
        except ai_stamp.AiStampError as e:
            return _err(e)
        return ai_stamp.public(r)

    # ---- 常用組合（給 AI；v1.17.54）----

    def _clean_preset(user: dict, body: dict) -> list:
        from ..tools.pdf_stamp import ai_layout
        try:
            items = ai_layout.validate(body.get("placements"))
        except ai_layout.LayoutError as e:
            raise HTTPException(400, e.message)
        # 存之前先確認章都能用（不能用的章存了也蓋不了，早點講）
        ai_stamp.allowed(user["id"], "pdf-stamp", [it["asset_id"] for it in items])
        return items

    @router.get("/api/ai-stamp/presets")
    async def presets(request: Request):
        try:
            user = ai_stamp.require(request, "pdf-stamp")
        except ai_stamp.AiStampError as e:
            return _err(e)
        return {"presets": await asyncio.to_thread(ai_stamp.list_presets, user["id"])}

    @router.post("/api/ai-stamp/presets")
    async def preset_save(request: Request):
        try:
            user = ai_stamp.require(request, "pdf-stamp")
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(400, "要送 JSON 物件 {name, placements}")
            items = await asyncio.to_thread(_clean_preset, user, body)
            res = await asyncio.to_thread(ai_stamp.save_preset, user["id"], body.get("name"), items)
        except ai_stamp.AiStampError as e:
            return _err(e)
        except ValueError:
            raise HTTPException(400, "不是合法的 JSON")
        _audit_preset(request, user, "save", res["name"], len(items))
        return res

    @router.post("/api/ai-stamp/presets/delete")
    async def preset_delete(request: Request):
        try:
            user = ai_stamp.require(request, "pdf-stamp")
            body = await request.json()
            ok = await asyncio.to_thread(ai_stamp.delete_preset, user["id"], (body or {}).get("name"))
        except ai_stamp.AiStampError as e:
            return _err(e)
        except (ValueError, AttributeError):
            raise HTTPException(400, "要送 JSON 物件 {name}")
        if not ok:
            return _err(ai_stamp.AiStampError("PRESET_NOT_FOUND", 404))
        _audit_preset(request, user, "delete", str(body.get("name")).strip(), 0)
        return {"ok": True}

    # ---- 本人確認（網頁）----

    @router.get("/ai-stamp/api/presets")
    async def web_presets(request: Request):
        me = _web_user(request)

        def work():
            from ..core import asset_access
            from ..core.asset_manager import asset_manager
            items = ai_stamp.list_presets(me["id"])
            names = {}
            for pr in items:
                for it in pr["placements"]:
                    aid = it.get("asset_id") if isinstance(it, dict) else None
                    if isinstance(aid, str) and aid not in names and ai_stamp.REQ_ID_RE.fullmatch(aid):
                        a = asset_manager.get(aid)
                        # 只給名稱（不給圖）；用不到的章（受管制、沒權限）不顯示名稱
                        names[aid] = a.name if a and asset_access.can_use_asset(request, a) else ""
            return {"presets": items, "names": names}
        return await asyncio.to_thread(work)

    @router.post("/ai-stamp/api/presets/delete")
    async def web_preset_delete(request: Request):
        me = _web_user(request)
        try:
            body = await request.json()
            ok = await asyncio.to_thread(ai_stamp.delete_preset, me["id"], (body or {}).get("name"))
        except ai_stamp.AiStampError as e:
            return _err(e)
        except (ValueError, AttributeError):
            raise HTTPException(400, "要送 JSON 物件 {name}")
        if not ok:
            return _err(ai_stamp.AiStampError("PRESET_NOT_FOUND", 404))
        _audit_preset(request, me, "delete", str(body.get("name")).strip(), 0)
        return {"ok": True}

    @router.get("/ai-stamp")
    async def page(request: Request):
        """舊網址（v1.17.38～54 的側欄與通知連結）：確認頁已併進「用印申請」的「AI 用印」分頁（v1.17.56）。
        網址後面的 `#req=…` 由瀏覽器自己帶過去（轉址不帶片段時瀏覽器保留原本的）。"""
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/seal-request", status_code=307)

    @router.get("/ai-stamp/api/pending")
    async def pending(request: Request):
        me = _web_user(request)
        return {"items": await asyncio.to_thread(ai_stamp.list_pending, me["id"])}

    @router.get("/ai-stamp/api/{request_id}/preview/{page}")
    async def preview(request: Request, request_id: str, page: int, size: str = "small"):
        me = _web_user(request)
        try:
            r = ai_stamp.get_for_user(request_id, me["id"])
        except ai_stamp.AiStampError as e:
            return _err(e)
        if r["status"] != "pending":
            raise HTTPException(410, "這件已經處理過，預覽已刪除")
        src = ai_stamp.out_path(r)
        if page < 0 or not src.exists():
            raise HTTPException(404, "找不到這一頁")
        large = size == "large"                                  # 放大檢視用（點預覽圖時才載）
        out = src.with_name(f"p{int(page)}{'_l' if large else ''}.png")
        if not out.exists():
            from ..core import pdf_preview
            try:
                await pdf_preview.render_page_png_async(src, out, int(page), dpi=150 if large else 80)
            except ValueError:
                raise HTTPException(404, "找不到這一頁")
        return FileResponse(str(out), media_type="image/png", headers={"Cache-Control": "no-store"})

    @router.post("/ai-stamp/api/{request_id}/approve")
    async def approve(request: Request, request_id: str):
        me = _web_user(request)
        from ..core import workspace as ws
        try:
            return await asyncio.to_thread(ai_stamp.approve, request_id, me["id"], me["label"], _ip(request))
        except ai_stamp.AiStampError as e:
            return _err(e)
        except ws.WorkspaceError:
            # 存不進工作區（容量滿、工作區沒開）：維持待確認，清出空間再按一次。
            # 回固定文字，例外內容只進伺服器日誌（不把內部訊息回給瀏覽器）
            logging.getLogger(__name__).warning("AI 用印核准時存不進工作區", exc_info=True)
            return JSONResponse(status_code=409, content={
                "detail": "存不進你的工作區（可能是容量已滿或工作區沒有開啟），這件維持待確認；清出空間後再按一次核准",
                "code": "WORKSPACE"})

    @router.post("/ai-stamp/api/{request_id}/reject")
    async def reject(request: Request, request_id: str):
        me = _web_user(request)
        try:
            return await asyncio.to_thread(ai_stamp.reject, request_id, me["id"], me["label"], _ip(request))
        except ai_stamp.AiStampError as e:
            return _err(e)

    return router
