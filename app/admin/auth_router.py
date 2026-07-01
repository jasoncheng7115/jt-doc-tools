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
                    group_manager, permissions, roles, sso_settings,
                    user_manager)


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

    # ---------- /admin/sso (OIDC + SAML，附加登入) ----------

    @router.get("/sso", response_class=HTMLResponse)
    async def sso_page(request: Request):
        from ..core import sso_provision  # noqa: F401 (ensure importable)
        return templates.TemplateResponse(request, "admin_sso.html", {
            "request": request,
            "sso": sso_settings.get(),          # secrets masked
            "auth_enabled": auth_settings.is_enabled(),
        })

    @router.post("/sso/save")
    async def sso_save(request: Request):
        body = await request.json()
        oidc_in = body.get("oidc") or {}
        saml_in = body.get("saml") or {}
        new = {
            "base_url": (body.get("base_url") or "").strip().rstrip("/"),
            "oidc": {
                "enabled": bool(oidc_in.get("enabled")),
                "display_name": (oidc_in.get("display_name") or "").strip()[:64],
                "issuer": (oidc_in.get("issuer") or "").strip().rstrip("/"),
                "client_id": (oidc_in.get("client_id") or "").strip(),
                "client_secret_enc": oidc_in.get("client_secret_enc", sso_settings.SECRET_KEPT),
                "require_https": bool(oidc_in.get("require_https", True)),
                "scopes": (oidc_in.get("scopes") or "openid email profile").strip(),
                "username_claim": (oidc_in.get("username_claim") or "preferred_username").strip(),
                "email_claim": (oidc_in.get("email_claim") or "email").strip(),
                "name_claim": (oidc_in.get("name_claim") or "name").strip(),
                "groups_claim": (oidc_in.get("groups_claim") or "groups").strip(),
                "admin_group": (oidc_in.get("admin_group") or "").strip(),
            },
            "saml": {
                "enabled": bool(saml_in.get("enabled")),
                "display_name": (saml_in.get("display_name") or "").strip()[:64],
                "idp_entity_id": (saml_in.get("idp_entity_id") or "").strip(),
                "idp_sso_url": (saml_in.get("idp_sso_url") or "").strip(),
                "idp_slo_url": (saml_in.get("idp_slo_url") or "").strip(),
                "idp_x509cert": (saml_in.get("idp_x509cert") or "").strip(),
                "sp_entity_id": (saml_in.get("sp_entity_id") or "").strip(),
                "want_assertions_signed": bool(saml_in.get("want_assertions_signed", True)),
                "username_attr": (saml_in.get("username_attr") or "").strip(),
                "email_attr": (saml_in.get("email_attr") or "").strip(),
                "name_attr": (saml_in.get("name_attr") or "").strip(),
                "groups_attr": (saml_in.get("groups_attr") or "").strip(),
                "admin_group": (saml_in.get("admin_group") or "").strip(),
                "sp_private_key_enc": saml_in.get("sp_private_key_enc", sso_settings.SECRET_KEPT),
                "sp_x509cert": (saml_in.get("sp_x509cert") or "").strip(),
            },
        }
        # Guard: enabling SSO without a primary auth backend would let anyone the
        # IdP authenticates in, but there'd be no break-glass admin. Require auth on.
        if (new["oidc"]["enabled"] or new["saml"]["enabled"]) and not auth_settings.is_enabled():
            raise HTTPException(409, "請先於「認證設定」啟用認證並建立管理員，再開啟 SSO（保留 break-glass 帳號）")
        sso_settings.save(new)
        sso_settings._invalidate_cache()
        audit_db.log_event("settings_change", username=_actor(request),
                           ip=_client_ip(request), target="sso",
                           details={"oidc": new["oidc"]["enabled"],
                                    "saml": new["saml"]["enabled"]})
        return JSONResponse({"ok": True})

    @router.post("/sso/test")
    async def sso_test(request: Request):
        """Validate OIDC discovery + SAML SP metadata against current saved
        config (does not require enabling). Returns per-provider result."""
        body = await request.json()
        which = body.get("provider")
        out: dict = {}
        if which in (None, "oidc"):
            try:
                from ..core import oidc as _oidc
                cfg = sso_settings.get_oidc(reveal=True)
                doc = _oidc.discover(cfg)
                out["oidc"] = {"ok": True,
                               "authorization_endpoint": doc.get("authorization_endpoint"),
                               "token_endpoint": doc.get("token_endpoint")}
            except Exception as e:
                out["oidc"] = {"ok": False, "error": str(e)[:200]}
        if which in (None, "saml"):
            try:
                from ..core import saml as _saml
                cfg = sso_settings.get_saml(reveal=True)
                base = sso_settings.base_url()
                _saml.sp_metadata(cfg, base)
                out["saml"] = {"ok": True, "metadata_url": (base + "/auth/saml/metadata") if base else ""}
            except Exception as e:
                out["saml"] = {"ok": False, "error": str(e)[:200]}
        return JSONResponse({"ok": True, "result": out})

    # ---------- /admin/auth-settings ----------

    @router.get("/auth-settings", response_class=HTMLResponse)
    async def auth_settings_page(request: Request):
        s = auth_settings.get()
        return templates.TemplateResponse(request, "admin_auth_settings.html", {
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
        # Pull current lockout state so UI can flag locked users + offer
        # "解鎖" button.
        from ..core import auth_db
        import time as _t
        now = _t.time()
        lock_rows = auth_db.conn().execute(
            "SELECT key, locked_until FROM lockouts WHERE locked_until > ?",
            (now,),
        ).fetchall()
        locked_by_uid: dict[int, float] = {}
        locked_by_username: dict[str, float] = {}
        for r in lock_rows:
            key = r["key"] or ""
            until = r["locked_until"]
            if key.startswith("user:"):
                rest = key.split(":", 2)[1]
                if rest.isdigit():
                    locked_by_uid[int(rest)] = until
                else:
                    locked_by_username[rest] = until
        for u in users:
            u["roles_display"] = [
                {"id": rid, "display_name": role_name_by_id.get(rid, rid)}
                for rid in (u.get("roles") or [])
            ]
            until = (locked_by_uid.get(u["id"])
                     or locked_by_username.get(u["username"]) or 0)
            u["locked"] = bool(until and until > now)
            u["locked_until"] = until or None
        return templates.TemplateResponse(request, "admin_users.html", {
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

    @router.post("/users/{uid}/reset-totp")
    async def users_reset_totp(uid: int, request: Request):
        """Admin 重設使用者的 2FA — 清掉 secret + enabled。下次登入會被導
        去 /2fa-verify 強制 setup（重新顯示 QR）。用情境：使用者手機遺失
        無法產生 6 碼、或 admin 想強制重設。"""
        from ..core import auth_db, db, totp as _totp
        conn = auth_db.conn()
        row = conn.execute("SELECT username FROM users WHERE id=?",
                           (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "使用者不存在")
        _totp.disable(uid)
        # Also revoke active sessions so any cookie they have stops working
        with db.tx(conn):
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        audit_db.log_event(
            "user_2fa_reset", username=_actor(request),
            ip=_client_ip(request), target=row["username"],
        )
        return {"ok": True}

    @router.post("/users/{uid}/unlock")
    async def users_unlock(uid: int, request: Request):
        """清除這個 user 的密碼錯誤次數鎖定。Lockouts 表用 user_id 跟 IP
        當 key — 這裡只清 user 的，IP 鎖另外有「清所有 IP 鎖」按鈕。"""
        from ..core import auth_db, db
        conn = auth_db.conn()
        row = conn.execute("SELECT username FROM users WHERE id=?",
                           (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "使用者不存在")
        with db.tx(conn):
            cur = conn.execute(
                "DELETE FROM lockouts WHERE key LIKE ?",
                (f"user:{uid}:%",),
            )
            n_user = cur.rowcount
            # Older format may have used username — also clean
            cur2 = conn.execute(
                "DELETE FROM lockouts WHERE key LIKE ?",
                (f"user:{row['username']}:%",),
            )
            n_user += cur2.rowcount
        audit_db.log_event(
            "user_unlock", username=_actor(request), ip=_client_ip(request),
            target=str(uid), details={"cleared": n_user},
        )
        return {"ok": True, "cleared": n_user}

    @router.post("/auth-settings/unlock-all")
    async def auth_unlock_all(request: Request):
        """清除所有鎖定（含 IP-based）。緊急用 — 例如多人同時撞密碼鎖死全
        辦公室 IP。"""
        from ..core import auth_db, db
        conn = auth_db.conn()
        with db.tx(conn):
            cur = conn.execute("DELETE FROM lockouts")
            n = cur.rowcount
        audit_db.log_event(
            "lockouts_clear_all", username=_actor(request),
            ip=_client_ip(request), details={"cleared": n},
        )
        return {"ok": True, "cleared": n}

    # ---------- /admin/groups ----------

    @router.get("/groups", response_class=HTMLResponse)
    async def groups_page(request: Request):
        from ..core import auth_settings
        groups = group_manager.list_groups()
        all_users = user_manager.list_users()
        all_roles = roles.list_roles()
        backend = (auth_settings.get() or {}).get("backend", "off")
        return templates.TemplateResponse(request, "admin_groups.html", {
            "request": request,
            "groups": groups,
            "all_users": all_users,
            "all_roles": all_roles,
            "auth_backend": backend,
            "is_directory_backend": backend in ("ldap", "ad"),
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

    @router.post("/groups/sync-ldap")
    async def groups_sync_ldap(request: Request):
        """從 AD / LDAP 目錄列舉**所有**群組,鏡射進本地群組清單（不動成員）。
        解決預設「只看得到曾登入使用者所屬群組」的 JIT 限制 → admin 可預先把
        權限指派給任何目錄群組。"""
        from ..core import auth_settings, auth_ldap
        backend = (auth_settings.get() or {}).get("backend", "off")
        if backend not in ("ldap", "ad"):
            raise HTTPException(400, "目前認證後端不是 LDAP / AD，無法同步目錄群組。")
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        name_contains = str((body or {}).get("name_contains") or "").strip()
        try:
            result = auth_ldap.sync_all_groups(name_contains=name_contains)
        except auth_ldap.AuthError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"同步失敗：{type(e).__name__}: {e}")
        audit_db.log_event(
            "group_sync_ldap", username=_actor(request), ip=_client_ip(request),
            details=result,
        )
        return {"ok": True, **result}

    @router.get("/groups/{gid}/members-ldap")
    async def groups_members_ldap(gid: int, request: Request):
        """查某 AD/LDAP 群組在**目錄**裡的直接成員（含尚未登入過本系統的人）。"""
        from ..core import auth_settings, auth_ldap
        backend = (auth_settings.get() or {}).get("backend", "off")
        if backend not in ("ldap", "ad"):
            raise HTTPException(400, "此功能僅適用 LDAP / AD 群組。")
        g = group_manager.get(gid)
        if not g:
            raise HTTPException(404, "群組不存在")
        dn = (g.get("external_dn") or "").strip()
        if not dn:
            raise HTTPException(400, "此群組沒有目錄 DN（可能是本機群組）。")
        try:
            members = auth_ldap.get_group_members(dn)
        except auth_ldap.AuthError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"查詢失敗：{type(e).__name__}: {e}")
        # 標註哪些目錄成員「已登入過本系統」（本地 users 表有對應列）。以
        # external_dn（AD DN）為主鍵比對,退回 username（sAMAccountName）。
        local_dns, local_logins = set(), set()
        for u in user_manager.list_users():
            if u.get("source") in ("ldap", "ad"):
                if u.get("external_dn"):
                    local_dns.add(str(u["external_dn"]).strip().lower())
                if u.get("username"):
                    # username 可能是 sAMAccountName 或 name@realm，取 @ 前段一起比
                    un = str(u["username"]).strip().lower()
                    local_logins.add(un)
                    local_logins.add(un.split("@", 1)[0])
        local_count = 0
        for m in members:
            is_local = (
                (m.get("dn", "").strip().lower() in local_dns)
                or (m.get("login", "").strip().lower() in local_logins))
            m["local"] = is_local
            if is_local:
                local_count += 1
        return {"ok": True, "group": g.get("name"), "count": len(members),
                "local_count": local_count,
                "not_local_count": len(members) - local_count,
                "members": members}

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
        return templates.TemplateResponse(request, "admin_roles.html", {
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
                "source": u["source"],
                "is_admin_seed": u.get("is_admin_seed", False),
                "is_audit_seed": u.get("is_audit_seed", False),
                "roles": permissions.list_roles_for_subject("user", str(u["id"])),
                "direct_tools": permissions.list_direct_tools_for_subject("user", str(u["id"])),
            })
        for g in groups:
            subjects.append({
                "type": "group", "key": str(g["id"]),
                "label": f"群組：{g['name']}",
                "name": g["name"], "username": "",
                "source": g["source"], "is_admin_seed": False,
                "is_audit_seed": False,
                "roles": permissions.list_roles_for_subject("group", str(g["id"])),
                "direct_tools": permissions.list_direct_tools_for_subject("group", str(g["id"])),
            })
        tools_meta = [{"id": tid, "name": _tool_name(tid)} for tid in _all_tool_ids()]
        return templates.TemplateResponse(request, "admin_permissions.html", {
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
        # Built-in seed users (jtdt-admin / jtdt-auditor) 角色與工具是固定的，
        # 不可改 — 拒絕。
        if st == "user":
            from ..core import auth_db
            row = auth_db.conn().execute(
                "SELECT username, is_admin_seed, is_audit_seed FROM users WHERE id=?",
                (int(sk),),
            ).fetchone()
            if row and (row["is_admin_seed"] or row["is_audit_seed"]):
                raise HTTPException(
                    400,
                    f"內建帳號（{row['username']}）的角色與工具權限固定，"
                    "不可從權限矩陣修改。")
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
        return templates.TemplateResponse(request, "admin_audit.html", {
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
        return templates.TemplateResponse(request, "admin_uploads.html", {
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
        return templates.TemplateResponse(request, "admin_log_forward.html", {
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
        return templates.TemplateResponse(request, "admin_history.html", {
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
        return templates.TemplateResponse(request, "admin_retention.html", {
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

    # ---------- /admin/workspace ----------

    @router.get("/workspace", response_class=HTMLResponse)
    async def workspace_admin_page(request: Request):
        from ..core import workspace as _ws
        return templates.TemplateResponse(request, "admin_workspace.html", {
            "request": request,
            "settings": _ws.get_settings(),
            "stats": _ws.collect_stats(),
        })

    @router.post("/workspace/save")
    async def workspace_admin_save(request: Request):
        from ..core import workspace as _ws
        body = await request.json()
        try:
            saved = _ws.save_settings(body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        audit_db.log_event(
            "settings_change", username=_actor(request), ip=_client_ip(request),
            target="workspace", details=body,
        )
        return {"ok": True, "settings": saved}

    @router.post("/workspace/clear-user")
    async def workspace_admin_clear_user(request: Request, user_key: str = Form(...)):
        from ..core import workspace as _ws
        n = _ws.admin_clear_user(user_key)
        audit_db.log_event(
            "settings_change", username=_actor(request), ip=_client_ip(request),
            target="workspace", details={"action": "clear_user", "user_key": user_key, "removed": n},
        )
        return {"ok": True, "removed": n}

    @router.post("/workspace/clear-all")
    async def workspace_admin_clear_all(request: Request):
        from ..core import workspace as _ws
        n = _ws.admin_clear_all()
        audit_db.log_event(
            "settings_change", username=_actor(request), ip=_client_ip(request),
            target="workspace", details={"action": "clear_all", "removed": n},
        )
        return {"ok": True, "removed": n}

    # ---------- /admin/system-status ----------

    @router.get("/system-status", response_class=HTMLResponse)
    async def system_status_page(request: Request):
        return templates.TemplateResponse(request, 
            "admin_system_status.html", {"request": request},
        )

    @router.get("/system-status/host")
    async def system_status_host():
        """Fast — only psutil snapshot. Called every 5s by the auto-refresh."""
        from ..core import host_stats as _hs
        return _hs.get_host_stats()

    @router.get("/system-status/users")
    async def system_status_users(force: bool = False):
        """Slow — walks filesystem to compute per-user file counts + bytes.
        Cached 60s; pass `?force=1` to bypass cache (button on the page).
        Heavy IO offloaded to thread pool to keep the event loop free."""
        from ..core import host_stats as _hs
        import asyncio as _asyncio
        return await _asyncio.to_thread(_hs.get_user_file_stats, force)

    return router


def _tool_name(tool_id: str) -> str:
    """Look up the friendly name for a tool id from the registry."""
    from ..tool_registry import discover_tools
    for t in discover_tools():
        if t.metadata.id == tool_id:
            return t.metadata.name
    return tool_id
