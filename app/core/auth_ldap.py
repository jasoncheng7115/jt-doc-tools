"""LDAP / AD authentication backend.

Flow:
1. Connect to the configured LDAP server using the service account
   (validating its cert when use_tls + verify_cert).
2. Search for the user by username (filter from settings).
3. Re-bind as the discovered user DN with the supplied password.
   Successful bind = correct password.
4. Sync into our local `users` / `groups` tables: insert user row if new,
   refresh groups + OU subjects.

For permissions: the user-level subject is the local users.id; group
subjects are local groups.id rows that mirror AD groups; OU subjects
are the OU DNs themselves (subject_key = the DN string).

Security:
- Service password is NEVER logged.
- LDAP filters are escaped via ldap3.utils.conv.escape_filter_chars.
- Default ldaps:// + verify_cert. Plain ldap:// only when admin opts in.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from . import audit_db, auth_db, auth_settings, db, group_manager, permissions

logger = logging.getLogger(__name__)


class AuthError(Exception):
    pass


def _build_server(cfg: dict):
    """Build a ldap3 Server from cfg dict. Raises AuthError on bad config /
    missing ldap3."""
    try:
        from ldap3 import Server, Tls, ALL
        import ssl as _ssl
    except ImportError:
        raise AuthError("ldap3 套件未安裝；請聯絡管理員")
    server_url = (cfg.get("server_url") or "").strip()
    if not server_url:
        raise AuthError("伺服器 URL 未設定")
    use_tls = bool(cfg.get("use_tls", True))
    verify = bool(cfg.get("verify_cert", True))
    tls = None
    if use_tls or server_url.lower().startswith("ldaps://"):
        tls = Tls(validate=_ssl.CERT_REQUIRED if verify else _ssl.CERT_NONE,
                  version=_ssl.PROTOCOL_TLS_CLIENT)
    return Server(server_url, get_info=ALL, tls=tls)


def test_connection(cfg: dict) -> dict:
    """Try the service-account bind only. Used by the admin UI to verify
    server URL / TLS / service credentials without requiring a real user.

    Returns {"ok": True, "elapsed_ms": int, "info": "..."} on success.
    Raises AuthError with a Chinese message on failure."""
    try:
        from ldap3 import Connection
    except ImportError:
        raise AuthError("ldap3 套件未安裝")
    svc_dn = (cfg.get("service_dn") or "").strip()
    svc_pw = cfg.get("service_password") or ""
    if not svc_dn:
        raise AuthError("Service Account DN 未設定")
    if not svc_pw:
        raise AuthError("Service Account 密碼未提供（首次測試請填入；已儲存的密碼請於表單再輸入一次以測試）")
    server = _build_server(cfg)
    t0 = time.time()
    try:
        with Connection(server, user=svc_dn, password=svc_pw,
                        auto_bind=True, raise_exceptions=True) as conn:
            who = ""
            try:
                who = conn.extend.standard.who_am_i() or ""
            except Exception:
                pass
        elapsed = int((time.time() - t0) * 1000)
        info_obj = getattr(server, "info", None)
        vendor = ""
        if info_obj is not None:
            try:
                vendor = (info_obj.vendor_name or [""])[0] if info_obj.vendor_name else ""
            except Exception:
                vendor = ""
        return {"ok": True, "elapsed_ms": elapsed, "who": str(who),
                "vendor": str(vendor or "")}
    except Exception as exc:
        raise AuthError(f"連線失敗：{type(exc).__name__}: {exc}")


def test_user_login(cfg: dict, username: str, password: str) -> dict:
    """Run the full bind→search→user-bind cycle with a real account, but
    DO NOT touch the local users table or write audit events. Used by the
    admin UI to verify that an end-user can authenticate.

    Returns {"ok": True, "user_dn": "...", "display_name": "...",
             "groups": [...], "elapsed_ms": int}.
    Raises AuthError with a Chinese message on failure."""
    try:
        from ldap3 import Connection, SUBTREE
        from ldap3.utils.conv import escape_filter_chars
    except ImportError:
        raise AuthError("ldap3 套件未安裝")
    if not username:
        raise AuthError("請輸入測試帳號")
    if not password:
        raise AuthError("請輸入測試密碼")

    svc_dn = (cfg.get("service_dn") or "").strip()
    svc_pw = cfg.get("service_password") or ""
    base = (cfg.get("user_search_base") or "").strip()
    user_filter_tpl = (cfg.get("user_search_filter") or "(sAMAccountName={username})")
    if not svc_dn or not svc_pw or not base:
        raise AuthError("Service Account / 搜尋 base DN / Service 密碼 都需先填妥")
    # Catch the common mistake of putting a filter expression in the base DN
    # field — LDAP returns an opaque "character '(' not allowed in attribute
    # type" otherwise. Flag it with a Chinese hint pointing at the right field.
    if "(" in base or ")" in base:
        raise AuthError(
            "「使用者搜尋 base DN」不能包含 ( 或 )；那是 filter 語法。"
            "base DN 應該是純 DN（例：dc=example,dc=com），"
            "群組限制請寫到下方的「使用者搜尋 filter」"
        )
    server = _build_server(cfg)

    safe_username = escape_filter_chars(username.strip())
    user_filter = user_filter_tpl.replace("{username}", safe_username)

    t0 = time.time()
    # Step 1+2: service bind + search.
    try:
        with Connection(server, user=svc_dn, password=svc_pw,
                        auto_bind=True, raise_exceptions=True) as svc_conn:
            attrs = [
                cfg.get("displayname_attr", "displayName"),
                cfg.get("group_attr", "memberOf"),
                cfg.get("username_attr", "sAMAccountName"),
            ]
            svc_conn.search(search_base=base, search_filter=user_filter,
                            search_scope=SUBTREE, attributes=attrs,
                            size_limit=2)
            entries = list(svc_conn.entries)
    except Exception as exc:
        raise AuthError(f"Service 連線/搜尋失敗：{type(exc).__name__}: {exc}")
    if not entries:
        raise AuthError(f"找不到使用者「{username}」（搜尋 base 或 filter 可能不對）")
    if len(entries) > 1:
        raise AuthError("搜尋到多筆同名使用者，請收緊 filter 或 base DN")

    entry = entries[0]
    user_dn = str(entry.entry_dn)
    dn_attr = cfg.get("displayname_attr", "displayName")
    grp_attr = cfg.get("group_attr", "memberOf")
    display_name = (str(entry[dn_attr]) if dn_attr in entry else username)
    groups_raw = entry[grp_attr] if (grp_attr in entry) else []
    group_dns = [str(g) for g in groups_raw] if groups_raw else []

    # Step 3: user bind.
    try:
        with Connection(server, user=user_dn, password=password,
                        auto_bind=True, raise_exceptions=True):
            pass
    except Exception:
        raise AuthError("帳號或密碼錯誤（service search 找到使用者，但密碼 bind 失敗）")
    elapsed = int((time.time() - t0) * 1000)
    return {"ok": True, "user_dn": user_dn, "display_name": display_name,
            "groups": group_dns, "elapsed_ms": elapsed}


def authenticate(username: str, password: str, *, ip: str = "") -> dict:
    """Verify creds against AD/LDAP, sync the user, return user dict."""
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        from ldap3.utils.conv import escape_filter_chars
        import ssl as _ssl
    except ImportError:
        raise AuthError("ldap3 套件未安裝；請聯絡管理員")

    s = auth_settings.get()
    cfg = s.get("ldap", {})
    server_url = cfg.get("server_url", "")
    if not server_url:
        raise AuthError("LDAP 伺服器尚未設定")

    use_tls = bool(cfg.get("use_tls", True))
    verify = bool(cfg.get("verify_cert", True))
    tls = None
    if use_tls or server_url.lower().startswith("ldaps://"):
        tls = Tls(validate=_ssl.CERT_REQUIRED if verify else _ssl.CERT_NONE,
                  version=_ssl.PROTOCOL_TLS_CLIENT)

    server = Server(server_url, get_info=ALL, tls=tls)

    # Step 1+2: bind as service, search for the user.
    svc_dn = cfg.get("service_dn", "")
    svc_pw = cfg.get("service_password", "")
    base = cfg.get("user_search_base", "")
    user_filter_tpl = cfg.get("user_search_filter", "(sAMAccountName={username})")
    if not svc_dn or not svc_pw or not base:
        raise AuthError("LDAP service account / search base 尚未設定")

    safe_username = escape_filter_chars((username or "").strip())
    user_filter = user_filter_tpl.replace("{username}", safe_username)

    try:
        with Connection(server, user=svc_dn, password=svc_pw,
                        auto_bind=True, raise_exceptions=True) as svc_conn:
            svc_conn.search(
                search_base=base,
                search_filter=user_filter,
                search_scope=SUBTREE,
                attributes=[
                    cfg.get("displayname_attr", "displayName"),
                    cfg.get("group_attr", "memberOf"),
                    cfg.get("username_attr", "sAMAccountName"),
                ],
                size_limit=2,
            )
            entries = list(svc_conn.entries)
    except Exception as exc:
        logger.warning("LDAP service bind/search failed: %s", exc)
        # Surface the real error class + message so admins can diagnose
        # (wrong port, bad service password, TLS issue, …) instead of the
        # opaque "無法連線到 LDAP 伺服器". Service password is never in the
        # exception text itself, so this doesn't leak secrets.
        raise AuthError(f"無法連線/查詢 LDAP：{type(exc).__name__}: {exc}")

    if not entries:
        # Same error as wrong password (no enumeration).
        audit_db.log_event("login_fail", username=username, ip=ip,
                           details={"reason": "ldap_user_not_found"})
        raise AuthError("帳號或密碼錯誤")
    if len(entries) > 1:
        logger.warning("LDAP returned multiple users for %r — refusing", username)
        raise AuthError("LDAP 設定錯誤：搜尋到多筆同名使用者")

    entry = entries[0]
    user_dn = str(entry.entry_dn)
    display_name = (str(entry[cfg.get("displayname_attr", "displayName")])
                    if cfg.get("displayname_attr", "displayName") in entry else username)
    groups_raw = entry[cfg.get("group_attr", "memberOf")] if (
        cfg.get("group_attr", "memberOf") in entry) else []
    group_dns = [str(g) for g in groups_raw] if groups_raw else []

    # Step 3: try to bind as the discovered user → password check.
    try:
        with Connection(server, user=user_dn, password=password,
                        auto_bind=True, raise_exceptions=True) as user_conn:
            pass
    except Exception:
        audit_db.log_event("login_fail", username=username, ip=ip,
                           details={"reason": "ldap_bind_failed"})
        raise AuthError("帳號或密碼錯誤")

    # Step 4: sync into local users / groups tables.
    user_row = _sync_user(username, display_name, user_dn,
                          backend=s.get("backend", "ldap"))
    _sync_groups(user_row["user_id"], group_dns, backend=s.get("backend", "ldap"))
    _sync_ous(user_row["user_id"], user_dn)

    audit_db.log_event("login_success", username=username, ip=ip,
                       details={"source": s.get("backend"), "dn": user_dn})
    return user_row


def _sync_user(username: str, display_name: str, dn: str, backend: str) -> dict:
    conn = auth_db.conn()
    # Already-synced LDAP user → just refresh display_name + last_login.
    row = conn.execute(
        "SELECT id, username FROM users WHERE source IN ('ldap','ad') "
        "AND external_dn=?", (dn,)
    ).fetchone()
    now = time.time()
    if row:
        with db.tx(conn):
            conn.execute(
                "UPDATE users SET display_name=?, last_login_at=?, enabled=1 "
                "WHERE id=?", (display_name, now, row["id"]),
            )
        return {"user_id": row["id"], "username": username,
                "display_name": display_name, "source": backend}

    # First-time login for this LDAP DN. PVE-style: same username can exist
    # in different realms (local vs ldap) — UNIQUE(username, source) lets
    # them coexist. Still refuse when a *different* LDAP DN already claimed
    # the same username in this same backend, to avoid silent identity
    # takeover (login as `jason` from one OU vs another).
    clash = conn.execute(
        "SELECT external_dn FROM users WHERE username=? AND source=?",
        (username, backend),
    ).fetchone()
    if clash:
        raise AuthError(
            f"已有另一個 {backend.upper()} DN 使用此帳號名「{username}」"
            f"（DN: {clash['external_dn']}）。請聯絡管理員處理同名衝突。"
        )

    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO users(username, display_name, source, external_dn, "
            "enabled, is_admin_seed, created_at, last_login_at) "
            "VALUES (?, ?, ?, ?, 1, 0, ?, ?)",
            (username, display_name, backend, dn, now, now),
        )
        uid = cur.lastrowid
    # New users get default-user role per spec
    permissions.set_subject_roles("user", str(uid), ["default-user"])
    return {"user_id": uid, "username": username,
            "display_name": display_name, "source": backend}


def _sync_groups(user_id: int, group_dns: list[str], backend: str) -> None:
    """Make sure each AD group has a row in our `groups` table, then set
    the user's local membership accordingly. We treat the AD group's DN
    as the unique key; group `name` is the CN portion for display."""
    conn = auth_db.conn()
    group_ids: list[int] = []
    with db.tx(conn):
        # Clean existing memberships for this user (we'll rebuild).
        conn.execute("DELETE FROM group_members WHERE user_id=?", (user_id,))
        for dn in group_dns:
            cn = _cn_from_dn(dn) or dn
            row = conn.execute(
                "SELECT id FROM groups WHERE source=? AND external_dn=?",
                (backend, dn)
            ).fetchone()
            if row:
                gid = row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO groups(name, source, external_dn, created_at) "
                    "VALUES (?, ?, ?, ?)", (cn, backend, dn, time.time()),
                )
                gid = cur.lastrowid
            conn.execute(
                "INSERT OR IGNORE INTO group_members(group_id, user_id) "
                "VALUES (?,?)", (gid, user_id),
            )
            group_ids.append(gid)
    # Cache invalidation handled by permissions.set_subject_roles indirectly;
    # explicit invalidate here to be safe.
    permissions.invalidate_cache()


# Per-user OU subjects are derived per-request from the user's DN at login;
# we don't persist them in a table for now (would need a per-user OU mapping
# table). Instead, the permission resolver in permissions.py will (in v1.1.x)
# look them up on demand. For now we just record the user's DN; resolver
# treats the DN's parent OUs as additional subjects.
def _sync_ous(user_id: int, dn: str) -> None:
    # No-op for v1.1.0; OU resolution is per-request based on users.external_dn.
    # Hook left here to make future enhancement obvious.
    return


def _cn_from_dn(dn: str) -> Optional[str]:
    """Extract CN= portion from an LDAP DN. Returns None if not parseable."""
    try:
        for part in dn.split(","):
            part = part.strip()
            if part.upper().startswith("CN="):
                return part[3:]
    except Exception:
        pass
    return None


def get_ou_subjects_for_dn(dn: str) -> list[tuple[str, str]]:
    """Return all OU=… ancestor DNs as ('ou', dn) subjects.

    For dn='CN=Alice,OU=Sales,OU=TW,DC=example,DC=com' returns:
        [('ou', 'OU=Sales,OU=TW,DC=example,DC=com'),
         ('ou', 'OU=TW,DC=example,DC=com')]
    """
    if not dn:
        return []
    parts = [p.strip() for p in dn.split(",")]
    out: list[tuple[str, str]] = []
    for i, p in enumerate(parts):
        if p.upper().startswith("OU="):
            ou_dn = ",".join(parts[i:])
            out.append(("ou", ou_dn))
    return out
