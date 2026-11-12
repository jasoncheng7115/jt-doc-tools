"""AI 用印（v1.17.38）：給 AI 查的兩支（可以用的章、某一件的狀態），與本人確認的網頁。

* `GET /api/ai-stamp/seals?tool=pdf-stamp|pdf-seam-stamp`：這張 Token 的使用者經 AI 可以用的章（不回圖檔）
* `GET /api/ai-stamp/requests/{id}`：某一件的狀態；核准後帶工作區的檔案編號
* `/ai-stamp`：本人確認頁（待確認清單、預覽、核准、退回）。**核准與退回只能用網頁的登入**：
  帶 API Token 的請求一律 403 —— AI 不可以自己核准（設計第 5 節）。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

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

    # ---- 本人確認（網頁）----

    @router.get("/ai-stamp", response_class=HTMLResponse)
    async def page(request: Request):
        return templates.TemplateResponse(request, "ai_stamp.html", {"request": request,
                                                                     "confirm_hours": ai_stamp.CONFIRM_HOURS})

    @router.get("/ai-stamp/api/pending")
    async def pending(request: Request):
        me = _web_user(request)
        return {"items": await asyncio.to_thread(ai_stamp.list_pending, me["id"])}

    @router.get("/ai-stamp/api/{request_id}/preview/{page}")
    async def preview(request: Request, request_id: str, page: int):
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
        out = src.with_name(f"p{int(page)}.png")
        if not out.exists():
            from ..core import pdf_preview
            try:
                await pdf_preview.render_page_png_async(src, out, int(page), dpi=80)
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
        except ws.WorkspaceError as e:
            # 存不進工作區（容量滿、工作區沒開）：維持待確認，清出空間再按一次
            from ..core.log_safe import safe_user_error
            return JSONResponse(status_code=409, content={
                "detail": "存不進你的工作區，這件維持待確認：" + safe_user_error(e, "工作區無法存檔"),
                "code": "WORKSPACE"})

    @router.post("/ai-stamp/api/{request_id}/reject")
    async def reject(request: Request, request_id: str):
        me = _web_user(request)
        try:
            return await asyncio.to_thread(ai_stamp.reject, request_id, me["id"], me["label"], _ip(request))
        except ai_stamp.AiStampError as e:
            return _err(e)

    return router
