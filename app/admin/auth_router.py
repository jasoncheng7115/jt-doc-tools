"""Admin endpoints for authentication, users, groups, roles, permissions.

All endpoints inherit `require_admin` from the parent admin router (added
via router-level dependency), so they're locked behind the admin role
when auth is on, and freely accessible when auth is off (existing
behaviour).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..core import (audit_db, audit_forward, auth_db, auth_settings,
                    group_manager, permissions, roles, user_manager)


def _all_tool_ids() -> list[str]:
    from ..tool_registry import discover_tools
    return [t.metadata.id for t in discover_tools()]

logger = logging.getLogger(__name__)


def _client_ip(r: Request) -> str:
    xff = r.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",", 1)[0].strip()[:64]
    return (r.client.host if r.client else "")[:64]


def _actor(r: Request) -> str:
    user = getattr(r.state, "user", None)
    return user["username"] if user else ""


def build_auth_router(templates) -> APIRouter:
    router = APIRouter()

    # ---------- /admin/auth-settings ----------

    @router.get("/auth-settings", response_class=HTMLResponse)
    async def auth_settings_page(request: Request):
        s = auth_settings.get()
        return templates.TemplateResponse("admin_auth_settings.html", {
            "request": request,
            "settings": s,
            "is_enabled": auth_settings.is_enabled(),
        })

    @router.post("/auth-settings/disable")
    async def auth_settings_disable(request: Request):
        if not auth_settings.is_enabled():
            return JSONResponse({"ok": True, "noop": True})
        auth_settings.disable_auth(actor=_actor(request), ip=_client_ip(request))
        return JSONResponse({"ok": True})

    @router.post("/auth-settings/ldap-save")
    async def auth_settings_ldap_save(request: Request):
        """Configure LDAP/AD settings. To switch the backend itself (off →
        ldap), set body['backend'] = 'ldap' or 'ad'; otherwise we just
        update the LDAP block.

        Switching from 'local' → 'ldap' will leave existing local users
        intact (they just won't be able to log in until you switch back).
        """
        # Defence in depth: refuse if auth is not enabled. The UI also locks
        # this form, but a curl/script could still hit the endpoint and lock
        # the admin out (no jtdt-admin exists yet to log back in with).
        if not auth_settings.is_enabled():
            raise HTTPException(
                409,
                "Cannot configure LDAP/AD backend before authentication is enabled. "
                "Visit /setup-admin to enable auth and create the first admin first.",
            )
        body = await request.json()
        target_backend = (body.get("backend") or "").lower()
        ldap_cfg = body.get("ldap") or {}
        if target_backend not in ("", "off", "local", "ldap", "ad"):
            raise HTTPException(400, "invalid backend")
        s = auth_settings.get()
        if target_backend:
            s["backend"] = target_backend
        # Merge new LDAP fields into the block (don't blow away service_password
        # if caller didn't provide one — admin is just editing other fields).
        for k in ("server_url", "use_tls", "verify_cert", "service_dn",
                  "user_search_base", "user_search_filter", "group_attr",
                  "username_attr", "displayname_attr"):
            if k in ldap_cfg:
                s["ldap"][k] = ldap_cfg[k]
        if ldap_cfg.get("service_password"):
            # Note: storing in plain JSON for v1.1.0 (file is mode 600).
            # M3+ enhancement: encrypt with Fernet keyed off session secret.
            s["ldap"]["service_password"] = ldap_cfg["service_password"]
        auth_settings.save(s)
        audit_db.log_event(
            "settings_change", username=_actor(request), ip=_client_ip(request),
            target="ldap", details={k: v for k, v in ldap_cfg.items()
                                    if k != "service_password"},
        )
        return {"ok": True, "backend": s["backend"]}

    def _build_ldap_cfg_from_request(body: dict) -> dict:
        """Compose an LDAP cfg dict from request body, falling back to the
        saved value for any field the user left blank — so the admin can
        test their just-edited form without re-entering the saved password."""
        saved = auth_settings.get().get("ldap", {}) or {}
        ldap_in = body.get("ldap") or {}
        merged = {}
        for k in ("server_url", "service_dn", "user_search_base",
                  "user_search_filter", "username_attr", "displayname_attr",
                  "group_attr"):
            v = ldap_in.get(k)
            merged[k] = (v if v not in (None, "") else saved.get(k, ""))
        # bools — accept explicit False from the form
        for k in ("use_tls", "verify_cert"):
            if k in ldap_in:
                merged[k] = bool(ldap_in[k])
            else:
                merged[k] = bool(saved.get(k, False))
        # password: if user typed a new one use it, else use saved.
        merged["service_password"] = (
            ldap_in.get("service_password") or saved.get("service_password", "")
        )
        return merged

    @router.post("/auth-settings/ldap-test-connection")
    async def auth_settings_ldap_test_connection(request: Request):
        from ..core import auth_ldap
        body = await request.json()
        cfg = _build_ldap_cfg_from_request(body)
        try:
            res = auth_ldap.test_connection(cfg)
        except auth_ldap.AuthError as exc:
            audit_db.log_event(
                "settings_change", username=_actor(request),
                ip=_client_ip(request), target="ldap_test_connection",
                details={"ok": False, "error": str(exc)[:200]},
            )
            raise HTTPException(400, str(exc))
        audit_db.log_event(
            "settings_change", username=_actor(request), ip=_client_ip(request),
            target="ldap_test_connection",
            details={"ok": True, "elapsed_ms": res.get("elapsed_ms")},
        )
        return res

    @router.post("/auth-settings/ldap-test-login")
    async def auth_settings_ldap_test_login(request: Request):
        from ..core import auth_ldap
        body = await request.json()
        cfg = _build_ldap_cfg_from_request(body)
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        try:
            res = auth_ldap.test_user_login(cfg, username, password)
        except auth_ldap.AuthError as exc:
            audit_db.log_event(
                "settings_change", username=_actor(request),
                ip=_client_ip(request), target="ldap_test_login",
                details={"ok": False, "tested_user": username,
                         "error": str(exc)[:200]},
            )
            raise HTTPException(400, str(exc))
        audit_db.log_event(
            "settings_change", username=_actor(request), ip=_client_ip(request),
            target="ldap_test_login",
            details={"ok": True, "tested_user": username,
                     "elapsed_ms": res.get("elapsed_ms")},
        )
        # Truncate group list before returning — we don't need them all on UI.
        res["groups"] = res.get("groups", [])[:20]
        return res

    # ---------- /admin/users ----------

    @router.get("/users", response_class=HTMLResponse)
    async def users_page(request: Request):
        users = user_manager.list_users()
        all_roles = roles.list_roles()
        all_groups = group_manager.list_groups()
        # Enrich each user with role display names so the table can show
        # human labels ("管理員") not just slugs ("admin"). Keep `roles` as
        # the slug list (backend contract) and add `roles_display`.
        role_name_by_id = {r["id"]: r["display_name"] for r in all_roles}
        for u in users:
            u["roles_display"] = [
                {"id": rid, "display_name": role_name_by_id.get(rid, rid)}
                for rid in (u.get("roles") or [])
            ]
        return templates.TemplateResponse("admin_users.html", {
            "request": request,
            "users": users,
            "all_roles": all_roles,
            "all_groups": all_groups,
            "auth_on": auth_settings.is_enabled(),
        })

    @router.post("/users/create")
    async def users_create(request: Request):
        body = await request.json()
        try:
            new_id = user_manager.create_local(
                username=body.get("username", ""),
                display_name=body.get("display_name", ""),
                password=body.get("password", ""),
                enabled=bool(body.get("enabled", True)),
                roles=body.get("roles") or ["default-user"],
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "user_create", username=_actor(request), ip=_client_ip(request),
            target=body.get("username", ""),
            details={"new_user_id": new_id, "roles": body.get("roles")},
        )
        return {"ok": True, "id": new_id}

    @router.post("/users/{uid}/update")
    async def users_update(uid: int, request: Request):
        body = await request.json()
        try:
            user_manager.update(
                uid,
                display_name=body.get("display_name"),
                enabled=body.get("enabled"),
                roles=body.get("roles"),
                groups=body.get("groups"),
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "user_update", username=_actor(request), ip=_client_ip(request),
            target=str(uid), details={k: v for k, v in body.items() if k != "password"},
        )
        return {"ok": True}

    @router.post("/users/{uid}/reset-password")
    async def users_reset_password(uid: int, request: Request):
        body = await request.json()
        try:
            user_manager.reset_password(uid, body.get("password", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "user_pwd_reset", username=_actor(request), ip=_client_ip(request),
            target=str(uid),
        )
        return {"ok": True}

    @router.post("/users/{uid}/delete")
    async def users_delete(uid: int, request: Request):
        try:
            user_manager.delete(uid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "user_delete", username=_actor(request), ip=_client_ip(request),
            target=str(uid),
        )
        return {"ok": True}

    # ---------- /admin/groups ----------

    @router.get("/groups", response_class=HTMLResponse)
    async def groups_page(request: Request):
        groups = group_manager.list_groups()
        all_users = user_manager.list_users()
        all_roles = roles.list_roles()
        return templates.TemplateResponse("admin_groups.html", {
            "request": request,
            "groups": groups,
            "all_users": all_users,
            "all_roles": all_roles,
        })

    @router.post("/groups/create")
    async def groups_create(request: Request):
        body = await request.json()
        try:
            gid = group_manager.create_local(
                name=body.get("name", ""),
                description=body.get("description", ""),
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "group_create", username=_actor(request), ip=_client_ip(request),
            target=body.get("name", ""), details={"new_group_id": gid},
        )
        return {"ok": True, "id": gid}

    @router.post("/groups/{gid}/update")
    async def groups_update(gid: int, request: Request):
        body = await request.json()
        try:
            group_manager.update(
                gid,
                name=body.get("name"),
                description=body.get("description"),
                roles=body.get("roles"),
            )
            if "members" in body:
                group_manager.set_members(gid, [int(m) for m in body["members"]])
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "group_update", username=_actor(request), ip=_client_ip(request),
            target=str(gid),
        )
        return {"ok": True}

    @router.post("/groups/{gid}/delete")
    async def groups_delete(gid: int, request: Request):
        try:
            group_manager.delete(gid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "group_delete", username=_actor(request), ip=_client_ip(request),
            target=str(gid),
        )
        return {"ok": True}

    # ---------- /admin/roles ----------

    @router.get("/roles", response_class=HTMLResponse)
    async def roles_page(request: Request):
        all_roles = roles.list_roles()
        # tool registry: id + display name
        tools_meta = [{"id": tid, "name": _tool_name(tid)} for tid in _all_tool_ids()]
        return templates.TemplateResponse("admin_roles.html", {
            "request": request,
            "roles": all_roles,
            "tools": tools_meta,
        })

    @router.post("/roles/create")
    async def roles_create(request: Request):
        body = await request.json()
        try:
            roles.create(
                role_id=body.get("id", ""),
                display_name=body.get("display_name", ""),
                description=body.get("description", ""),
                tools=body.get("tools") or [],
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "role_create", username=_actor(request), ip=_client_ip(request),
            target=body.get("id", ""),
        )
        return {"ok": True}

    @router.post("/roles/{role_id}/update")
    async def roles_update(role_id: str, request: Request):
        body = await request.json()
        try:
            roles.update(
                role_id,
                display_name=body.get("display_name"),
                description=body.get("description"),
                tools=body.get("tools"),
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        permissions.invalidate_cache()
        audit_db.log_event(
            "role_update", username=_actor(request), ip=_client_ip(request),
            target=role_id,
        )
        return {"ok": True}

    @router.post("/roles/{role_id}/delete")
    async def roles_delete(role_id: str, request: Request):
        try:
            roles.delete(role_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        permissions.invalidate_cache()
        audit_db.log_event(
            "role_delete", username=_actor(request), ip=_client_ip(request),
            target=role_id,
        )
        return {"ok": True}

    # ---------- /admin/permissions (matrix) ----------

    @router.get("/permissions", response_class=HTMLResponse)
    async def permissions_page(request: Request):
        users = user_manager.list_users()
        groups = group_manager.list_groups()
        all_roles = roles.list_roles()
        # Subjects shown in matrix: users + groups (OUs only when LDAP/AD active
        # and admin has set per-OU rules — TBD via M3).
        subjects = []
        for u in users:
            subjects.append({
                "type": "user", "key": str(u["id"]),
                "label": f"{u['display_name']} ({u['username']})",
                "name": u["display_name"], "username": u["username"],
                "source": u["source"], "is_admin_seed": u.get("is_admin_seed", False),
                "roles": permissions.list_roles_for_subject("user", str(u["id"])),
                "direct_tools": permissions.list_direct_tools_for_subject("user", str(u["id"])),
            })
        for g in groups:
            subjects.append({
                "type": "group", "key": str(g["id"]),
                "label": f"群組：{g['name']}",
                "name": g["name"], "username": "",
                "source": g["source"], "is_admin_seed": False,
                "roles": permissions.list_roles_for_subject("group", str(g["id"])),
                "direct_tools": permissions.list_direct_tools_for_subject("group", str(g["id"])),
            })
        tools_meta = [{"id": tid, "name": _tool_name(tid)} for tid in _all_tool_ids()]
        return templates.TemplateResponse("admin_permissions.html", {
            "request": request,
            "subjects": subjects,
            "all_roles": all_roles,
            "tools": tools_meta,
        })

    @router.post("/permissions/set")
    async def permissions_set(request: Request):
        body = await request.json()
        st = body.get("subject_type")
        sk = body.get("subject_key")
        if st not in ("user", "group", "ou") or not sk:
            raise HTTPException(400, "subject_type / subject_key required")
        try:
            if "roles" in body:
                permissions.set_subject_roles(st, str(sk), body["roles"])
            if "direct_tools" in body:
                # Replace direct grants
                from ..core import auth_db, db as _db
                conn = auth_db.conn()
                with _db.tx(conn):
                    conn.execute(
                        "DELETE FROM subject_perms WHERE subject_type=? AND subject_key=?",
                        (st, str(sk)),
                    )
                    for t in body["direct_tools"]:
                        conn.execute(
                            "INSERT OR IGNORE INTO subject_perms(subject_type, subject_key, tool_id) "
                            "VALUES (?,?,?)", (st, str(sk), t),
                        )
                permissions.invalidate_cache()
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "perm_change", username=_actor(request), ip=_client_ip(request),
            target=f"{st}:{sk}",
            details={k: body.get(k) for k in ("roles", "direct_tools") if k in body},
        )
        return {"ok": True}

    # ---------- /admin/audit ----------

    @router.get("/audit", response_class=HTMLResponse)
    async def audit_page(request: Request,
                         q_user: str = "", q_event: str = "",
                         q_from: str = "", q_to: str = "",
                         page: int = 1, page_size: int = 100):
        page = max(1, page)
        page_size = min(500, max(10, page_size))
        offset = (page - 1) * page_size
        # Build SQL conditions
        conds, params = [], []
        if q_user:
            conds.append("username = ?")
            params.append(q_user)
        if q_event:
            conds.append("event_type = ?")
            params.append(q_event)
        if q_from:
            try:
                import datetime as _dt
                ts_from = _dt.datetime.fromisoformat(q_from).timestamp()
                conds.append("ts >= ?"); params.append(ts_from)
            except ValueError:
                pass
        if q_to:
            try:
                import datetime as _dt
                ts_to = _dt.datetime.fromisoformat(q_to).timestamp()
                conds.append("ts <= ?"); params.append(ts_to)
            except ValueError:
                pass
        where = " WHERE " + " AND ".join(conds) if conds else ""

        c = audit_db.conn()
        total = c.execute(f"SELECT count(*) FROM audit_events{where}",
                          tuple(params)).fetchone()[0]
        rows = c.execute(
            f"SELECT id, ts, username, ip, event_type, target, details_json "
            f"FROM audit_events{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params) + (page_size, offset),
        ).fetchall()
        events = [dict(r) for r in rows]
        # Distinct values for filter dropdowns
        distinct_events = [r[0] for r in c.execute(
            "SELECT DISTINCT event_type FROM audit_events ORDER BY event_type"
        ).fetchall()]
        distinct_users = [r[0] for r in c.execute(
            "SELECT DISTINCT username FROM audit_events WHERE username != '' "
            "ORDER BY username"
        ).fetchall()]
        # File size for the warning banner.
        from . import router as _admin_router_mod  # noqa
        from ..core import db as _db
        size_bytes = _db.db_size_bytes(audit_db.audit_db_path())
        return templates.TemplateResponse("admin_audit.html", {
            "request": request,
            "events": events,
            "total": total,
            "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
            "q_user": q_user, "q_event": q_event,
            "q_from": q_from, "q_to": q_to,
            "distinct_events": distinct_events,
            "distinct_users": distinct_users,
            "size_mb": size_bytes / 1024 / 1024,
            "size_warn": size_bytes > 5 * 1024 * 1024 * 1024,
        })

    @router.get("/audit/export.csv")
    async def audit_export_csv(request: Request,
                               q_user: str = "", q_event: str = "",
                               q_from: str = "", q_to: str = ""):
        import csv as _csv
        import io as _io
        from datetime import datetime as _dt
        from fastapi.responses import StreamingResponse
        conds, params = [], []
        if q_user:
            conds.append("username = ?"); params.append(q_user)
        if q_event:
            conds.append("event_type = ?"); params.append(q_event)
        if q_from:
            try:
                conds.append("ts >= ?"); params.append(_dt.fromisoformat(q_from).timestamp())
            except ValueError:
                pass
        if q_to:
            try:
                conds.append("ts <= ?"); params.append(_dt.fromisoformat(q_to).timestamp())
            except ValueError:
                pass
        where = " WHERE " + " AND ".join(conds) if conds else ""
        rows = audit_db.conn().execute(
            f"SELECT id, ts, username, ip, event_type, target, details_json "
            f"FROM audit_events{where} ORDER BY id DESC", tuple(params)
        ).fetchall()

        buf = _io.StringIO()
        # UTF-8 BOM so Excel opens it as UTF-8 by default
        buf.write("﻿")
        w = _csv.writer(buf)
        w.writerow(["id", "time", "user", "ip", "event_type", "target", "details"])
        for r in rows:
            t = _dt.fromtimestamp(r["ts"]).isoformat(sep=" ", timespec="seconds")
            w.writerow([r["id"], t, r["username"], r["ip"],
                        r["event_type"], r["target"], r["details_json"]])
        from ..core.http_utils import content_disposition
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     content_disposition(f"audit_{_dt.now():%Y%m%d_%H%M%S}.csv")},
        )

    # ---------- /admin/uploads (file-upload activity) ----------

    @router.get("/uploads", response_class=HTMLResponse)
    async def uploads_page(request: Request,
                           q_user: str = "", q_tool: str = "",
                           q_filename: str = "",
                           q_from: str = "", q_to: str = "",
                           page: int = 1, page_size: int = 50):
        """Uploads activity log — derived from `audit_events` rows where
        event_type='tool_invoke' AND details_json contains a `filename`
        (filled in by the upload-filename middleware in main.py).
        """
        import json as _json
        page = max(1, page)
        page_size = min(500, max(10, page_size))
        offset = (page - 1) * page_size
        conds = ["event_type = 'tool_invoke'", "details_json LIKE '%\"filename\"%'"]
        params: list = []
        if q_user:
            conds.append("username = ?")
            params.append(q_user)
        if q_tool:
            conds.append("target = ?")
            params.append(q_tool)
        if q_filename:
            # Crude substring match on the JSON blob — fine since filename
            # appears as `"filename": "X"` within details_json.
            conds.append("details_json LIKE ?")
            params.append(f"%{q_filename}%")
        if q_from:
            try:
                import datetime as _dt
                conds.append("ts >= ?"); params.append(_dt.datetime.fromisoformat(q_from).timestamp())
            except ValueError:
                pass
        if q_to:
            try:
                import datetime as _dt
                conds.append("ts <= ?"); params.append(_dt.datetime.fromisoformat(q_to).timestamp())
            except ValueError:
                pass
        where = " WHERE " + " AND ".join(conds)

        c = audit_db.conn()
        total = c.execute(f"SELECT count(*) FROM audit_events{where}",
                          tuple(params)).fetchone()[0]
        rows = c.execute(
            f"SELECT id, ts, username, ip, target, details_json "
            f"FROM audit_events{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params) + (page_size, offset),
        ).fetchall()
        uploads = []
        total_bytes = 0
        for r in rows:
            try:
                d = _json.loads(r["details_json"] or "{}")
            except Exception:
                d = {}
            sz = int(d.get("size_bytes") or 0)
            total_bytes += sz
            uploads.append({
                "id": r["id"],
                "ts": r["ts"],
                "username": r["username"] or "(匿名)",
                "ip": r["ip"],
                "tool_id": r["target"],
                "filename": d.get("filename", ""),
                "filenames": d.get("filenames"),
                "file_count": d.get("count") or (1 if d.get("filename") else 0),
                "action": d.get("action", ""),
                "size_bytes": sz,
                "status": d.get("status", 0),
            })
        # Distinct dropdowns
        distinct_users = [r[0] for r in c.execute(
            "SELECT DISTINCT username FROM audit_events "
            "WHERE event_type='tool_invoke' AND username != '' "
            "AND details_json LIKE '%\"filename\"%' ORDER BY username"
        ).fetchall()]
        distinct_tools = [r[0] for r in c.execute(
            "SELECT DISTINCT target FROM audit_events "
            "WHERE event_type='tool_invoke' AND target != '' "
            "AND details_json LIKE '%\"filename\"%' ORDER BY target"
        ).fetchall()]
        return templates.TemplateResponse("admin_uploads.html", {
            "request": request,
            "uploads": uploads,
            "total": total,
            "total_bytes": total_bytes,
            "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
            "q_user": q_user, "q_tool": q_tool, "q_filename": q_filename,
            "q_from": q_from, "q_to": q_to,
            "distinct_users": distinct_users,
            "distinct_tools": distinct_tools,
        })

    # ---------- /admin/log-forward ----------

    @router.get("/log-forward", response_class=HTMLResponse)
    async def log_forward_page(request: Request):
        cfg = audit_forward.get()
        return templates.TemplateResponse("admin_log_forward.html", {
            "request": request,
            "destinations": cfg.get("destinations", []),
        })

    @router.post("/log-forward/save")
    async def log_forward_save(request: Request):
        body = await request.json()
        dests = body.get("destinations") or []
        # Validate
        cleaned = []
        import uuid as _uu
        for d in dests:
            if not isinstance(d, dict):
                continue
            fmt = d.get("format")
            if fmt not in ("syslog", "cef", "gelf"):
                raise HTTPException(400, f"unsupported format: {fmt}")
            transport = d.get("transport", "udp")
            if transport not in ("udp", "tcp"):
                raise HTTPException(400, f"unsupported transport: {transport}")
            host = (d.get("host") or "").strip()
            if not host:
                raise HTTPException(400, "host required")
            try:
                port = int(d.get("port", 514))
            except ValueError:
                raise HTTPException(400, "port must be int")
            if port < 1 or port > 65535:
                raise HTTPException(400, "port out of range")
            cleaned.append({
                "id": d.get("id") or _uu.uuid4().hex[:12],
                "name": (d.get("name") or "")[:80] or f"{fmt}://{host}:{port}",
                "format": fmt,
                "transport": transport,
                "host": host,
                "port": port,
                "enabled": bool(d.get("enabled", True)),
            })
        audit_forward.save({"destinations": cleaned})
        audit_db.log_event(
            "settings_change", username=_actor(request), ip=_client_ip(request),
            target="log_forward", details={"destination_count": len(cleaned)},
        )
        # Make sure worker is running
        audit_forward.start_worker()
        return {"ok": True, "count": len(cleaned)}

    # ---------- /admin/history (fill / stamp / watermark) ----------

    @router.get("/history", response_class=HTMLResponse)
    async def history_redirect(request: Request):
        return RedirectResponse("/admin/history/fill", status_code=302)

    @router.get("/history/{kind}", response_class=HTMLResponse)
    async def history_page(kind: str, request: Request,
                           q_user: str = ""):
        from ..core.history_manager import (history_manager, stamp_history,
                                              watermark_history)
        managers = {"fill": (history_manager, "表單填寫", "/tools/pdf-fill"),
                    "stamp": (stamp_history, "用印與簽名", "/tools/pdf-stamp"),
                    "watermark": (watermark_history, "浮水印", "/tools/pdf-watermark")}
        if kind not in managers:
            raise HTTPException(404)
        mgr, title, tool_url = managers[kind]
        entries = mgr.list_all()
        if q_user:
            entries = [e for e in entries if (e.get("username") or "") == q_user]
        users = sorted({e.get("username") or "(匿名)" for e in mgr.list_all()})
        return templates.TemplateResponse("admin_history.html", {
            "request": request,
            "kind": kind, "title": title, "tool_url": tool_url,
            "entries": entries, "users": users, "q_user": q_user,
        })

    @router.get("/history/{kind}/{hid}/file/{which}")
    async def history_file(kind: str, hid: str, which: str):
        from ..core.history_manager import (history_manager, stamp_history,
                                              watermark_history)
        from fastapi.responses import FileResponse
        mgr_map = {"fill": history_manager, "stamp": stamp_history,
                   "watermark": watermark_history}
        mgr = mgr_map.get(kind)
        if not mgr:
            raise HTTPException(404)
        p = mgr.file(hid, which)
        if not p:
            raise HTTPException(404)
        media = "image/png" if which == "preview" else "application/pdf"
        return FileResponse(str(p), media_type=media, filename=p.name)

    @router.post("/history/{kind}/{hid}/delete")
    async def history_delete(kind: str, hid: str, request: Request):
        from ..core.history_manager import (history_manager, stamp_history,
                                              watermark_history)
        mgr_map = {"fill": history_manager, "stamp": stamp_history,
                   "watermark": watermark_history}
        mgr = mgr_map.get(kind)
        if not mgr:
            raise HTTPException(404)
        ok = mgr.delete(hid)
        if not ok:
            raise HTTPException(404)
        audit_db.log_event(
            "history_delete", username=_actor(request), ip=_client_ip(request),
            target=f"{kind}:{hid}",
        )
        return {"ok": True}

    # ---------- /admin/retention ----------

    @router.get("/retention", response_class=HTMLResponse)
    async def retention_page(request: Request):
        from ..core import retention as _ret
        return templates.TemplateResponse("admin_retention.html", {
            "request": request,
            "settings": _ret.get(),
            "stats": _ret.collect_stats(),
        })

    @router.post("/retention/save")
    async def retention_save(request: Request):
        from ..core import retention as _ret
        body = await request.json()
        try:
            _ret.save(body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "settings_change", username=_actor(request), ip=_client_ip(request),
            target="retention", details=body,
        )
        return {"ok": True}

    @router.post("/retention/sweep-now")
    async def retention_sweep_now(request: Request):
        from ..core import retention as _ret
        report = _ret.sweep_all()
        audit_db.log_event(
            "retention_sweep", username=_actor(request), ip=_client_ip(request),
            details=report,
        )
        return {"ok": True, "report": report}

    return router


def _tool_name(tool_id: str) -> str:
    """Look up the friendly name for a tool id from the registry."""
    from ..tool_registry import discover_tools
    for t in discover_tools():
        if t.metadata.id == tool_id:
            return t.metadata.name
    return tool_id
