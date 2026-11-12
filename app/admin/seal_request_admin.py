"""設定 → 用印申請：功能開關與每顆章的管制（第 1 版）。

掛在管理員的 router 底下（整個 router 已經要求管理員）。規則見 `app/core/seal_policy.py`。
每個動作都有 API（畫面只是呼叫這些 API），每次變更都記稽核。
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..core import seal_policy
from ..core.asset_manager import asset_manager


def _err(e: seal_policy.PolicyError, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "code": e.code, "message": seal_policy.message(e.code),
                         "detail": e.detail}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _audit(request: Request, event: str, target: str, details: dict) -> None:
    from ..core import audit_db, client_ip, sessions
    audit_db.log_event(event, username=sessions.user_label(getattr(request.state, "user", None)) or "",
                       ip=client_ip.real_client_ip(request), target=target, details=details)


def _subject_names() -> tuple[dict, dict]:
    from ..core import group_manager, user_manager
    return ({str(u["id"]): u for u in user_manager.list_users()},
            {str(g["id"]): g for g in group_manager.list_groups()})


def _describe(subjects: list[dict], users: dict, groups: dict) -> list[dict]:
    out = []
    for s in subjects:
        d = dict(s)
        if s["type"] == "user":
            u = users.get(s["key"])
            d["name"] = f"{u.get('username')}@{u.get('source') or 'local'}" if u else ""
            d["exists"] = u is not None
        elif s["type"] == "group":
            g = groups.get(s["key"])
            d["name"] = (g or {}).get("name") or ""
            d["exists"] = g is not None
        else:
            d["name"] = s["key"]
            d["exists"] = True
        out.append(d)
    return out


def _state() -> dict:
    from ..core import auth_settings
    users, groups = _subject_names()
    assets = []
    for t in seal_policy.CONTROLLABLE_TYPES:
        for a in asset_manager.list(type=t):
            p = seal_policy.get_policy(a.id)
            assets.append({"id": a.id, "name": a.name, "type": a.type, "mode": p["mode"],
                           "requesters": _describe(p["requesters"], users, groups),
                           "custodians": _describe(p["custodians"], users, groups),
                           "ai": {**p["ai"], "subjects": _describe(p["ai"]["subjects"], users, groups)},
                           "thumb_url": f"/admin/assets/{a.id}/thumb"})
    return {"ok": True, "enabled": seal_policy.is_enabled(), "readable": seal_policy.readable(),
            "auth_enabled": auth_settings.is_enabled(), "assets": assets,
            # 流程可選的浮水印圖（資產庫裡種類是浮水印的）
            "watermarks": [{"id": a.id, "name": a.name, "thumb_url": f"/admin/assets/{a.id}/thumb"}
                           for a in asset_manager.list(type="watermark")],
            "retention_days": seal_policy.retention_days()}


def register(router, templates) -> None:
    register_flows(router)
    register_void(router)

    @router.get("/seal-request", response_class=HTMLResponse)
    async def seal_request_page(request: Request):
        return templates.TemplateResponse(request, "admin_seal_request.html", {"request": request})

    @router.get("/seal-request/api/state")
    async def seal_request_state():
        return JSONResponse(_state(), headers={"Cache-Control": "no-store"})

    @router.get("/seal-request/api/subjects")
    async def seal_request_subjects():
        users, groups = _subject_names()
        return JSONResponse({
            "ok": True,
            "users": [{"key": k, "username": u.get("username"), "display_name": u.get("display_name"),
                       "source": u.get("source")} for k, u in users.items()],
            "groups": [{"key": k, "name": g.get("name"), "source": g.get("source"),
                        "members": int(g.get("member_count") or 0)} for k, g in groups.items()],
        }, headers={"Cache-Control": "no-store"})

    @router.put("/seal-request/api/enabled")
    async def seal_request_set_enabled(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = None
        on = body.get("enabled") if isinstance(body, dict) else None
        try:
            seal_policy.set_enabled(on)
        except seal_policy.PolicyError as e:
            return _err(e, 409 if e.code in ("STILL_CONTROLLED", "AUTH_REQUIRED") else 400)
        _audit(request, "seal_request_toggle", "seal-request", {"enabled": on})
        return JSONResponse(_state(), headers={"Cache-Control": "no-store"})

    @router.put("/seal-request/api/retention")
    async def seal_request_set_retention(request: Request):
        """結束的案件保留幾天（0 ＝ 不自動刪除）。依公司規定設定。"""
        try:
            body = await request.json()
        except Exception:
            body = None
        days = body.get("days") if isinstance(body, dict) else None
        before = seal_policy.retention_days()
        try:
            seal_policy.set_retention_days(days)
        except seal_policy.PolicyError as e:
            return _err(e, 409 if e.code == "NOT_ENABLED" else 400)
        _audit(request, "seal_request_retention", "seal-request", {"days": [before, days]})
        return JSONResponse(_state(), headers={"Cache-Control": "no-store"})

    @router.put("/seal-request/api/assets/{asset_id}")
    async def seal_request_set_asset(request: Request, asset_id: str):
        a = asset_manager.get(asset_id)
        if a is None:
            return _err(seal_policy.PolicyError("ASSET_NOT_FOUND"), 404)
        try:
            body = await request.json()
        except Exception:
            body = None
        before = seal_policy.get_policy(a.id)
        try:
            after = seal_policy.set_policy(a.id, a.type, body if isinstance(body, dict) else None)
        except seal_policy.PolicyError as e:
            return _err(e, 409 if e.code in ("NOT_ENABLED", "AI_ON_CONTROLLED") else 400)
        _audit(request, "seal_asset_policy_change", a.name, {
            "asset_id": a.id, "type": a.type,
            "mode": [before["mode"], after["mode"]],
            "requesters": len(after["requesters"]), "custodians": len(after["custodians"]),
            "ai": {"enabled": [before["ai"]["enabled"], after["ai"]["enabled"]],
                   "subjects": len(after["ai"]["subjects"]), "confirm": after["ai"]["confirm"],
                   "daily_limit": after["ai"]["daily_limit"]}})
        return JSONResponse(_state(), headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- 流程範本與改派（第 2 版）

def _flow_err(e) -> JSONResponse:
    from ..core import seal_flow as F
    return JSONResponse({"ok": False, "code": e.code, "message": F.message(e.code), "detail": e.detail},
                        status_code=e.status, headers={"Cache-Control": "no-store"})


def _flows_payload() -> dict:
    from ..core import seal_store as S
    users, groups = _subject_names()
    flows = []
    for f in S.list_flows():
        d = dict(f)
        d["applicants"] = _describe(f["applicants"], users, groups)
        d["stages"] = [{**st, "candidates": _describe(st["candidates"], users, groups)} for st in f["stages"]]
        flows.append(d)
    return {"ok": True, "flows": flows}


def register_flows(router) -> None:
    from ..core import seal_flow as F
    from ..core import seal_store as S


    @router.get("/seal-request/api/flows")
    async def flows_list():
        return JSONResponse(_flows_payload(), headers={"Cache-Control": "no-store"})

    async def _body(request: Request):
        try:
            return await request.json()
        except Exception:
            return None

    @router.post("/seal-request/api/simulate")
    async def flows_simulate(request: Request):
        """模擬一件申請會怎麼走（只讀：不建案件、不發通知）。流程可以是還沒儲存的那份。"""
        body = await _body(request)
        body = body if isinstance(body, dict) else {}
        try:
            res = F.simulate(body.get("flow"), body.get("applicant"), body.get("assets"))
        except F.FlowError as e:
            return _flow_err(e)
        return JSONResponse({"ok": True, "result": res}, headers={"Cache-Control": "no-store"})

    @router.post("/seal-request/api/flows")
    async def flows_create(request: Request):
        try:
            f = F.save_flow(None, await _body(request))
        except F.FlowError as e:
            return _flow_err(e)
        _audit(request, "seal_flow_change", f["name"], {"action": "create", "flow_id": f["id"],
                                                         "stages": len(f["stages"])})
        return JSONResponse(_flows_payload(), headers={"Cache-Control": "no-store"})

    @router.put("/seal-request/api/flows/{flow_id}")
    async def flows_update(request: Request, flow_id: int):
        try:
            f = F.save_flow(flow_id, await _body(request))
        except F.FlowError as e:
            return _flow_err(e)
        _audit(request, "seal_flow_change", f["name"], {"action": "update", "flow_id": f["id"],
                                                         "version": f["version"], "stages": len(f["stages"])})
        return JSONResponse(_flows_payload(), headers={"Cache-Control": "no-store"})

    @router.delete("/seal-request/api/flows/{flow_id}")
    async def flows_delete(request: Request, flow_id: int):
        f = S.get_flow(flow_id)
        if f is None or not S.delete_flow(flow_id):
            return _flow_err(F.FlowError("FLOW_NOT_FOUND", 404))
        _audit(request, "seal_flow_change", f["name"], {"action": "delete", "flow_id": flow_id})
        return JSONResponse(_flows_payload(), headers={"Cache-Control": "no-store"})

    @router.get("/seal-request/api/cases")
    async def cases_overview(request: Request):
        """管理員的案件總覽：只有案件資料（不含文件），標出需要改派的。"""
        out = []
        for c in S.list_cases("state<>'draft'", limit=1000):
            st = F.stage_status(c)
            out.append({k: c[k] for k in ("id", "number", "applicant_label", "flow_name", "subject", "state",
                                          "stage_idx", "version", "updated_at")}
                       | {"stage": ({k: st[k] for k in ("index", "name", "rule", "need", "approved", "stuck")}
                                    if st else None)})
        return JSONResponse({"ok": True, "cases": out}, headers={"Cache-Control": "no-store"})

    @router.post("/seal-request/api/cases/{case_id}/reassign")
    async def cases_reassign(request: Request, case_id: str):
        try:
            user = F.me(request)
            case = S.get_case(case_id)
            if case is None:
                raise F.FlowError("NOT_FOUND", 404)
            body = await _body(request)
            body = body if isinstance(body, dict) else {}
            case = F.reassign(user, case, body.get("candidates"), body.get("reason") if isinstance(body.get("reason"), str) else "")
        except F.FlowError as e:
            return _flow_err(e)
        _audit(request, "seal_req_reassign", case["number"], {"case_id": case["id"], "stage": case["stage_idx"],
                                                              "version": case["version"]})
        return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})



def register_void(router) -> None:
    from ..core import seal_flow as F
    from ..core import seal_store as S

    @router.post("/seal-request/api/cases/{case_id}/void")
    async def cases_void(request: Request, case_id: str):
        """作廢已完成的案件：原因必填；停止一般取件，已下載出去的檔案收不回。"""
        try:
            user = F.me(request)
            case = S.get_case(case_id)
            if case is None:
                raise F.FlowError("NOT_FOUND", 404)
            try:
                body = await request.json()
            except Exception:
                body = None
            reason = body.get("reason") if isinstance(body, dict) and isinstance(body.get("reason"), str) else ""
            case = F.void(user, case, reason)
        except F.FlowError as e:
            return _flow_err(e)
        _audit(request, "seal_req_void", case["number"], {"case_id": case["id"]})
        return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
