"""OIDC (OpenID Connect) Authorization-Code flow.

Dependency-light, mirroring the jt-ipam approach: httpx for discovery + token
exchange, PyJWT (+ cryptography, already a dep) for JWKS id_token verification.
No authlib.

The endpoints we fetch (discovery / token / JWKS) come from an
**admin-configured** issuer, so internal IdPs (e.g. on-prem Keycloak on a
private IP) are allowed; we only require https and block cloud metadata
endpoints as defence in depth. State + nonce are generated here but stored by
the route layer (signed short-lived cookie) and passed back in for verification.
"""
from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from ..logging_setup import get_logger

logger = get_logger(__name__)


class OIDCError(Exception):
    pass


_BLOCKED_HOSTS = {
    "169.254.169.254", "100.100.100.200",
    "metadata", "metadata.google.internal",
}
_HTTP_TIMEOUT = 10.0

# discovery cache: issuer -> (expires_ts, doc)
_DISCO_CACHE: dict[str, tuple[float, dict]] = {}
_DISCO_TTL = 3600.0


def _check_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("https", "http"):
        raise OIDCError(f"OIDC URL 必須是 http/https：{url[:80]}")
    host = (p.hostname or "").lower()
    if not host or host in _BLOCKED_HOSTS:
        raise OIDCError("OIDC URL 指向被封鎖的位址")
    return url


def new_state() -> str:
    return secrets.token_urlsafe(24)


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def discover(cfg: dict[str, Any]) -> dict[str, Any]:
    """Fetch + cache the issuer's .well-known/openid-configuration."""
    issuer = (cfg.get("issuer") or "").rstrip("/")
    if not issuer:
        raise OIDCError("OIDC issuer 未設定")
    now = time.time()
    cached = _DISCO_CACHE.get(issuer)
    if cached and cached[0] > now:
        return cached[1]
    url = _check_url(f"{issuer}/.well-known/openid-configuration")
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=False) as cli:
            r = cli.get(url)
    except httpx.RequestError as e:
        raise OIDCError(f"OIDC discovery 連線失敗：{e.__class__.__name__}") from e
    if r.status_code != 200:
        raise OIDCError(f"OIDC discovery HTTP {r.status_code}")
    try:
        doc = r.json()
    except Exception as e:
        raise OIDCError("OIDC discovery 回傳非 JSON") from e
    for k in ("authorization_endpoint", "token_endpoint", "issuer", "jwks_uri"):
        if not doc.get(k):
            raise OIDCError(f"OIDC discovery 缺欄位 {k}")
    # Spec: discovered issuer must match the configured issuer exactly.
    if doc["issuer"].rstrip("/") != issuer:
        raise OIDCError("OIDC discovery issuer 與設定不符")
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        _check_url(doc[k])
    _DISCO_CACHE[issuer] = (now + _DISCO_TTL, doc)
    return doc


def build_auth_url(cfg: dict[str, Any], *, state: str, nonce: str,
                   redirect_uri: str) -> str:
    doc = discover(cfg)
    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri,
        "scope": (cfg.get("scopes") or "openid email profile"),
        "state": state,
        "nonce": nonce,
    }
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return doc["authorization_endpoint"] + sep + urlencode(params)


def exchange_code(cfg: dict[str, Any], *, code: str, redirect_uri: str) -> dict[str, Any]:
    doc = discover(cfg)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": cfg["client_id"],
        "client_secret": cfg.get("client_secret") or "",
    }
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=False) as cli:
            r = cli.post(doc["token_endpoint"], data=data,
                         headers={"Accept": "application/json"})
    except httpx.RequestError as e:
        raise OIDCError(f"OIDC token 交換連線失敗：{e.__class__.__name__}") from e
    if r.status_code != 200:
        logger.warning("OIDC token endpoint HTTP %s: %s", r.status_code, r.text[:200])
        raise OIDCError(f"OIDC token 交換失敗 (HTTP {r.status_code})")
    body = r.json()
    if not body.get("id_token"):
        raise OIDCError("OIDC token 回應缺 id_token")
    return body


_ALLOWED_ALGS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512",
                 "PS256", "PS384", "PS512"]


def verify_id_token(cfg: dict[str, Any], id_token: str, *, nonce: str | None) -> dict[str, Any]:
    import jwt
    doc = discover(cfg)
    try:
        jwk_client = jwt.PyJWKClient(doc["jwks_uri"])
        signing_key = jwk_client.get_signing_key_from_jwt(id_token)
        # Pin to asymmetric algorithms — NEVER trust the token's own `alg`
        # header, which would let an attacker downgrade to HS256 and sign with
        # the (public) JWKS key (alg-confusion attack).
        claims = jwt.decode(
            id_token, signing_key.key, algorithms=_ALLOWED_ALGS,
            audience=cfg["client_id"], issuer=doc["issuer"],
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise OIDCError(f"id_token 驗證失敗：{e}") from e
    except Exception as e:
        raise OIDCError(f"id_token 驗證錯誤：{e.__class__.__name__}") from e
    if nonce is not None and claims.get("nonce") != nonce:
        raise OIDCError("id_token nonce 不符（可能為重放攻擊）")
    return claims


def map_claims(cfg: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    """Pull username / email / name / groups out of the verified claims."""
    sub = str(claims.get("sub") or "")
    email = str(claims.get(cfg.get("email_claim") or "email") or "")
    username = str(claims.get(cfg.get("username_claim") or "preferred_username") or "") \
        or email or sub
    name = str(claims.get(cfg.get("name_claim") or "name") or "") or username
    raw_groups = claims.get(cfg.get("groups_claim") or "groups") or []
    if isinstance(raw_groups, str):
        raw_groups = [g.strip() for g in raw_groups.split(",") if g.strip()]
    groups = [str(g) for g in raw_groups] if isinstance(raw_groups, (list, tuple)) else []
    return {"sub": sub, "username": username, "email": email,
            "name": name, "groups": groups}
