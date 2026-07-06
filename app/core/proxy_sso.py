"""Reverse-proxy (Kerberos / SPNEGO) SSO — trusted-header login path.

An ADDITIVE login mechanism, layered on top of the existing Local / LDAP / AD
/ OIDC / SAML backends (it does NOT replace `auth_settings.backend`). When a
trusted upstream proxy (Nginx doing SPNEGO/Kerberos against AD) authenticates
the browser, it forwards the domain account in a header (default
``X-Remote-User``). We:

  1. verify the request came from a trusted proxy IP (the immediate TCP peer),
  2. normalise the account (DOMAIN\\user / user@domain / user → user),
  3. look the user up + sync via the EXISTING LDAP/AD service account
     (``auth_ldap.sync_user_by_username`` — no password bind needed),
  4. hand back a resolved user row so the caller can issue a normal session
     (respecting 2FA, RBAC, audit, workspace isolation — nothing bypassed).

Security model (see reverse_proxy_sso.md):
  - Trust is decided ONLY by the direct client IP (``request.client.host``),
    never by X-Forwarded-For (spoofable). Bind the app to 127.0.0.1 so only
    the local Nginx can reach it, and configure Nginx to ALWAYS overwrite the
    header so a client-supplied one can never pass through.
  - A header arriving from an untrusted source is ignored (never logs in) and
    audited as ``proxy_sso_untrusted_proxy``.
  - jtdt-admin (local) is never resolvable here and /login is always reachable
    → break-glass preserved.

This module is intentionally free of web/HTTP-response concerns: it returns a
(status, user_row) decision and does the audit logging; the caller
(``app/main.py:_auth_gate``) turns that into an HTTP response so it can reuse
the shared 2FA-aware ``auth_routes.complete_login`` helper.
"""
from __future__ import annotations

import ipaddress
import logging
import time
from typing import Optional

from . import audit_db, auth_settings

logger = logging.getLogger(__name__)

# Rate-limit the "header missing" audit so an anonymous bot hammering a
# protected URL (or a monitoring probe) can't flood the audit log. One event
# per client IP per window is plenty to alert an admin that the proxy isn't
# setting the header. Process-local; fine for the single-uvicorn deployment.
_missing_audit_seen: dict[str, float] = {}
_MISSING_AUDIT_TTL = 300.0  # seconds


# Resolve statuses returned by resolve_user().
OK = "ok"                    # user resolved + synced → caller issues session
MISSING = "missing"          # no header present → fall back to /login (or 401)
UNTRUSTED = "untrusted"      # header from a non-trusted source → ignore
FAIL = "fail"                # header present + trusted but lookup/sync failed


def _cfg() -> dict:
    return auth_settings.get().get("proxy_sso", {}) or {}


def is_enabled() -> bool:
    return bool(_cfg().get("enabled"))


def fallback_login_enabled() -> bool:
    # Default True — safer (a misconfig doesn't lock everyone out).
    return bool(_cfg().get("fallback_login", True))


def header_name() -> str:
    return (_cfg().get("header") or "X-Remote-User").strip() or "X-Remote-User"


def normalize_remote_user(raw: Optional[str]) -> str:
    """Normalise a proxy-supplied account to a bare username.

      DOMAIN\\username      → username
      username@domain.local → username
      username              → username

    Returns "" if the value is empty or contains control characters / spaces
    (defence against header-injection style values). The result is still
    escaped again for the LDAP filter downstream by auth_ldap.
    """
    if not raw:
        return ""
    v = raw.strip()
    # Reject anything with control chars (CR/LF header smuggling), NUL, or
    # internal whitespace — real sAMAccountName / UPN never contain these.
    if any(ord(ch) < 0x20 or ch == "\x7f" for ch in v):
        return ""
    if "\\" in v:                 # DOMAIN\username (down-level logon name)
        v = v.rsplit("\\", 1)[1]
    if "@" in v:                  # username@domain (UPN)
        v = v.split("@", 1)[0]
    v = v.strip()
    if not v or any(ch.isspace() for ch in v):
        return ""
    return v


def _client_ip(request) -> str:
    return request.client.host if getattr(request, "client", None) else ""


def _real_client_ip(request) -> str:
    """The end-user's workstation IP for AUDIT purposes, used ONLY after the
    request has passed the trusted-proxy check. A trusted proxy sets
    X-Forwarded-For to the real client, so it's safe to log here (we do NOT use
    it for the trust decision — that's always the direct peer). Falls back to
    the peer IP when no XFF is present."""
    from . import client_ip as _cip
    return _cip.real_client_ip(request)


def client_is_trusted(request) -> bool:
    """True iff the DIRECT peer IP is in the configured trusted_proxies list.

    Entries may be plain IPs ('127.0.0.1', '::1') or CIDR networks
    ('10.0.0.0/24'). We deliberately look at the real socket peer only, NOT any
    forwarded header, so a client cannot spoof its way into being 'trusted'.
    IPv4-mapped IPv6 peers (::ffff:127.0.0.1) are normalised.
    """
    peer = _client_ip(request)
    if not peer:
        return False
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    for entry in (_cfg().get("trusted_proxies") or []):
        entry = (entry or "").strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            else:
                cand = ipaddress.ip_address(entry)
                if getattr(cand, "ipv4_mapped", None):
                    cand = cand.ipv4_mapped
                if ip == cand:
                    return True
        except ValueError:
            continue
    return False


def resolve_user(request) -> tuple[str, Optional[dict]]:
    """Attempt reverse-proxy SSO for this request.

    Returns (status, user_row):
      - (OK, user_row)   header present, from a trusted proxy, user resolved
                         + synced. Caller should issue a session for user_row.
      - (MISSING, None)  no header → caller applies fallback_login policy.
      - (UNTRUSTED, None) header present but not from a trusted proxy → ignore.
      - (FAIL, None)     trusted header but lookup/sync failed → treat like
                         missing (fall back), but audited distinctly.

    All audit events are written here. Caller does NOT audit again.
    """
    cfg = _cfg()
    ip = _client_ip(request)
    hname = header_name()
    raw = request.headers.get(hname)
    path = request.scope.get("path") or ""

    if not raw:
        # Audit missing-header (helps spot a proxy that forgot to set it), but
        # rate-limited per client IP so anonymous bots can't flood the log.
        now = time.time()
        last = _missing_audit_seen.get(ip)
        if last is None or (now - last) > _MISSING_AUDIT_TTL:
            _missing_audit_seen[ip] = now
            # opportunistic GC so the dict can't grow unbounded
            if len(_missing_audit_seen) > 4096:
                cutoff = now - _MISSING_AUDIT_TTL
                for k in [k for k, v in _missing_audit_seen.items() if v < cutoff]:
                    _missing_audit_seen.pop(k, None)
            audit_db.log_event("proxy_sso_header_missing", ip=ip,
                               details={"path": path, "header": hname})
        return (MISSING, None)

    if not client_is_trusted(request):
        # Someone is sending the header from an untrusted source — either a
        # misconfig (app reachable directly) or an attacker trying to spoof an
        # identity. Never log in; audit loudly.
        audit_db.log_event(
            "proxy_sso_untrusted_proxy", ip=ip,
            details={"path": path, "client_ip": ip,
                     "claimed_user": normalize_remote_user(raw) or "(invalid)"},
        )
        return (UNTRUSTED, None)

    # Past the trust gate → the proxy is trusted, so its X-Forwarded-For names
    # the real workstation. Log THAT for audit/forensics (not the proxy's IP).
    client_ip = _real_client_ip(request)
    username = normalize_remote_user(raw)
    if not username:
        audit_db.log_event("proxy_sso_login_fail", ip=client_ip,
                           details={"reason": "empty_after_normalize"})
        return (FAIL, None)

    try:
        from . import auth_ldap
        user = auth_ldap.sync_user_by_username(username, ip=client_ip)
    except Exception as exc:  # noqa: BLE001 — audit + fall back, never 500
        from .log_safe import safe_log
        logger.warning("proxy_sso lookup failed for %s: %s",
                       safe_log(username), exc)
        audit_db.log_event(
            "proxy_sso_login_fail", username=username, ip=client_ip,
            details={"reason": f"{type(exc).__name__}: {exc}"[:200]},
        )
        return (FAIL, None)

    audit_db.log_event(
        "proxy_sso_login_success", username=username, ip=client_ip,
        details={"dn": user.get("dn") or user.get("external_dn") or "",
                 "proxy_ip": ip},
    )
    return (OK, user)
