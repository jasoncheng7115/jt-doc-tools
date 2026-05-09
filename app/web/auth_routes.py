"""HTTP routes for login / logout / first-time admin bootstrap.

Mounted at the app root (no prefix). All endpoints listed here are PUBLIC
— the auth middleware lets them through unauthenticated.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote as _qstr

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..core import audit_db, auth as _auth, auth_local, auth_settings, sessions

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honours X-Forwarded-For (first hop) when set
    by a trusted reverse proxy. Caller MUST configure their reverse proxy
    to strip incoming XFF and set its own; otherwise an attacker can spoof
    by adding the header themselves."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        # left-most is the original client (per convention).
        return xff.split(",", 1)[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _set_session_cookie(response: Response, token: str, *, remember: bool,
                       request: Request, expires_at: float) -> None:
    """Apply the session cookie with secure flags. Secure flag is enabled
    when the request looks HTTPS (proxy hint or direct)."""
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
    )
    s = auth_settings.get()
    max_age_days = s["remember_max_age_days"] if remember else s["session_max_age_days"]
    response.set_cookie(
        key=sessions.COOKIE_NAME,
        value=token,
        max_age=max_age_days * 86400,
        httponly=True,        # JS can't read
        secure=is_https,      # never sent over plain HTTP when origin is HTTPS
        samesite="lax",       # CSRF defence on cross-site POST
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(sessions.COOKIE_NAME, path="/")


# In-memory rate limit for /change-password failed attempts.
# Maps user_id → (last_fail_ts, fail_count). After 5 fails within 10 min
# the user is locked out from change-password (their normal login still works).
# Process-local; OK for a single-uvicorn deployment.
_CHGPW_FAILS: dict[int, tuple[float, int]] = {}


# --- Pending 2FA store -----------------------------------------------------
# Process-local dict of {pending_token: {user_id, ts, remember, ip, ua,
# next_url, forced_setup}}. After password OK but before TOTP verify, we
# stash the partial-auth state here so the /2fa-verify page can complete
# login. TTL 5 min — long enough to type a code from your phone, short
# enough to limit replay window if cookie leaks.
import secrets as _secrets
PENDING_2FA_COOKIE = "jtdt_pending_2fa"
PENDING_2FA_TTL = 5 * 60   # seconds
_PENDING_2FA: dict[str, dict] = {}


def _stash_pending_2fa(*, user_id: int, remember: bool, ip: str, ua: str,
                       next_url: str, forced_setup: bool) -> str:
    import time as _t
    # GC stale entries
    cutoff = _t.time() - PENDING_2FA_TTL
    for k in list(_PENDING_2FA.keys()):
        if _PENDING_2FA[k]["ts"] < cutoff:
            del _PENDING_2FA[k]
    token = _secrets.token_urlsafe(24)
    _PENDING_2FA[token] = {
        "user_id": user_id, "ts": _t.time(), "remember": remember,
        "ip": ip, "ua": ua, "next_url": next_url, "forced_setup": forced_setup,
        "fails": 0,
    }
    return token


def _consume_pending_2fa(token: str) -> Optional[dict]:
    """Look up + return entry, but don't delete (lets retries work).
    Returns None if missing or expired."""
    import time as _t
    if not token:
        return None
    entry = _PENDING_2FA.get(token)
    if not entry:
        return None
    if (_t.time() - entry["ts"]) > PENDING_2FA_TTL:
        _PENDING_2FA.pop(token, None)
        return None
    return entry


def _drop_pending_2fa(token: str) -> None:
    _PENDING_2FA.pop(token, None)


def build_router(templates) -> APIRouter:
    router = APIRouter()

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/", error: str = ""):
        # If auth is off → landing page (no login needed)
        if not auth_settings.is_enabled():
            return RedirectResponse("/", status_code=302)
        # Already logged in → redirect onward
        if request.cookies.get(sessions.COOKIE_NAME):
            cur = sessions.lookup(request.cookies[sessions.COOKIE_NAME])
            if cur:
                return RedirectResponse(_safe_next(next), status_code=302)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": error, "next": _safe_next(next),
             "realms": _auth.available_realms(),
             "default_realm": _auth.default_realm()},
        )

    @router.post("/login")
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        remember: Optional[str] = Form(None),
        next: str = Form("/"),
        realm: str = Form(""),
    ):
        # If auth somehow off, accepting login would be confusing — refuse.
        if not auth_settings.is_enabled():
            return RedirectResponse("/", status_code=302)
        ip = _client_ip(request)
        ua = request.headers.get("User-Agent", "")
        try:
            user = _auth.authenticate(username, password, ip=ip, realm=realm)
        except (_auth.AuthError, auth_local.AuthError) as e:
            # Re-render login form with error. Status 200 (form-style) — we
            # don't want POST→302→GET to lose the password field's flash.
            return templates.TemplateResponse(
                "login.html",
                {"request": request,
                 "error": str(e),
                 "username": (username or "")[:64],
                 "next": _safe_next(next),
                 "realms": _auth.available_realms(),
                 "default_realm": realm or _auth.default_realm()},
                status_code=200,
            )
        # 2FA check — 在 issue session 之前。auditor role 強制 TOTP；
        # 其他 user 自選。totp_required + 沒 setup → 強制走 setup；
        # totp_enabled → 要求驗 6 碼。詳見 totp 模組 + /2fa-verify 路由。
        from ..core import totp as _totp, permissions as _perm
        try:
            tstate = _totp.get_user_totp_state(user["user_id"])
        except Exception:
            tstate = {"enabled": False, "required": False, "has_secret": False}
        # 預先計算「是否強制」（auditor role 自動視為 required，即使 DB
        # 還沒設 totp_required，也強制；admin 後台另開的也吃 DB column）
        try:
            forced_by_role = _perm.is_auditor(user["user_id"])
        except Exception:
            forced_by_role = False
        needs_2fa = tstate["enabled"] or tstate["required"] or forced_by_role
        if needs_2fa:
            pending_token = _stash_pending_2fa(
                user_id=user["user_id"], remember=bool(remember),
                ip=ip, ua=ua, next_url=_safe_next(next),
                forced_setup=(not tstate["enabled"]),
            )
            resp = RedirectResponse("/2fa-verify", status_code=302)
            resp.set_cookie(key=PENDING_2FA_COOKIE, value=pending_token,
                            max_age=PENDING_2FA_TTL, httponly=True,
                            samesite="lax", path="/")
            return resp
        token, expires_at = sessions.issue(
            user["user_id"], remember=bool(remember), ip=ip, ua=ua,
        )
        resp = RedirectResponse(_safe_next(next), status_code=302)
        _set_session_cookie(resp, token, remember=bool(remember),
                            request=request, expires_at=expires_at)
        return resp

    @router.post("/change-password")
    async def change_password(request: Request):
        """Self-service password change for the logged-in local user.

        Security model:
        - **user_id from server-side session lookup, never from request body**
          → impossible to change another user's password by manipulating
          the request payload.
        - SameSite=Lax cookie blocks cross-site CSRF on this POST.
        - `verify_password()` is constant-time (argon2 / bcrypt).
        - Failed attempts (wrong old password) audited as
          `password_change_fail` so admin sees brute-force from a
          potentially-stolen session.
        - Rate limit: max 5 fails per user per 10 min → 429 lockout.
        - LDAP / AD users explicitly rejected (their hash isn't ours to set).

        Requires JSON body `{old_password, new_password}`. Auth backend
        OFF → 404 (no user concept). Wrong old password → 400.
        """
        from fastapi.responses import JSONResponse
        from ..core import user_manager, sessions as _sessions
        if not auth_settings.is_enabled():
            return JSONResponse({"error": "auth_off",
                                 "detail": "未啟用認證，無密碼可改"},
                                status_code=404)
        token = request.cookies.get(sessions.COOKIE_NAME, "")
        user = sessions.lookup(token) if token else None
        if not user:
            return JSONResponse({"error": "unauthorized",
                                 "detail": "請先登入"}, status_code=401)
        # Username for audit always includes realm (jason@local / jason@ldap)
        actor_label = _sessions.user_label(user)
        # Rate limit: in-memory per-user fail counter (defined at module level)
        import time as _t
        uid = user["user_id"]
        now = _t.time()
        # Prune old entries (> 10 min) — keep dict from growing unbounded
        for _k in [k for k, v in _CHGPW_FAILS.items() if now - v[0] >= 600]:
            _CHGPW_FAILS.pop(_k, None)
        cur = _CHGPW_FAILS.get(uid)
        if cur and cur[1] >= 5:
            audit_db.log_event(
                "password_change_lockout", username=actor_label,
                ip=_client_ip(request),
            )
            return JSONResponse({
                "error": "locked",
                "detail": "輸入錯誤次數太多，請等 10 分鐘後再試",
            }, status_code=429)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "bad_request",
                                 "detail": "需要 JSON body"}, status_code=400)
        old = str(body.get("old_password") or "")
        new = str(body.get("new_password") or "")
        if not old or not new:
            return JSONResponse({"error": "bad_request",
                                 "detail": "需要 old_password 跟 new_password"},
                                status_code=400)
        try:
            user_manager.change_password(
                uid, old, new, keep_current_session=token,
            )
        except ValueError as e:
            # Bump fail counter so brute force gets locked out
            ts, n = _CHGPW_FAILS.get(uid, (now, 0))
            _CHGPW_FAILS[uid] = (now, n + 1)
            audit_db.log_event(
                "password_change_fail", username=actor_label,
                ip=_client_ip(request),
                details={"reason": str(e), "fail_count": n + 1},
            )
            # v1.5.4 CodeQL py/stack-trace-exposure FP: e is our own ValueError
            # from user_manager.change_password (e.g.「舊密碼錯誤」/「密碼太短」),
            # 沒有 stack trace 資訊。給 user 看才知道為什麼被拒。
            return JSONResponse({"error": "rejected", "detail": str(e)},  # noqa: E501
                                status_code=400)  # codeql[py/stack-trace-exposure]
        # Success — clear fail counter
        _CHGPW_FAILS.pop(uid, None)
        audit_db.log_event(
            "password_change", username=actor_label,
            ip=_client_ip(request),
        )
        return JSONResponse({"ok": True,
                             "detail": "密碼已變更，其他裝置的登入會被登出"})

    # ---------- 2FA / TOTP --------------------------------------------------

    @router.get("/2fa-verify", response_class=HTMLResponse)
    async def twofa_verify_page(request: Request):
        """Show the 6-digit code input. If user is in `forced_setup` state
        (no secret yet but role / admin requires TOTP), show setup screen
        with QR code first."""
        from ..core import totp as _totp
        ptoken = request.cookies.get(PENDING_2FA_COOKIE, "")
        entry = _consume_pending_2fa(ptoken)
        if not entry:
            # Cookie expired / missing — back to login
            return RedirectResponse("/login?error=session+expired", status_code=302)
        uid = entry["user_id"]
        # 取 user 資訊用於 setup page 顯示 issuer + username
        from ..core import auth_db, branding
        urow = auth_db.conn().execute(
            "SELECT username, display_name FROM users WHERE id=?", (uid,),
        ).fetchone()
        if not urow:
            return RedirectResponse("/login?error=user+not+found", status_code=302)
        # 強制 setup 流程（required + 沒 secret）
        secret = _totp.get_secret(uid)
        is_setup = entry.get("forced_setup") and not secret
        qr_url = ""
        if is_setup:
            # 為使用者產一個 secret 並寫進 DB（totp_enabled 仍 0，要驗碼才啟用）
            if not secret:
                secret = _totp.new_secret()
                _totp.set_secret(uid, secret)
            uri = _totp.provision_uri(
                secret, urow["username"], branding.get_site_name(default="jt-doc-tools"),
            )
            qr_url = _totp.qr_png_data_url(uri)
        return templates.TemplateResponse("twofa_verify.html", {
            "request": request,
            "username": urow["username"],
            "display_name": urow["display_name"] or urow["username"],
            "is_setup": is_setup,
            "qr_url": qr_url,
            "secret_for_manual": secret if is_setup else "",
            "fails": entry.get("fails", 0),
        })

    @router.post("/2fa-verify")
    async def twofa_verify_submit(request: Request, code: str = Form(...)):
        from ..core import totp as _totp
        ptoken = request.cookies.get(PENDING_2FA_COOKIE, "")
        entry = _consume_pending_2fa(ptoken)
        if not entry:
            return RedirectResponse("/login?error=session+expired", status_code=302)
        uid = entry["user_id"]
        secret = _totp.get_secret(uid)
        if not secret:
            # 不該發生（應該在 GET setup 階段先生成）
            return RedirectResponse("/2fa-verify", status_code=302)
        if not _totp.verify_code(secret, code):
            entry["fails"] += 1
            audit_db.log_event(
                "2fa_fail", username=str(uid), ip=_client_ip(request),
                details={"fail_count": entry["fails"]},
            )
            if entry["fails"] >= 5:
                _drop_pending_2fa(ptoken)
                return RedirectResponse(
                    "/login?error=2FA+failed+too+many+times", status_code=302)
            return templates.TemplateResponse("twofa_verify.html", {
                "request": request, "error": "驗證碼錯誤，請再試一次",
                "fails": entry["fails"],
                "is_setup": False,  # 失敗時不重新顯示 QR（避免 secret 反覆暴露）
                "qr_url": "", "secret_for_manual": "",
            }, status_code=200)
        # 成功 — 第一次就標 enabled；發 session；清 pending
        if not _totp.get_user_totp_state(uid)["enabled"]:
            _totp.mark_enabled(uid)
            audit_db.log_event(
                "2fa_enabled", username=str(uid), ip=_client_ip(request),
            )
        audit_db.log_event(
            "2fa_success", username=str(uid), ip=_client_ip(request),
        )
        ip = entry["ip"]; ua = entry["ua"]
        token, expires_at = sessions.issue(
            uid, remember=entry["remember"], ip=ip, ua=ua,
        )
        next_url = entry.get("next_url") or "/"
        resp = RedirectResponse(next_url, status_code=302)
        _set_session_cookie(resp, token, remember=entry["remember"],
                            request=request, expires_at=expires_at)
        resp.delete_cookie(PENDING_2FA_COOKIE, path="/")
        _drop_pending_2fa(ptoken)
        return resp

    @router.get("/me/2fa", response_class=HTMLResponse)
    async def my_2fa_page(request: Request):
        """Self-service: 啟用 / 停用 / 重新生 TOTP secret。需登入；audit role
        強制 enabled，不能 disable（disable button 隱藏）。"""
        from ..core import auth_settings as _as
        if not _as.is_enabled():
            raise HTTPException(404, "auth not enabled")
        token = request.cookies.get(sessions.COOKIE_NAME, "")
        user = sessions.lookup(token) if token else None
        if not user:
            return RedirectResponse("/login?next=/me/2fa", status_code=302)
        from ..core import totp as _totp, permissions as _perm
        uid = user["user_id"]
        st = _totp.get_user_totp_state(uid)
        forced = st["required"] or _perm.is_auditor(uid)
        return templates.TemplateResponse("me_2fa.html", {
            "request": request,
            "username": user["username"],
            "enabled": st["enabled"],
            "required": forced,
        })

    @router.post("/me/2fa/start")
    async def my_2fa_start(request: Request):
        """Generate a new secret + return QR code. 已 enabled 的 user 也可
        re-generate（用情境：手機遺失需換新 secret）— 但會把 enabled 重置
        為 0，下次驗碼成功後才再 enable。"""
        from ..core import auth_settings as _as
        if not _as.is_enabled():
            raise HTTPException(404)
        token = request.cookies.get(sessions.COOKIE_NAME, "")
        user = sessions.lookup(token) if token else None
        if not user:
            raise HTTPException(401)
        from ..core import totp as _totp, branding
        uid = user["user_id"]
        secret = _totp.new_secret()
        _totp.set_secret(uid, secret)
        uri = _totp.provision_uri(
            secret, user["username"], branding.get_site_name(default="jt-doc-tools"),
        )
        return JSONResponse({
            "ok": True,
            "qr_url": _totp.qr_png_data_url(uri),
            "secret": secret,  # 顯示給 user 手動輸入備用
        })

    @router.post("/me/2fa/verify")
    async def my_2fa_verify(request: Request):
        """Confirm initial setup or re-setup."""
        from ..core import auth_settings as _as
        if not _as.is_enabled():
            raise HTTPException(404)
        token = request.cookies.get(sessions.COOKIE_NAME, "")
        user = sessions.lookup(token) if token else None
        if not user:
            raise HTTPException(401)
        body = await request.json()
        code = str(body.get("code") or "")
        from ..core import totp as _totp
        uid = user["user_id"]
        secret = _totp.get_secret(uid)
        if not secret:
            return JSONResponse(
                {"ok": False, "detail": "請先按「啟用」生成 secret"}, status_code=400)
        if not _totp.verify_code(secret, code):
            audit_db.log_event(
                "2fa_setup_fail", username=user["username"],
                ip=_client_ip(request),
            )
            return JSONResponse(
                {"ok": False, "detail": "驗證碼錯誤"}, status_code=400)
        _totp.mark_enabled(uid)
        audit_db.log_event(
            "2fa_enabled", username=user["username"], ip=_client_ip(request),
        )
        return JSONResponse({"ok": True, "detail": "TOTP 已啟用"})

    @router.post("/me/2fa/disable")
    async def my_2fa_disable(request: Request):
        from ..core import auth_settings as _as
        if not _as.is_enabled():
            raise HTTPException(404)
        token = request.cookies.get(sessions.COOKIE_NAME, "")
        user = sessions.lookup(token) if token else None
        if not user:
            raise HTTPException(401)
        from ..core import totp as _totp, permissions as _perm
        uid = user["user_id"]
        st = _totp.get_user_totp_state(uid)
        # 強制要求的 user（含 audit role）不能自己 disable
        if st["required"] or _perm.is_auditor(uid):
            return JSONResponse(
                {"ok": False, "detail": "您的角色強制使用 2FA，不能自行停用"},
                status_code=403)
        _totp.disable(uid)
        audit_db.log_event(
            "2fa_disabled", username=user["username"], ip=_client_ip(request),
        )
        return JSONResponse({"ok": True, "detail": "已停用 TOTP"})

    @router.post("/logout")
    async def logout(request: Request):
        token = request.cookies.get(sessions.COOKIE_NAME, "")
        if token:
            cur = sessions.lookup(token)
            sessions.revoke(token)
            if cur:
                audit_db.log_event(
                    "logout", username=cur["username"], ip=_client_ip(request),
                )
        resp = RedirectResponse("/login", status_code=302)
        _clear_session_cookie(resp)
        return resp

    # GET /logout also works (some users will type the URL); same behaviour.
    @router.get("/logout")
    async def logout_get(request: Request):
        return await logout(request)

    # ---------- first-time admin bootstrap ----------

    @router.get("/setup-admin", response_class=HTMLResponse)
    async def setup_admin_page(request: Request, error: str = ""):
        # Only reachable when auth is OFF — once enabled, this page would let
        # an attacker overwrite the admin. Guard hard.
        if auth_settings.is_enabled():
            return RedirectResponse("/login", status_code=302)
        # 偵測「停用過認證後再啟用」的情境 — auth.sqlite 內已有 users 但
        # backend=off。這時不該叫使用者再建一個新 admin（會撞既有 user
        # 報「已存在使用者，無法初始化（資料庫狀態異常）」這個誤導訊息），
        # 改提供「沿用既有 admin 直接啟用」的選項。詳見 v1.4.2 修法。
        existing = auth_settings.list_existing_users()
        return templates.TemplateResponse(
            "setup_admin.html",
            {"request": request, "error": error,
             "existing_users": existing,
             "has_existing": bool(existing)},
        )

    @router.post("/setup-admin")
    async def setup_admin_submit(
        request: Request,
        username: str = Form("jtdt-admin"),
        display_name: str = Form(""),
        password: str = Form(...),
        password_confirm: str = Form(...),
    ):
        if auth_settings.is_enabled():
            return RedirectResponse("/login", status_code=302)
        ip = _client_ip(request)
        try:
            uid = auth_settings.enable_local_with_admin(
                admin_username=username,
                admin_display_name=display_name,
                admin_password=password,
                admin_password_confirm=password_confirm,
                actor_ip=ip,
            )
        except auth_settings.BootstrapError as e:
            existing = auth_settings.list_existing_users()
            return templates.TemplateResponse(
                "setup_admin.html",
                {"request": request, "error": str(e),
                 "username": (username or "")[:64],
                 "display_name": (display_name or "")[:64],
                 "existing_users": existing,
                 "has_existing": bool(existing)},
                status_code=200,
            )
        # Auto-login the new admin so they don't have to immediately re-type.
        token, expires_at = sessions.issue(
            uid, remember=False, ip=ip,
            ua=request.headers.get("User-Agent", ""),
        )
        resp = RedirectResponse("/admin/", status_code=302)
        _set_session_cookie(resp, token, remember=False,
                            request=request, expires_at=expires_at)
        return resp

    @router.post("/setup-admin/reuse-existing")
    async def setup_admin_reuse(request: Request):
        """偵測到既有 user 時的捷徑 — 直接 flip backend=local 不建新帳號。
        使用者再用既有 admin 帳號 + 密碼登入。如果忘記密碼可請系統管理員
        在主機上跑 `sudo jtdt reset-password <username>` 重設。"""
        if auth_settings.is_enabled():
            return RedirectResponse("/login", status_code=302)
        ip = _client_ip(request)
        try:
            n = auth_settings.reenable_local_with_existing(actor_ip=ip)
        except auth_settings.BootstrapError as e:
            existing = auth_settings.list_existing_users()
            return templates.TemplateResponse(
                "setup_admin.html",
                {"request": request, "error": str(e),
                 "existing_users": existing,
                 "has_existing": bool(existing)},
                status_code=200,
            )
        # 不自動登入（沒有密碼可用）— 帶到登入頁，使用者用既有 admin 登入
        return RedirectResponse(
            f"/login?msg={_qstr('已恢復本機認證，沿用 ' + str(n) + ' 個既有帳號。請用原 admin 帳號密碼登入。')}",
            status_code=303,
        )

    return router


def _safe_next(target: str) -> str:
    """Sanitise the post-login redirect target so it stays on this site.

    v1.5.4 加嚴（CodeQL py/url-redirection）：用 urllib.parse + 多層黑名單
    防 open redirect。Reject anything that's:
      - empty / non-string
      - contains :// (cross-origin redirect)
      - starts with // (protocol-relative URL —「//evil.com」會被當成 https URL)
      - starts with /\ (Windows path-separator bypass)
      - has a non-empty scheme or netloc after parsing (defence in depth)
      - contains control chars (\\r \\n \\0 → CRLF injection in Location header)
    Default to '/' on rejection.
    """
    if not isinstance(target, str) or not target:
        return "/"
    # Strip leading whitespace which some browsers/proxies allow
    s = target
    if any(c in s for c in ("\r", "\n", "\0", "\\")):
        return "/"
    if "://" in s:
        return "/"
    if s.startswith("//"):
        return "/"
    if not s.startswith("/"):
        return "/"
    # Defence in depth — parse and assert no scheme / netloc
    try:
        from urllib.parse import urlparse
        u = urlparse(s)
        if u.scheme or u.netloc:
            return "/"
    except Exception:
        return "/"
    return s
