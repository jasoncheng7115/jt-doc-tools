"""CSRF 防護 — double-submit token（defense-in-depth，疊在 SameSite=Lax 之上）。

每個瀏覽器一個隨機 token：放在「JS 可讀」的 cookie（jtdt_csrf），同時以隱藏
欄位（表單）/ X-CSRF-Token 標頭（AJAX）回送。伺服器對不安全方法（POST/PUT/
PATCH/DELETE）比對「提交的 token」與「cookie 的 token」是否一致。跨站攻擊者
讀不到 token（SameSite + 同源）→ 無法偽造請求。

驗證細節（pure-ASGI，避免 BaseHTTPMiddleware 讀 body 破壞下游）：
  - 帶 X-CSRF-Token 標頭（AJAX / fetch）→ 直接比對，不讀 body。
  - 無標頭但 Content-Type 是 application/x-www-form-urlencoded（原生小表單）
    → 緩衝該小 body 解出 csrf_token 欄位後再 replay 給下游。
  - 其餘不安全方法（multipart / JSON 無標頭）→ 拒（這些一律走 AJAX 帶標頭）。

豁免：
  - Authorization: Bearer（API token 客戶端，非 cookie 驗證、無 CSRF 風險）。
  - SSO 跨站回呼（SAML ACS / SLS 由 IdP 以 POST 導回，另有簽章 + replay 防護）。
"""
from __future__ import annotations

import hmac
import os
import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

COOKIE_NAME = "jtdt_csrf"
HEADER_NAME = b"x-csrf-token"
FIELD_NAME = "csrf_token"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
# 跨站回呼（IdP → 我方），本就跨站、改用簽章 / replay 防護 → CSRF 豁免
_EXEMPT_PREFIXES = ("/auth/saml/acs", "/auth/saml/sls")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _valid(cookie_tok, submitted) -> bool:
    if not cookie_tok or not submitted:
        return False
    return hmac.compare_digest(str(cookie_tok), str(submitted))


def _header(scope, name: bytes) -> bytes:
    for k, v in scope.get("headers", []):
        if k == name:
            return v
    return b""


def _cookie_token(scope) -> str | None:
    raw = _header(scope, b"cookie")
    if not raw:
        return None
    try:
        c = SimpleCookie()
        c.load(raw.decode("latin-1"))
    except Exception:
        return None
    m = c.get(COOKIE_NAME)
    return m.value if m else None


def _is_exempt(scope) -> bool:
    path = scope.get("path", "")
    if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
        return True
    if _header(scope, b"authorization").startswith(b"Bearer "):
        return True
    return False


def _form_token(body: bytes) -> str | None:
    try:
        q = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except Exception:
        return None
    v = q.get(FIELD_NAME)
    return v[0] if v else None


async def _buffer_body(receive):
    """讀完整個 request body（只用於 urlencoded 小表單）並保留原始訊息供 replay。"""
    messages, body = [], b""
    while True:
        msg = await receive()
        messages.append(msg)
        if msg["type"] != "http.request":
            break
        body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break
    return body, messages


async def _reject(send):
    body = b'{"detail":"CSRF token \\u907a\\u5931\\u6216\\u4e0d\\u6b63\\u78ba"}'
    await send({
        "type": "http.response.start", "status": 403,
        "headers": [(b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


class CSRFMiddleware:
    """Pure-ASGI：驗證不安全方法的 CSRF token、把 token 放進 request.state、
    並於回應補設 jtdt_csrf cookie（若 request 尚未帶）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        cookie_tok = _cookie_token(scope)
        token = cookie_tok or new_token()
        # 曝露給模板（login / setup-admin 等原生表單以隱藏欄位 render）
        st = scope.setdefault("state", {})
        st["csrf_token"] = token
        # CSP nonce（pure-ASGI 設 scope state → 必傳到 handler / 模板）；
        # 每請求一個，模板 inline <script> 用 {{ request.state.csp_nonce }}，
        # _security_headers 讀同值放進 CSP script-src 'nonce-...'。
        st["csp_nonce"] = secrets.token_urlsafe(16)

        method = scope.get("method", "GET")
        replay_messages = None
        # JTDT_CSRF_DISABLE=1 只在 pytest（conftest 設）跳過驗證；仍設 cookie +
        # request.state.csrf_token 讓模板 / fetch 照常。正式部署永不設此旗標。
        _disabled = os.environ.get("JTDT_CSRF_DISABLE") == "1"
        if not _disabled and method not in _SAFE_METHODS and not _is_exempt(scope):
            hdr = _header(scope, HEADER_NAME).decode("latin-1") or None
            if hdr is not None:
                if not _valid(cookie_tok, hdr):
                    return await _reject(send)
            else:
                ctype = _header(scope, b"content-type").decode("latin-1").lower()
                if ctype.startswith("application/x-www-form-urlencoded"):
                    body, replay_messages = await _buffer_body(receive)
                    if not _valid(cookie_tok, _form_token(body)):
                        return await _reject(send)
                else:
                    # 不安全方法、非 bearer/SSO、又沒帶標頭（multipart / JSON）→ 拒
                    return await _reject(send)

        set_cookie = cookie_tok is None
        # 反向代理（nginx 終結 TLS）後 scope scheme 是 http；與 app 其他 cookie /
        # HSTS 一致，也認 X-Forwarded-Proto 判斷 https → 在 https 站台帶 Secure。
        xfp = _header(scope, b"x-forwarded-proto").decode("latin-1").split(",")[0].strip().lower()
        is_https = scope.get("scheme") == "https" or xfp == "https"

        async def _send(message):
            if set_cookie and message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                cookie = (f"{COOKIE_NAME}={token}; Path=/; SameSite=Lax"
                          + ("; Secure" if is_https else ""))
                headers.append((b"set-cookie", cookie.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        if replay_messages is not None:
            _it = iter(replay_messages)

            async def _replay():
                try:
                    return next(_it)
                except StopIteration:
                    return {"type": "http.request", "body": b"", "more_body": False}

            return await self.app(scope, _replay, _send)
        return await self.app(scope, receive, _send)
