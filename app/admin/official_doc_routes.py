"""管理區「公文撰擬設定」—— 官方公文範本與機關地址簿的來源。

邏輯全在 `app/core/official_doc_sources.py`；這裡只做 HTTP 那一層：

* 所有端點都是管理員專用（自己掛 `require_admin`，併進 `/admin` 的 router 時
  也會繼承那邊的 router 層級閘）。
* **會動到檔案或要解析的一律丟出事件迴圈**：上傳要解 88 份 odt、地址簿第一次
  查詢要讀 5 MB 的 JSON —— 留在事件迴圈上會讓全站在那段時間不回應。
* 下載是背景執行緒（`start_download` 立刻回來），畫面輪詢 `/status`。
* 設定的增刪改、下載、上傳都寫稽核紀錄。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ..core import official_doc_sources as ods
from ..logging_setup import get_logger
from ..web.deps import require_admin

logger = get_logger(__name__)


def _audit(request: Request, action: str, sid: str = "", **extra) -> None:
    from ..core import audit_db, client_ip as _cip
    user = getattr(request.state, "user", None)
    details = {"action": action}
    if sid:
        details["source_id"] = sid
    details.update({k: v for k, v in extra.items() if v is not None})
    try:
        audit_db.log_event("settings_change",
                           username=(user or {}).get("username", ""),
                           ip=_cip.real_client_ip(request),
                           target="official_doc_sources", details=details)
    except Exception:       # 稽核寫不進去不可以讓操作本身失敗
        logger.exception("official doc sources audit failed")


def _sid_or_404(sid: str) -> str:
    if not ods.valid_source_id(sid):
        raise HTTPException(404, ods.MESSAGES["not_found"])
    return sid


async def _json_body(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "資料格式不正確")
    return body


def _http_error(e: ods.SourceError) -> HTTPException:
    if isinstance(e, ods.SourceNotFound):
        return HTTPException(404, str(e))
    if isinstance(e, ods.SourceBusy):
        return HTTPException(409, str(e))
    return HTTPException(400, str(e))


def build_router(templates) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_admin)])

    @router.get("/official-doc", response_class=HTMLResponse)
    async def official_doc_page(request: Request):
        st = await asyncio.to_thread(ods.status)
        return templates.TemplateResponse(request, "admin_official_doc.html", {
            "request": request,
            "initial": st,
            "kinds": ods.kind_options(),
            "default_license": ods.LICENSE_OGDL_V1,
        })

    @router.get("/official-doc/status")
    async def official_doc_status():
        return await asyncio.to_thread(ods.status)

    @router.post("/official-doc/sources")
    async def official_doc_add(request: Request):
        body = await _json_body(request)
        try:
            rec = await asyncio.to_thread(ods.add_source, body)
        except ods.SourceError as e:
            raise _http_error(e)
        _audit(request, "add", rec["id"], kind=rec["kind"], url=rec["url"])
        return {"ok": True, "source": rec}

    @router.post("/official-doc/sources/{sid}")
    async def official_doc_update(sid: str, request: Request):
        _sid_or_404(sid)
        body = await _json_body(request)
        try:
            rec = await asyncio.to_thread(ods.update_source, sid, body)
        except ods.SourceError as e:
            raise _http_error(e)
        _audit(request, "update", sid,
               fields=sorted(k for k in body if k in (
                   "name", "url", "dataset_url", "publisher", "license", "enabled")),
               url=rec.get("url"), enabled=rec.get("enabled"))
        return {"ok": True, "source": rec}

    @router.post("/official-doc/sources/{sid}/delete")
    async def official_doc_delete(sid: str, request: Request):
        _sid_or_404(sid)
        try:
            await asyncio.to_thread(ods.delete_source, sid)
        except ods.SourceError as e:
            raise _http_error(e)
        _audit(request, "delete", sid)
        return {"ok": True}

    @router.post("/official-doc/restore-defaults")
    async def official_doc_restore(request: Request):
        await asyncio.to_thread(ods.restore_defaults)
        _audit(request, "restore_defaults")
        return {"ok": True}

    @router.post("/official-doc/sources/{sid}/download")
    async def official_doc_download(sid: str, request: Request):
        _sid_or_404(sid)
        try:
            await asyncio.to_thread(ods.start_download, sid)
        except ods.SourceError as e:
            raise _http_error(e)
        src = ods.get_source(sid) or {}
        _audit(request, "download", sid, url=src.get("url"))
        return {"ok": True, "started": True}

    @router.post("/official-doc/sources/{sid}/upload")
    async def official_doc_upload(sid: str, request: Request,
                                  file: UploadFile = File(...)):
        _sid_or_404(sid)
        src = ods.get_source(sid)
        if src is None:
            raise HTTPException(404, ods.MESSAGES["not_found"])
        limit = ods.MAX_BYTES[src["kind"]]
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(
                413, f"檔案太大（上限 {limit // (1024 * 1024)} MB）")
        try:
            res = await asyncio.to_thread(ods.install_upload, sid, data,
                                          file.filename or "")
        except ods.SourceError as e:
            _audit(request, "upload_rejected", sid)
            raise _http_error(e)
        _audit(request, "upload", sid, count=res.get("count"),
               sha256=res.get("sha256"))
        return res

    @router.get("/official-doc/sources/{sid}/templates")
    async def official_doc_templates(sid: str):
        _sid_or_404(sid)
        try:
            return await asyncio.to_thread(ods.templates_of, sid)
        except ods.SourceError as e:
            raise _http_error(e)

    @router.get("/official-doc/search-orgs")
    async def official_doc_search_orgs(q: str = "", limit: int = 20):
        # 第一次查詢要讀整份地址簿（約 5 MB）——丟出事件迴圈。
        # `total` 是符合的總筆數：只列前 20 筆時，畫面要講出還有幾筆沒列（不然看起來像查不到）。
        res = await asyncio.to_thread(ods.search_orgs_page, q[:200], limit)
        return {"results": res["results"], "total": res["total"], "max": ods.ORG_SEARCH_MAX}

    return router
