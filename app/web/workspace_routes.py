"""User-facing workspace routes: the 「我的工作區」 page + save/list/serve/
delete/rename endpoints. Mounted from main.py.

All routes 404 when the admin has disabled the feature, so a disabled
workspace is completely invisible (the UI also hides its buttons via the
`workspace_enabled` Jinja global).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..core import workspace as ws
from ..core.http_utils import content_disposition
from ..core.safe_paths import is_uuid_hex

router = APIRouter()


def _require_enabled() -> None:
    if not ws.is_enabled():
        raise HTTPException(404, "工作區功能未啟用")


def _user_id_of(request: Request):
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        return None
    v = user.get("user_id") if isinstance(user, dict) else getattr(user, "user_id", None)
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


def _err_to_http(e: ws.WorkspaceError) -> HTTPException:
    if isinstance(e, ws.WorkspaceDisabled):
        return HTTPException(404, str(e))
    if isinstance(e, ws.QuotaExceeded):
        return HTTPException(413, str(e))
    if isinstance(e, ws.UnsupportedType):
        return HTTPException(400, str(e))
    if isinstance(e, ws.NotFound):
        return HTTPException(404, str(e))
    return HTTPException(400, str(e))


def build_router(templates) -> APIRouter:

    @router.get("/workspace", response_class=HTMLResponse)
    async def workspace_page(request: Request):
        _require_enabled()
        return templates.TemplateResponse(
            "my_workspace.html", {"request": request})

    @router.get("/workspace/api/list")
    async def workspace_list(request: Request, accept: Optional[str] = None):
        """List the current user's files. `accept` filters by extension list
        (e.g. 'pdf' or 'pdf,png') for the upload picker."""
        _require_enabled()
        try:
            files = ws.list_files(request)
            use = ws.usage(request)
        except ws.WorkspaceError as e:
            raise _err_to_http(e)
        if accept:
            wanted = {("." + a.strip().lstrip(".")).lower()
                      for a in accept.split(",") if a.strip()}
            files = [f for f in files if f.get("ext", "").lower() in wanted]
        # Resolve source tool id → 中文工具名稱 for display.
        name_map = {n.get("id"): n.get("name")
                    for n in (templates.env.globals.get("nav_tools") or [])}
        for f in files:
            st = f.get("source_tool") or ""
            f["source_tool_name"] = name_map.get(st, st)
        return {
            "files": files,
            "usage": use,
            "retention_hours": int(ws.get_settings().get("retention_hours", -1)),
        }

    @router.get("/workspace/api/count")
    async def workspace_count(request: Request):
        """Lightweight file count for the sidebar badge."""
        if not ws.is_enabled():
            return {"count": 0}
        try:
            return {"count": ws.count_files(request)}
        except ws.WorkspaceError:
            return {"count": 0}

    @router.post("/workspace/save")
    async def workspace_save(
        request: Request,
        job_id: Optional[str] = Form(None),
        file: Optional[UploadFile] = File(None),
        name: Optional[str] = Form(None),
        source_tool: Optional[str] = Form(None),
    ):
        """Save a tool's output into the user's workspace.

        Two modes:
        - job_id: server-side copy the finished job's result (no re-upload).
        - file:   the browser POSTs the produced PDF/PNG bytes directly.
        """
        _require_enabled()
        data: bytes
        disp_name = name or ""
        if job_id:
            if not is_uuid_hex(job_id):
                raise HTTPException(400, "invalid job_id")
            from ..core.job_manager import job_manager
            job = job_manager.get(job_id)
            if not job or not job.result_path or not job.result_path.exists():
                raise HTTPException(404, "找不到工作結果（可能已過期）")
            # ACL: a job tagged with an owner can only be saved by that owner
            # (admin override). Prevents a leaked job_id from letting another
            # user copy someone else's result into their own workspace.
            if job.owner_id is not None:
                from ..core import permissions as _perm
                cur = _user_id_of(request)
                if cur != job.owner_id and not (cur is not None and _perm.effective_tools(cur) == "ALL"):
                    raise HTTPException(403, "無權存取此工作結果")
            data = job.result_path.read_bytes()
            if not disp_name:
                disp_name = job.result_filename or job.result_path.name
            if not source_tool:
                source_tool = job.tool_id
        elif file is not None:
            data = await file.read()
            if not disp_name:
                disp_name = file.filename or ""
        else:
            raise HTTPException(400, "需要 job_id 或 file")
        try:
            meta = ws.save_bytes(request, data, disp_name, source_tool or "")
        except ws.WorkspaceError as e:
            raise _err_to_http(e)
        # Non-blocking duplicate hint: another file with the same display name
        # already exists (we still keep this new copy).
        dup = any(f["name"] == meta["name"] and f["file_id"] != meta["file_id"]
                  for f in ws.list_files(request))
        return {"ok": True, "file": meta, "duplicate": dup}

    @router.get("/workspace/file/{file_id}")
    async def workspace_file(request: Request, file_id: str, dl: int = 0):
        _require_enabled()
        try:
            fp, meta = ws.get_file(request, file_id)
        except ws.WorkspaceError as e:
            raise _err_to_http(e)
        disposition = "attachment" if dl else "inline"
        return FileResponse(
            str(fp), media_type=meta.get("mime", "application/octet-stream"),
            headers={"Content-Disposition":
                     content_disposition(meta.get("name", fp.name), disposition)})

    @router.get("/workspace/thumb/{file_id}")
    async def workspace_thumb(request: Request, file_id: str):
        _require_enabled()
        try:
            fp, mime = ws.get_thumbnail(request, file_id)
        except ws.WorkspaceError as e:
            raise _err_to_http(e)
        return FileResponse(str(fp), media_type=mime)

    @router.post("/workspace/delete")
    async def workspace_delete(request: Request, file_id: str = Form(...)):
        _require_enabled()
        try:
            ws.delete_file(request, file_id)
        except ws.WorkspaceError as e:
            raise _err_to_http(e)
        return {"ok": True}

    @router.post("/workspace/rename")
    async def workspace_rename(request: Request, file_id: str = Form(...),
                               name: str = Form(...)):
        _require_enabled()
        if not (name or "").strip():
            raise HTTPException(400, "名稱不可空白")
        try:
            meta = ws.rename_file(request, file_id, name)
        except ws.WorkspaceError as e:
            raise _err_to_http(e)
        return {"ok": True, "file": meta}

    return router
