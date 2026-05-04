"""HTTP routes for login / logout / first-time admin bootstrap.

Mounted at the app root (no prefix). All endpoints listed here are PUBLIC
— the auth middleware lets them through unauthenticated.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote as _qstr

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

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
        token, expires_at = sessions.issue(
            user["user_id"], remember=bool(remember), ip=ip, ua=ua,
        )
        resp = RedirectResponse(_safe_next(next), status_code=302)
        _set_session_cookie(resp, token, remember=bool(remember),
                            request=request, expires_at=expires_at)
        return resp

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

    Reject anything that's:
      - empty
      - contains :// (would let attacker redirect cross-origin)
      - starts with // (protocol-relative)
      - starts with / followed by another / (also protocol-relative)
    Default to '/' on rejection.
    """
    if not target:
        return "/"
    if "://" in target:
        return "/"
    if target.startswith("//"):
        return "/"
    if not target.startswith("/"):
        return "/"
    return target
