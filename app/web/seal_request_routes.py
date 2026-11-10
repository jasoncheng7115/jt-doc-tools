"""用印申請的頁面與 API（`/seal-request`）。規則在 `app/core/seal_flow.py`。

* 功能沒啟用、或沒有啟用登入：頁面說明「尚未啟用」，API 一律 404。
* 每個操作都是 API（畫面只呼叫這些）；每次狀態變更都寫稽核。
* 文件、附件、預覽圖只給跟這件案件有關的人（申請者、審核者、保管人、稽核員）；
  非相關的人一律回「找不到」，不透露案件存不存在。管理員只看得到案件資料、可以改派。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ..core import seal_flow as F
from ..core import seal_store as S

_NO_STORE = {"Cache-Control": "no-store"}


def _err(e: F.FlowError) -> JSONResponse:
    return JSONResponse({"ok": False, "code": e.code, "message": F.message(e.code), "detail": e.detail},
                        status_code=e.status, headers=_NO_STORE)


def _ok(data: dict) -> JSONResponse:
    return JSONResponse({"ok": True, **data}, headers=_NO_STORE)


def _audit(request: Request, user: dict, event: str, case: dict, **details) -> None:
    from ..core import audit_db, client_ip
    audit_db.log_event(event, username=user["label"], ip=client_ip.real_client_ip(request),
                       target=case.get("number") or case.get("id"),
                       details={"case_id": case.get("id"), "version": case.get("version"), **details})


def _case_for(user: dict, case_id: str, *, documents: bool = False) -> dict:
    case = S.get_case(case_id)
    if case is None or not F.can_view(user, case):
        raise F.FlowError("NOT_FOUND", 404)
    if documents and not F.can_view_documents(user, case):
        raise F.FlowError("NOT_FOUND", 404)
    return case


def _public_case(user: dict, case: dict, *, full: bool = False) -> dict:
    out = {k: case[k] for k in ("id", "number", "applicant_label", "flow_id", "flow_name", "state", "stage_idx",
                                "version", "created_at", "updated_at", "submitted_at", "closed_at",
                                *S.CASE_FIELDS)}
    out["assets"] = case["assets"]
    out["placements"] = case["placements"]
    out["mine"] = int(case["applicant_id"]) == int(user["id"])
    st = F.stage_status(case)
    if st:
        out["stage"] = {k: st[k] for k in ("index", "name", "rule", "need", "approved", "stuck")}
        out["stage"]["remaining"] = len(st["remaining"])
    out["can_review"] = F.can_review(user["id"], case)
    out["can_stamp"] = case["state"] in ("awaiting_stamp", "stamp_failed") and F.is_custodian(user["id"], case)
    for k in ("stamp_error", "stamped_by_label", "done_at", "void_reason", "first_download_at"):
        out[k] = case.get(k)
    out["has_result"] = case["state"] in ("done", "void")
    if not full:
        return out
    roles = F.roles_on(user, case)
    out["roles"] = sorted(roles)
    out["documents"] = F.can_view_documents(user, case)
    if out["documents"]:
        docs = F.documents(case)
        out["docs"] = [{"id": d["id"], "name": d["name"], "pages": d["pages"], "page_sizes": d["page_sizes"]}
                       for d in docs]
        out["doc"] = out["docs"][0] if docs else None          # 舊欄位：第一份
        out["attachments"] = F.attachments(case)
    if st:
        out["stage"]["voters"] = st["voters"]
    vers = []
    for ver in range(1, int(case["version"]) + 1):
        v = S.get_version(case["id"], ver)
        if v:
            vers.append({"version": ver, "doc_name": v["doc_name"], "doc_sha256": v["doc_sha256"],
                         "doc_pages": v["doc_pages"], "documents": F.version_docs(v), "created_at": v["created_at"],
                         "stages": [{"name": s["name"], "rule": s["rule"], "n": s["n"]} for s in v["stages"]],
                         "assets": [{"asset_id": a["asset_id"], "name": a["name"], "type": a["type"]}
                                    for a in v["assets"]]})
    out["versions"] = vers
    out["events"] = [{k: e[k] for k in ("ts", "actor_label", "action", "version", "stage_idx", "comment")}
                     for e in S.events(case["id"])]
    from ..core.asset_manager import asset_manager
    out["asset_names"] = {aid: (asset_manager.get(aid).name if asset_manager.get(aid) else "") for aid in case["assets"]}
    out["can_edit"] = "applicant" in roles and case["state"] in F.EDITABLE
    out["final_placements"] = case.get("final_placements") or []
    if out["can_stamp"] or case["state"] == "stamping":
        # 保管人調整位置用：核准的章與預設尺寸（章圖另外走 /assets/{aid}/image，只給保管人）
        ver = S.get_version(case["id"], case["version"])
        out["approved_assets"] = []
        for a in (ver["assets"] if ver else []):
            obj = asset_manager.get(a["asset_id"])
            out["approved_assets"].append({"asset_id": a["asset_id"], "name": a["name"], "type": a["type"],
                                           "width_mm": obj.preset.width_mm if obj else 40.0,
                                           "height_mm": obj.preset.height_mm if obj else 40.0})
    out["workspace"] = False
    if "applicant" in roles and out["has_result"]:
        from ..core import workspace
        out["workspace"] = workspace.is_enabled()
    # 草稿用「刪除草稿」，不用撤回（撤回是給已經送出的）
    out["can_withdraw"] = "applicant" in roles and case["state"] in ("reviewing", "returned", "awaiting_stamp")
    return out


def review_queue(user: dict) -> list[dict]:
    return [c for c in S.list_cases("state='reviewing'", limit=500) if F.can_review(user["id"], c)]


def todo_count(user: dict) -> int:
    """側欄的件數：待我審核＋待我用印。"""
    return len(review_queue(user)) + len([c for c in F.stamp_queue(user) if c["state"] != "stamping"])


async def _save_upload(file: UploadFile, limit: int) -> Path:
    """上傳先落地到暫存檔（不整份讀進記憶體），超過上限就停。"""
    from ..config import settings
    tmp = Path(tempfile.mkstemp(prefix="sealreq_", dir=str(settings.temp_dir))[1])
    size = 0
    with open(tmp, "wb") as f:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                f.close()
                tmp.unlink(missing_ok=True)
                raise F.FlowError("DOC_TOO_LARGE")
            f.write(chunk)
    return tmp


def build_router(templates) -> APIRouter:
    router = APIRouter()

    def guard(request: Request) -> dict:
        F.require_ready()
        return F.me(request)

    @router.get("/seal-request", response_class=HTMLResponse)
    async def page(request: Request):
        from ..core import auth_settings, seal_policy
        ready = seal_policy.is_enabled() and auth_settings.is_enabled()
        return templates.TemplateResponse(request, "seal_request.html", {"request": request, "ready": ready})

    @router.get("/seal-request/api/home")
    async def home(request: Request):
        try:
            user = guard(request)
        except F.FlowError as e:
            return _err(e)
        flows = [{"id": f["id"], "name": f["name"], "description": f["description"],
                  "stages": [{"name": s["name"], "rule": s["rule"], "n": s["n"]} for s in f["stages"]]}
                 for f in F.flows_for(user["id"])]
        return _ok({"me": {"label": user["label"], "admin": user["admin"], "auditor": user["auditor"]},
                    "flows": flows, "review_count": len(review_queue(user)),
                    "stamp_count": len([c for c in F.stamp_queue(user) if c["state"] != "stamping"]),
                    "limits": {"doc_mb": F.MAX_DOC_BYTES // 1048576, "attach_mb": F.MAX_ATTACH_BYTES // 1048576,
                               "attachments": F.MAX_ATTACHMENTS}})

    @router.get("/seal-request/api/flows/{flow_id}/assets")
    async def flow_assets(request: Request, flow_id: int):
        try:
            user = guard(request)
            flow = next((f for f in F.flows_for(user["id"]) if f["id"] == flow_id), None)
            if flow is None:
                raise F.FlowError("FLOW_NOT_FOUND", 404)
        except F.FlowError as e:
            return _err(e)
        # 申請者看不到原始印章圖：只給名稱、種類、尺寸（畫示意框用）
        return _ok({"assets": [{"id": a.id, "name": a.name, "type": a.type, "width_mm": a.preset.width_mm,
                                "height_mm": a.preset.height_mm} for a in F.requestable_assets(user["id"], flow)]})

    @router.get("/seal-request/api/cases")
    async def list_cases(request: Request, box: str = "mine"):
        try:
            user = guard(request)
        except F.FlowError as e:
            return _err(e)
        if box == "mine":
            rows = S.list_cases("applicant_id=?", (user["id"],))
        elif box == "review":
            rows = review_queue(user)
        elif box == "stamp":
            rows = F.stamp_queue(user)
        elif box == "handled":
            ids = S.participant_case_ids(user["id"])
            rows = [c for c in S.list_cases("1=1", limit=1000) if c["id"] in ids]
        elif box == "all" and (user["admin"] or user["auditor"]):
            rows = S.list_cases("state<>'draft'", limit=1000)
        else:
            return _err(F.FlowError("NOT_FOUND", 404))
        return _ok({"cases": [_public_case(user, c) for c in rows]})

    @router.post("/seal-request/api/cases")
    async def create(request: Request):
        try:
            user = guard(request)
            body = await request.json()
            case = F.create_draft(user, (body or {}).get("flow_id") if isinstance(body, dict) else None)
        except F.FlowError as e:
            return _err(e)
        except ValueError:
            return _err(F.FlowError("FLOW_NOT_FOUND", 404))
        _audit(request, user, "seal_req_create", case, flow=case["flow_name"])
        return _ok({"case": _public_case(user, case, full=True)})

    @router.get("/seal-request/api/cases/{case_id}")
    async def detail(request: Request, case_id: str):
        try:
            user = guard(request)
            case = F.recover_stale(_case_for(user, case_id))
        except F.FlowError as e:
            return _err(e)
        return _ok({"case": _public_case(user, case, full=True)})

    @router.put("/seal-request/api/cases/{case_id}")
    async def update(request: Request, case_id: str):
        try:
            user = guard(request)
            case = _case_for(user, case_id)
            try:
                body = await request.json()
            except ValueError:
                body = None
            case = F.update_draft(user, case, body)
        except F.FlowError as e:
            return _err(e)
        return _ok({"case": _public_case(user, case, full=True)})

    @router.delete("/seal-request/api/cases/{case_id}")
    async def delete(request: Request, case_id: str):
        try:
            user = guard(request)
            case = _case_for(user, case_id)
            F.delete_draft(user, case)
        except F.FlowError as e:
            return _err(e)
        _audit(request, user, "seal_req_delete_draft", case)
        return _ok({})

    async def _upload_document(request: Request, case_id: str, file: UploadFile, fn):
        """收一份用印文件（PDF 或辦公文件先轉成 PDF），交給 `fn(user, case, pdf, name)`。"""
        from ..core import office_convert
        from ..config import settings
        tmp: Optional[Path] = None
        conv: Optional[Path] = None
        try:
            user = guard(request)
            case = _case_for(user, case_id)
            F._require_applicant(user, case)
            if case["state"] not in F.EDITABLE:
                raise F.FlowError("BAD_STATE", 409)
            name = file.filename or "document.pdf"
            tmp = await _save_upload(file, F.MAX_DOC_BYTES)
            with open(tmp, "rb") as fh:
                head = fh.read(5)
            if head == b"%PDF-":
                pdf = tmp
            elif office_convert.is_office_file(name):
                # 辦公文件先轉成 PDF：之後大家審的、最後蓋的，都是這一份 PDF
                raw = settings.temp_dir / f"sealreq_raw_{uuid.uuid4().hex}{Path(name).suffix.lower()}"
                shutil.move(str(tmp), raw)
                tmp = raw
                conv = settings.temp_dir / f"sealreq_conv_{uuid.uuid4().hex}.pdf"
                try:
                    await office_convert.convert_to_pdf_async(raw, conv)
                except Exception:
                    raise F.FlowError("NOT_PDF")
                pdf = conv
            else:
                raise F.FlowError("NOT_PDF")
            case = await asyncio.to_thread(fn, user, case, pdf, Path(name).stem + ".pdf")
        except F.FlowError as e:
            return _err(e)
        finally:
            for p in (tmp, conv):
                if p is not None:
                    Path(p).unlink(missing_ok=True)
        return _ok({"case": _public_case(user, case, full=True)})

    @router.post("/seal-request/api/cases/{case_id}/documents")
    async def add_document(request: Request, case_id: str, file: UploadFile = File(...)):
        """加一份用印文件（可以有多份，各自蓋、各自下載）。"""
        return await _upload_document(request, case_id, file, F.add_document)

    @router.post("/seal-request/api/cases/{case_id}/document")
    async def put_document(request: Request, case_id: str, file: UploadFile = File(...)):
        """舊介面：把用印文件整份換成這一份。"""
        return await _upload_document(request, case_id, file, F.put_document)

    @router.delete("/seal-request/api/cases/{case_id}/documents/{doc_id}")
    async def remove_document(request: Request, case_id: str, doc_id: str):
        try:
            user = guard(request)
            case = _case_for(user, case_id)
            if not F._DOC_ID.match(doc_id):
                raise F.FlowError("NOT_FOUND", 404)
            case = await asyncio.to_thread(F.remove_document, user, case, doc_id)
        except F.FlowError as e:
            return _err(e)
        return _ok({"case": _public_case(user, case, full=True)})

    def _version_arg(case: dict, version: Optional[int]) -> Optional[int]:
        if version is None:
            return None
        if not 1 <= int(version) <= int(case["version"]):
            raise F.FlowError("NOT_FOUND", 404)
        return int(version)

    def _pick_doc(case: dict, doc_id: Optional[str], version: Optional[int]) -> dict:
        if doc_id is not None and not F._DOC_ID.match(doc_id):
            raise F.FlowError("NOT_FOUND", 404)
        return F.document(case, doc_id, _version_arg(case, version))

    async def _send_document(request: Request, case_id: str, doc_id: Optional[str], version: Optional[int]):
        from ..core.http_utils import content_disposition
        try:
            user = guard(request)
            case = _case_for(user, case_id, documents=True)
            info = _pick_doc(case, doc_id, version)
        except F.FlowError as e:
            return _err(e)
        _audit(request, user, "seal_req_download", case, what="document", document=info["id"], of_version=version)
        return FileResponse(str(info["path"]), media_type="application/pdf",
                            headers={**_NO_STORE, "Content-Disposition": content_disposition(info["name"]),
                                     "X-Content-Type-Options": "nosniff"})

    @router.get("/seal-request/api/cases/{case_id}/document")
    async def get_document(request: Request, case_id: str, version: Optional[int] = None):
        """舊介面：第一份用印文件。"""
        return await _send_document(request, case_id, None, version)

    @router.get("/seal-request/api/cases/{case_id}/documents/{doc_id}")
    async def get_document_by_id(request: Request, case_id: str, doc_id: str, version: Optional[int] = None):
        return await _send_document(request, case_id, doc_id, version)

    async def _send_page(request: Request, case_id: str, doc_id: Optional[str], page: int, version: Optional[int]):
        try:
            user = guard(request)
            case = _case_for(user, case_id, documents=True)
            info = _pick_doc(case, doc_id, version)
            if not 1 <= page <= info["pages"]:
                raise F.FlowError("NOT_FOUND", 404)
        except F.FlowError as e:
            return _err(e)

        def render() -> bytes:
            cache = info["dir"] / "previews" / f"{info['id']}-p{page}.png"
            if cache.exists():
                return cache.read_bytes()
            import fitz
            with fitz.open(str(info["path"])) as doc:
                pg = doc[page - 1]
                zoom = 900 / max(pg.rect.width, 1)
                png = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).tobytes("png")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(png)
            return png

        return Response(await asyncio.to_thread(render), media_type="image/png", headers=_NO_STORE)

    @router.get("/seal-request/api/cases/{case_id}/pages/{page}")
    async def page_png(request: Request, case_id: str, page: int, version: Optional[int] = None):
        """舊介面：第一份用印文件的某一頁。"""
        return await _send_page(request, case_id, None, page, version)

    @router.get("/seal-request/api/cases/{case_id}/documents/{doc_id}/pages/{page}")
    async def doc_page_png(request: Request, case_id: str, doc_id: str, page: int, version: Optional[int] = None):
        return await _send_page(request, case_id, doc_id, page, version)

    @router.post("/seal-request/api/cases/{case_id}/attachments")
    async def add_attachment(request: Request, case_id: str, file: UploadFile = File(...)):
        tmp: Optional[Path] = None
        try:
            user = guard(request)
            case = _case_for(user, case_id)
            try:
                tmp = await _save_upload(file, F.MAX_ATTACH_BYTES)
            except F.FlowError:
                raise F.FlowError("ATTACHMENT_TOO_LARGE")
            items = await asyncio.to_thread(F.add_attachment, user, case, file.filename or "file", tmp)
        except F.FlowError as e:
            return _err(e)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        return _ok({"attachments": items})

    @router.delete("/seal-request/api/cases/{case_id}/attachments/{att_id}")
    async def remove_attachment(request: Request, case_id: str, att_id: str):
        try:
            user = guard(request)
            case = _case_for(user, case_id)
            items = F.remove_attachment(user, case, att_id)
        except F.FlowError as e:
            return _err(e)
        return _ok({"attachments": items})

    @router.get("/seal-request/api/cases/{case_id}/attachments/{att_id}")
    async def get_attachment(request: Request, case_id: str, att_id: str, version: Optional[int] = None):
        from ..core.http_utils import content_disposition
        try:
            user = guard(request)
            case = _case_for(user, case_id, documents=True)
            path, name = F.attachment_path(case, att_id, _version_arg(case, version))
        except F.FlowError as e:
            return _err(e)
        _audit(request, user, "seal_req_download", case, what="attachment", name=name)
        # 一律下載、不在瀏覽器裡開（附件可以是任何格式）
        return FileResponse(str(path), media_type="application/octet-stream",
                            headers={**_NO_STORE, "Content-Disposition": content_disposition(name),
                                     "X-Content-Type-Options": "nosniff"})

    async def _case_action(request: Request, case_id: str, fn, event: str, text_key: str = "comment"):
        try:
            user = guard(request)
            case = _case_for(user, case_id)
            try:
                body = await request.json()
            except ValueError:
                body = {}
            text = (body or {}).get(text_key, "") if isinstance(body, dict) else ""
            text = text if isinstance(text, str) else ""
            out = fn(user, case, text)
        except F.FlowError as e:
            return _err(e)
        after = out if isinstance(out, dict) else case
        _audit(request, user, event, after, stage=case.get("stage_idx"), state=after.get("state"))
        return _ok({"case": _public_case(user, S.get_case(case["id"]) or case, full=True)})

    @router.post("/seal-request/api/cases/{case_id}/submit")
    async def submit(request: Request, case_id: str):
        return await _case_action(request, case_id, lambda u, c, _t: F.submit(u, c), "seal_req_submit")

    @router.post("/seal-request/api/cases/{case_id}/approve")
    async def approve(request: Request, case_id: str):
        return await _case_action(request, case_id, F.approve, "seal_req_approve")

    @router.post("/seal-request/api/cases/{case_id}/return")
    async def return_case(request: Request, case_id: str):
        return await _case_action(request, case_id, F.return_case, "seal_req_return")

    @router.post("/seal-request/api/cases/{case_id}/reject")
    async def reject(request: Request, case_id: str):
        return await _case_action(request, case_id, F.reject, "seal_req_reject")

    @router.post("/seal-request/api/cases/{case_id}/withdraw")
    async def withdraw(request: Request, case_id: str):
        return await _case_action(request, case_id, F.withdraw, "seal_req_withdraw")

    @router.post("/seal-request/api/cases/{case_id}/comment")
    async def comment(request: Request, case_id: str):
        return await _case_action(request, case_id, lambda u, c, t: F.add_comment(u, c, t), "seal_req_comment", "text")

    # ------------------------------------------------------------------ 用印（第 3 版）

    @router.get("/seal-request/api/cases/{case_id}/assets/{asset_id}/image")
    async def seal_image(request: Request, case_id: str, asset_id: str):
        """核准的章圖：只給這件案件的保管人、只在待用印 / 用印中 / 用印失敗時。"""
        from ..core.asset_manager import asset_manager
        try:
            user = guard(request)
            case = _case_for(user, case_id)
            ver = S.get_version(case["id"], case["version"]) if case["version"] else None
            if (case["state"] not in ("awaiting_stamp", "stamping", "stamp_failed") or ver is None
                    or not F.is_custodian(user["id"], case)
                    or asset_id not in {a["asset_id"] for a in ver["assets"]}):
                raise F.FlowError("NOT_FOUND", 404)
            a = asset_manager.get(asset_id)
            if a is None or not asset_manager.file_path(a).exists():
                raise F.FlowError("NOT_FOUND", 404)
        except F.FlowError as e:
            return _err(e)
        return FileResponse(str(asset_manager.file_path(a)), media_type="image/png", headers=_NO_STORE)

    @router.post("/seal-request/api/cases/{case_id}/stamp")
    async def stamp(request: Request, case_id: str):
        try:
            user = guard(request)
            case = _case_for(user, case_id)
            try:
                body = await request.json()
            except ValueError:
                body = None
            body = body if isinstance(body, dict) else {}
            case, started = F.start_stamp(user, case, body.get("placements"), body.get("exec_id"))
        except F.FlowError as e:
            return _err(e)
        if started:
            _audit(request, user, "seal_req_stamp_start", case, placements=len(case["final_placements"]))
            case = await asyncio.to_thread(F.run_stamp, case["id"])
            _audit(request, user, "seal_req_stamp_done" if case["state"] == "done" else "seal_req_stamp_fail",
                   case, sha256=case.get("stamped_sha256"), error=case.get("stamp_error") or None)
        return _ok({"case": _public_case(user, S.get_case(case["id"]) or case, full=True)})

    @router.post("/seal-request/api/cases/{case_id}/custodian-return")
    async def custodian_return(request: Request, case_id: str):
        return await _case_action(request, case_id, F.custodian_return, "seal_req_custodian_return")

    @router.get("/seal-request/api/cases/{case_id}/result")
    async def result(request: Request, case_id: str, doc: Optional[str] = None):
        """用印後的 PDF。`doc` 指定哪一份；沒指定時只有一份就給 PDF、多份就打包成 ZIP。"""
        import tempfile
        import zipfile
        from starlette.background import BackgroundTask
        from ..core.http_utils import content_disposition
        try:
            user = guard(request)
            case = _case_for(user, case_id, documents=True)
            if doc is not None and not F._DOC_ID.match(doc):
                raise F.FlowError("NOT_FOUND", 404)
            files = F.result_files(user, case)
            if doc is not None:
                files = [f for f in files if f["id"] == doc]
                if not files:
                    raise F.FlowError("NOT_FOUND", 404)
        except F.FlowError as e:
            return _err(e)
        F.note_download(user, case)
        _audit(request, user, "seal_req_download", case, what="result",
               documents=[f["id"] for f in files])
        if len(files) == 1:
            return FileResponse(str(files[0]["path"]), media_type="application/pdf",
                                headers={**_NO_STORE, "Content-Disposition": content_disposition(files[0]["name"]),
                                         "X-Content-Type-Options": "nosniff"})

        def build() -> str:
            fd, tmp = tempfile.mkstemp(prefix="sealreq_zip_", suffix=".zip")
            os.close(fd)
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for f in files:
                    z.write(f["path"], f["name"])
            return tmp

        tmp = await asyncio.to_thread(build)
        return FileResponse(tmp, media_type="application/zip",
                            headers={**_NO_STORE, "Content-Disposition": content_disposition(f"{case['number']}_用印.zip"),
                                     "X-Content-Type-Options": "nosniff"},
                            background=BackgroundTask(lambda: Path(tmp).unlink(missing_ok=True)))

    @router.post("/seal-request/api/cases/{case_id}/result/workspace")
    async def result_to_workspace(request: Request, case_id: str):
        """申請者把用印後的 PDF（每一份）存到自己的工作區。"""
        from ..core import workspace
        try:
            user = guard(request)
            case = _case_for(user, case_id, documents=True)
            F._require_applicant(user, case)
            if not workspace.is_enabled():
                raise F.FlowError("WORKSPACE_OFF", 409)
            ids = []
            for f in F.result_files(user, case):
                try:
                    meta = await asyncio.to_thread(workspace.save_file, request, f["path"], f["name"], "seal-request")
                except workspace.WorkspaceError:
                    # 工作區滿了之類：案件本身不受影響，照樣可以從案件頁下載。細節只進日誌，不回給畫面
                    import logging
                    logging.getLogger(__name__).warning("seal-request: save result to workspace failed", exc_info=True)
                    raise F.FlowError("WORKSPACE_FAILED", 409)
                ids.append(meta.get("file_id") if isinstance(meta, dict) else None)
        except F.FlowError as e:
            return _err(e)
        F.note_download(user, case)
        _audit(request, user, "seal_req_download", case, what="workspace", count=len(ids))
        return _ok({"file_id": ids[0] if ids else None, "file_ids": ids})

    return router
