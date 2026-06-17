"""SSO login routes (OIDC + SAML) — public, mounted before the auth gate.

These coexist with the password login: enabling SSO never disables local login.
On success a normal session is issued (same cookie + flags as password login),
the user is JIT-provisioned, and IdP groups are synced for the role matrix.

OIDC: state + nonce are carried in a short-lived signed cookie (the callback is
a top-level GET, so SameSite=Lax delivers it). SAML: the ACS is a cross-site
POST (Lax would drop a cookie), so CSRF/replay protection comes from the signed
SAML assertion validated by python3-saml; the next-url rides in RelayState.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from ..core import (audit_db, auth_settings, oidc, saml, sessions,
                    sso_provision, sso_settings)
from ..core.url_safety import safe_next
from ..logging_setup import get_logger
from .auth_routes import _client_ip, _set_session_cookie

logger = get_logger(__name__)

_SSO_TX_COOKIE = "jtdt_sso_tx"
_SSO_TX_TTL = 600  # seconds


# ---------- signed transaction cookie (OIDC state/nonce) ----------

def _sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
    key = auth_settings._ensure_secret()
    sig = hmac.new(key, raw, hashlib.sha256).digest()
    return raw.decode() + "." + base64.urlsafe_b64encode(sig).decode()


def _unsign(token: str) -> Optional[dict]:
    try:
        raw_s, sig_s = token.split(".", 1)
        raw = raw_s.encode()
        key = auth_settings._ensure_secret()
        expected = hmac.new(key, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, base64.urlsafe_b64decode(sig_s)):
            return None
        data = json.loads(base64.urlsafe_b64decode(raw))
        if time.time() - float(data.get("ts", 0)) > _SSO_TX_TTL:
            return None
        return data
    except Exception:
        return None


def _public_base(request: Request) -> str:
    """Public origin for redirect_uri / ACS — admin-configured base_url wins
    (correct behind a reverse proxy), else derive from the request."""
    b = sso_settings.base_url()
    if b:
        return b
    scheme = request.headers.get("X-Forwarded-Proto", "").lower() or request.url.scheme
    host = request.headers.get("X-Forwarded-Host") or request.url.netloc
    return f"{scheme}://{host}"


def _audit(event: str, ip: str, *, username: str = "", detail: str = "") -> None:
    try:
        audit_db.log_event(event, username=username, ip=ip, target="sso",
                           details={"info": detail[:200]} if detail else None)
    except Exception:
        pass


def _login_error(msg: str) -> RedirectResponse:
    from urllib.parse import quote
    return RedirectResponse(f"/login?error={quote(msg)}", status_code=302)


def _finish_login(request: Request, user: dict, next_url: str, *,
                  saml_session: dict | None = None) -> Response:
    ip = _client_ip(request)
    ua = request.headers.get("User-Agent", "")
    token, expires_at = sessions.issue(user["user_id"], remember=False, ip=ip, ua=ua)
    # Stash SAML NameID + SessionIndex (keyed by session token hash) so
    # SP-initiated Single-Logout can build a proper LogoutRequest later.
    if saml_session:
        from ..core import sso_store
        sso_store.save_saml_session(
            sessions._hash(token), saml_session.get("nameid", ""),
            saml_session.get("session_index", ""), expires_at)
    resp = RedirectResponse(safe_next(next_url), status_code=302)
    _set_session_cookie(resp, token, remember=False, request=request, expires_at=expires_at)
    resp.delete_cookie(_SSO_TX_COOKIE, path="/")
    _audit("login", ip, username=user.get("username", ""),
           detail=f"sso:{user.get('source')}{' (new)' if user.get('created') else ''}")
    return resp


def build_router(templates) -> APIRouter:
    router = APIRouter()

    # ---------------- OIDC ----------------
    @router.get("/auth/oidc/login")
    async def oidc_login(request: Request, next: str = "/"):
        if not sso_settings.oidc_enabled():
            return _login_error("OIDC 登入未啟用")
        cfg = sso_settings.get_oidc(reveal=True)
        state, nonce = oidc.new_state(), oidc.new_nonce()
        redirect_uri = _public_base(request) + "/auth/oidc/callback"
        try:
            url = oidc.build_auth_url(cfg, state=state, nonce=nonce,
                                      redirect_uri=redirect_uri)
        except oidc.OIDCError as e:
            _audit("login_fail", _client_ip(request), detail=f"oidc build: {e}")
            return _login_error(str(e))
        resp = RedirectResponse(url, status_code=302)
        tx = _sign({"state": state, "nonce": nonce, "next": safe_next(next),
                    "ts": time.time()})
        resp.set_cookie(_SSO_TX_COOKIE, tx, max_age=_SSO_TX_TTL, httponly=True,
                        secure=redirect_uri.startswith("https"), samesite="lax", path="/")
        return resp

    @router.get("/auth/oidc/callback")
    async def oidc_callback(request: Request, code: str = "", state: str = "",
                            error: str = ""):
        ip = _client_ip(request)
        if not sso_settings.oidc_enabled():
            return _login_error("OIDC 登入未啟用")
        if error:
            _audit("login_fail", ip, detail=f"oidc idp error: {error}")
            return _login_error(f"OIDC 供應商回傳錯誤：{error[:80]}")
        tx = _unsign(request.cookies.get(_SSO_TX_COOKIE, ""))
        if not tx or not state or not hmac.compare_digest(str(tx.get("state", "")), state):
            _audit("login_fail", ip, detail="oidc state mismatch")
            return _login_error("OIDC state 驗證失敗（請重新登入）")
        if not code:
            return _login_error("OIDC 回呼缺 code")
        cfg = sso_settings.get_oidc(reveal=True)
        redirect_uri = _public_base(request) + "/auth/oidc/callback"
        try:
            tok = oidc.exchange_code(cfg, code=code, redirect_uri=redirect_uri)
            claims = oidc.verify_id_token(cfg, tok["id_token"], nonce=tx.get("nonce"))
            ident = oidc.map_claims(cfg, claims)
            user = sso_provision.provision(
                "oidc", external_id=ident["sub"], username=ident["username"],
                display_name=ident["name"], groups=ident["groups"],
                admin_group=cfg.get("admin_group", ""))
        except (oidc.OIDCError, sso_provision.SSOProvisionError) as e:
            _audit("login_fail", ip, detail=f"oidc: {e}")
            return _login_error(str(e))
        return _finish_login(request, user, tx.get("next", "/"))

    # ---------------- SAML ----------------
    @router.get("/auth/saml/login")
    async def saml_login(request: Request, next: str = "/"):
        if not sso_settings.saml_enabled():
            return _login_error("SAML 登入未啟用")
        cfg = sso_settings.get_saml(reveal=True)
        try:
            url = saml.build_auth_url(request, cfg, _public_base(request),
                                      relay_state=safe_next(next))
        except saml.SAMLError as e:
            _audit("login_fail", _client_ip(request), detail=f"saml build: {e}")
            return _login_error(str(e))
        return RedirectResponse(url, status_code=302)

    @router.post("/auth/saml/acs")
    async def saml_acs(request: Request):
        ip = _client_ip(request)
        if not sso_settings.saml_enabled():
            return _login_error("SAML 登入未啟用")
        form = await request.form()
        post_data = {k: str(v) for k, v in form.items()}
        cfg = sso_settings.get_saml(reveal=True)
        try:
            ident = saml.process_acs(request, cfg, _public_base(request), post_data)
            user = sso_provision.provision(
                "saml", external_id=ident["nameid"], username=ident["username"],
                display_name=ident["name"], groups=ident["groups"],
                admin_group=cfg.get("admin_group", ""))
        except (saml.SAMLError, sso_provision.SSOProvisionError) as e:
            _audit("login_fail", ip, detail=f"saml: {e}")
            return _login_error(str(e))
        return _finish_login(request, user, ident.get("relay_state", "/"),
                             saml_session={"nameid": ident.get("nameid", ""),
                                           "session_index": ident.get("session_index", "")})

    @router.api_route("/auth/saml/sls", methods=["GET", "POST"])
    async def saml_sls(request: Request):
        """IdP Single-Logout endpoint: validate the SLO message, tear down our
        local session, then redirect to where the IdP asked (or /login)."""
        if not sso_settings.saml_enabled():
            return _login_error("SAML 登入未啟用")
        cfg = sso_settings.get_saml(reveal=True)
        try:
            redirect_to = saml.process_sls(request, cfg, _public_base(request),
                                           dict(request.query_params))
        except saml.SAMLError:
            redirect_to = "/login"
        # Revoke our own session regardless of the SLO outcome.
        token = request.cookies.get(sessions.COOKIE_NAME, "")
        if token:
            try:
                sessions.revoke(token)
            except Exception:
                pass
        resp = RedirectResponse(safe_next(redirect_to) or "/login", status_code=302)
        resp.delete_cookie(sessions.COOKIE_NAME, path="/")
        return resp

    @router.get("/auth/saml/metadata")
    async def saml_metadata(request: Request):
        if not sso_settings.saml_enabled():
            return Response("SAML 未啟用", status_code=404)
        cfg = sso_settings.get_saml(reveal=True)
        try:
            xml = saml.sp_metadata(cfg, _public_base(request))
        except saml.SAMLError as e:
            # Log the detail; return a generic message (no exception text to the
            # client — CodeQL #114 information exposure).
            logger.warning("SAML SP metadata generation failed: %s", e)
            return Response("SP metadata 產生失敗，請檢查 SAML 設定", status_code=400)
        return Response(xml, media_type="application/xml")

    return router
