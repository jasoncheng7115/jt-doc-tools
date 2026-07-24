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


def _safe_search_base(dn: str) -> str:
    """Validate + normalise a user-supplied DN before using it as an LDAP
    ``search_base``. ``parse_dn`` rejects malformed / injection-shaped input
    (raises on anything that isn't a well-formed sequence of RDNs) and
    ``safe_dn`` returns the escaped canonical form — so caller-controlled DNs
    (directory-browser node / user selection) can't smuggle extra filter or
    control characters into the search request."""
    from ldap3.utils.dn import parse_dn, safe_dn
    dn = (dn or "").strip()
    if not dn:
        raise AuthError("缺少 DN")
    try:
        parse_dn(dn)  # raises LDAPInvalidDnError on malformed / unsafe input
        return safe_dn(dn)
    except AuthError:
        raise
    except Exception:  # noqa: BLE001 — any parse failure → reject
        raise AuthError("DN 格式不正確")


class UserNotFoundError(AuthError):
    """Raised when the service-account search finds no (or several) matching
    user. Separated so the password path can map it to the generic
    "帳號或密碼錯誤" (no user enumeration) while reverse-proxy SSO can audit it
    as its own proxy_sso_login_fail without leaking it as a bad password."""
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
                        auto_bind=True, raise_exceptions=True, check_names=False) as conn:
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
                        auto_bind=True, raise_exceptions=True, check_names=False) as svc_conn:
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
                        auto_bind=True, raise_exceptions=True, check_names=False):
            pass
    except Exception:
        raise AuthError("帳號或密碼錯誤（service search 找到使用者，但密碼 bind 失敗）")
    elapsed = int((time.time() - t0) * 1000)
    return {"ok": True, "user_dn": user_dn, "display_name": display_name,
            "groups": group_dns, "elapsed_ms": elapsed}


def _resolve_backend_and_cfg() -> tuple[str, dict]:
    """Return (backend, ldap_cfg) for directory sync.

    `backend` becomes the synced user's `source` column. Normally it mirrors
    the primary `auth_settings.backend` ('ldap' / 'ad'). Reverse-proxy SSO may
    run layered on a non-directory primary backend (e.g. local + proxy); in
    that case directory users are still AD-sourced, so we default to 'ad'."""
    s = auth_settings.get()
    backend = s.get("backend", "ldap")
    if backend not in ("ldap", "ad"):
        backend = "ad"
    return backend, s.get("ldap", {})


def _search_ldap_user(username: str, cfg: dict) -> dict:
    """Service-bind + search for a single user. Returns
    {user_dn, display_name, group_dns}. Does NOT bind as the user (no password
    check) and does NOT write audit — that's the caller's job.

    Shared by the password `authenticate()` path and the reverse-proxy
    `sync_user_by_username()` path (the client asked us not to duplicate the
    LDAP search/sync logic). Raises:
      - UserNotFoundError  when 0 (or >1) users match
      - AuthError          on connection / config problems
    """
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        from ldap3.utils.conv import escape_filter_chars
        import ssl as _ssl
    except ImportError:
        raise AuthError("ldap3 套件未安裝；請聯絡管理員")

    server_url = cfg.get("server_url", "")
    if not server_url:
        raise AuthError("LDAP 伺服器尚未設定")
    svc_dn = cfg.get("service_dn", "")
    svc_pw = cfg.get("service_password", "")
    base = cfg.get("user_search_base", "")
    if not svc_dn or not svc_pw or not base:
        raise AuthError("LDAP service account / search base 尚未設定")

    use_tls = bool(cfg.get("use_tls", True))
    verify = bool(cfg.get("verify_cert", True))
    tls = None
    if use_tls or server_url.lower().startswith("ldaps://"):
        tls = Tls(validate=_ssl.CERT_REQUIRED if verify else _ssl.CERT_NONE,
                  version=_ssl.PROTOCOL_TLS_CLIENT)
    server = Server(server_url, get_info=ALL, tls=tls)

    user_filter_tpl = cfg.get("user_search_filter", "(sAMAccountName={username})")
    safe_username = escape_filter_chars((username or "").strip())
    user_filter = user_filter_tpl.replace("{username}", safe_username)

    try:
        with Connection(server, user=svc_dn, password=svc_pw,
                        auto_bind=True, raise_exceptions=True, check_names=False) as svc_conn:
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
        # (wrong port, bad service password, TLS issue, …). Service password
        # is never in the exception text itself, so this doesn't leak secrets.
        raise AuthError(f"無法連線/查詢 LDAP：{type(exc).__name__}: {exc}")

    if not entries:
        raise UserNotFoundError("找不到使用者")
    if len(entries) > 1:
        from .log_safe import safe_log
        logger.warning("LDAP returned multiple users for %s — refusing", safe_log(username))
        # NOT UserNotFoundError: this is a config error the admin should see
        # verbatim (the password path maps UserNotFoundError to a generic
        # "帳號或密碼錯誤" for no-enumeration; that would hide this misconfig).
        raise AuthError("LDAP 設定錯誤：搜尋到多筆同名使用者")

    entry = entries[0]
    user_dn = str(entry.entry_dn)
    dn_attr = cfg.get("displayname_attr", "displayName")
    grp_attr = cfg.get("group_attr", "memberOf")
    display_name = (str(entry[dn_attr]) if dn_attr in entry else username)
    groups_raw = entry[grp_attr] if (grp_attr in entry) else []
    group_dns = [str(g) for g in groups_raw] if groups_raw else []
    return {"user_dn": user_dn, "display_name": display_name,
            "group_dns": group_dns}


def _sync_directory_user(username: str, info: dict, backend: str) -> dict:
    """Sync a searched user into local users/groups/OU. Shared tail of both
    the password and reverse-proxy paths."""
    user_row = _sync_user(username, info["display_name"], info["user_dn"],
                          backend=backend)
    _sync_groups(user_row["user_id"], info["group_dns"], backend=backend)
    _sync_ous(user_row["user_id"], info["user_dn"])
    return user_row


def authenticate(username: str, password: str, *, ip: str = "") -> dict:
    """Verify creds against AD/LDAP, sync the user, return user dict."""
    try:
        from ldap3 import Server, Connection, ALL, Tls
        import ssl as _ssl
    except ImportError:
        raise AuthError("ldap3 套件未安裝；請聯絡管理員")

    # 空密碼防護（CRITICAL）：帶合法 DN + 零長度密碼的 LDAP simple bind 在
    # OpenLDAP / Univention 等後端會被當成「unauthenticated / 匿名 bind」回成功
    # （RFC 4513 §5.1.2），造成認證繞過。務必在進 bind 前擋掉，與 test_user_login
    # 的防護一致。帳號空一併擋（回同一訊息，不做使用者列舉）。
    if not username or not password:
        audit_db.log_event("login_fail", username=username or "", ip=ip,
                           details={"reason": "empty_credentials"})
        raise AuthError("帳號或密碼錯誤")

    backend, cfg = _resolve_backend_and_cfg()

    # Step 1+2: service bind + search (shared helper).
    try:
        info = _search_ldap_user(username, cfg)
    except UserNotFoundError:
        # Same error as wrong password (no user enumeration).
        audit_db.log_event("login_fail", username=username, ip=ip,
                           details={"reason": "ldap_user_not_found"})
        raise AuthError("帳號或密碼錯誤")
    user_dn = info["user_dn"]

    # Step 3: try to bind as the discovered user → password check.
    server_url = cfg.get("server_url", "")
    use_tls = bool(cfg.get("use_tls", True))
    verify = bool(cfg.get("verify_cert", True))
    tls = None
    if use_tls or server_url.lower().startswith("ldaps://"):
        tls = Tls(validate=_ssl.CERT_REQUIRED if verify else _ssl.CERT_NONE,
                  version=_ssl.PROTOCOL_TLS_CLIENT)
    server = Server(server_url, get_info=ALL, tls=tls)
    try:
        with Connection(server, user=user_dn, password=password,
                        auto_bind=True, raise_exceptions=True, check_names=False):
            pass
    except Exception:
        audit_db.log_event("login_fail", username=username, ip=ip,
                           details={"reason": "ldap_bind_failed"})
        raise AuthError("帳號或密碼錯誤")

    # Step 4: sync into local users / groups tables.
    user_row = _sync_directory_user(username, info, backend)

    audit_db.log_event("login_success", username=username, ip=ip,
                       details={"source": backend, "dn": user_dn})
    return user_row


def sync_user_by_username(username: str, *, ip: str = "") -> dict:
    """Look up `username` in AD/LDAP via the service account and sync into the
    local users/groups/OU tables WITHOUT any password bind.

    Used by reverse-proxy SSO (`app/core/proxy_sso.py`): the identity has
    already been asserted upstream by Nginx + Kerberos/SPNEGO, so we trust the
    username and only need to resolve the DN + groups + OU. Returns the user
    row (with an extra 'dn' key). Raises UserNotFoundError / AuthError.

    NOTE: this performs NO credential check itself — callers MUST only invoke
    it for a username they have already authenticated by other means.
    """
    backend, cfg = _resolve_backend_and_cfg()
    info = _search_ldap_user(username, cfg)   # UserNotFoundError bubbles up
    user_row = _sync_directory_user(username, info, backend)
    return {**user_row, "dn": info["user_dn"]}


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
        # Activation on real login: a mirrored-only user (pre-synced by
        # directory sync with enabled=0 and NO role) gets the configured
        # new-user default role on their first actual login. Users who already
        # have any role keep exactly what they have (admin assignments intact).
        if not permissions.list_roles_for_subject("user", str(row["id"])):
            from . import roles as _roles
            permissions.set_subject_roles(
                "user", str(row["id"]), [_roles.get_default_role_id()])
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
    # New users get the admin-configured new-user default role (default-user
    # unless admin picked another). See roles.get_default_role_id().
    from . import roles as _roles
    permissions.set_subject_roles("user", str(uid), [_roles.get_default_role_id()])
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


def sync_all_groups(name_contains: str = "") -> dict:
    """列舉目錄內群組,鏡射進本地 `groups` 表（不動成員關係）。

    解決「只看得到曾登入使用者所屬群組」的 JIT 限制 —— 讓 admin 在使用者登入前
    就能把權限指派給任何 AD / LDAP 群組。用 paged_search 處理 AD 1000 筆上限。

    避免把不必要的群組（Domain Users、內建系統群組…）全帶進來,有三層過濾:
      ① `name_contains`：只同步「名稱含此字串」的群組（本函式參數,UI 直接輸入,
         轉成 `(cn=*xxx*)` 條件,效率高）。
      ② `group_search_base`（cfg）：把搜尋範圍縮到某個 OU（只放要用的群組那層）。
      ③ `group_search_filter`（cfg）：完全自訂 LDAP filter（進階）。
    回 {synced, updated, total_seen, sample}。
    """
    from ldap3 import Connection, SUBTREE
    from ldap3.utils.conv import escape_filter_chars

    s = auth_settings.get()
    backend = s.get("backend", "ldap")
    cfg = s.get("ldap", {})
    svc_dn = (cfg.get("service_dn") or "").strip()
    svc_pw = cfg.get("service_password") or ""
    # 可獨立設 group_search_base / filter；預設沿用使用者 base + 常見群組 objectClass。
    base = (cfg.get("group_search_base")
            or cfg.get("user_search_base") or "").strip()
    gfilter = (cfg.get("group_search_filter")
               or "(|(objectClass=group)(objectClass=groupOfNames)"
                  "(objectClass=groupOfUniqueNames)(objectClass=posixGroup))")
    name_attr = cfg.get("group_name_attr", "cn")
    # ① 名稱過濾：AND 進 `(cn=*xxx*)`（跳脫特殊字元防注入）。
    nc = (name_contains or "").strip()
    if nc:
        gfilter = f"(&{gfilter}({name_attr}=*{escape_filter_chars(nc)}*))"
    if not svc_dn or not svc_pw or not base:
        raise AuthError("Service Account / 搜尋 base DN / Service 密碼 都需先填妥")
    if "(" in base or ")" in base:
        raise AuthError("「搜尋 base DN」不能包含 ( 或 )；那是 filter 語法。")

    server = _build_server(cfg)
    # (dn, name, [memberOf parent DNs]) — memberOf gives nested-group parents
    # (mainly AD; harmless-empty on directories without the memberOf overlay).
    seen: list[tuple[str, str, list[str]]] = []
    try:
        with Connection(server, user=svc_dn, password=svc_pw,
                        auto_bind=True, raise_exceptions=True, check_names=False) as conn:
            entries = conn.extend.standard.paged_search(
                search_base=base, search_filter=gfilter,
                search_scope=SUBTREE, attributes=[name_attr, "memberOf"],
                paged_size=500, generator=False)
            for e in entries:
                dn = e.get("dn") or ""
                if not dn or e.get("type") != "searchResEntry":
                    continue
                attrs = e.get("attributes", {}) or {}
                nm = attrs.get(name_attr)
                if isinstance(nm, list):
                    nm = nm[0] if nm else None
                mo = attrs.get("memberOf") or []
                if isinstance(mo, str):
                    mo = [mo]
                seen.append((dn, str(nm) if nm else (_cn_from_dn(dn) or dn),
                             [str(x) for x in mo]))
    except Exception as exc:
        raise AuthError(f"列舉群組失敗：{type(exc).__name__}: {exc}")

    # Resolve each group's parent to the FIRST memberOf that is itself a synced
    # group (so the tree only nests groups we actually show). Case-insensitive
    # DN compare; groups with no in-set parent are roots.
    dn_set = {dn.lower(): dn for dn, _, _ in seen}
    parent_of: dict[str, str] = {}
    for dn, _, mo in seen:
        for p in mo:
            if p.lower() in dn_set and p.lower() != dn.lower():
                parent_of[dn] = dn_set[p.lower()]
                break

    conn_db = auth_db.conn()
    synced = 0
    updated = 0
    with db.tx(conn_db):
        for dn, nm, _mo in seen:
            pdn = parent_of.get(dn, "")
            row = conn_db.execute(
                "SELECT id, name, parent_dn FROM groups WHERE source=? AND external_dn=?",
                (backend, dn)).fetchone()
            if row:
                if nm and row["name"] != nm:
                    conn_db.execute("UPDATE groups SET name=? WHERE id=?",
                                    (nm, row["id"]))
                    updated += 1
                if (row["parent_dn"] or "") != pdn:
                    conn_db.execute("UPDATE groups SET parent_dn=? WHERE id=?",
                                    (pdn, row["id"]))
            else:
                conn_db.execute(
                    "INSERT INTO groups(name, source, external_dn, created_at, parent_dn) "
                    "VALUES (?,?,?,?,?)", (nm, backend, dn, time.time(), pdn))
                synced += 1
    permissions.invalidate_cache()
    audit_db.log_event("ldap_group_sync",
                       details={"synced": synced, "updated": updated,
                                "total_seen": len(seen)})
    return {"synced": synced, "updated": updated, "total_seen": len(seen),
            "sample": [nm for _, nm, _mo in seen[:12]]}


def sync_all_users(name_contains: str = "") -> dict:
    """列舉目錄**所有使用者**,鏡射進本地 `users` 表，讓「使用者管理」預先看到所有
    目錄使用者、可先指派權限，不必等對方登入過。

    這是**目錄鏡射（可見、可指派）**，不是**啟用（可登入使用）**：新建的使用者一律
    `enabled=0`、**不給任何角色**，只當「目錄名冊」讓 admin 看得到、可預先指派。真正
    啟用只透過 ① 本人實際登入（JIT 會設 enabled=1、last_login 並補預設角色），或
    ② admin 明確「啟用」。

    - 不設密碼（仍走 LDAP 驗證），不動 `last_login_at`（未登入者標示「從未登入」）。
    - 同名不同 DN 衝突（同 backend 內）跳過不覆蓋（避免身分接管），計入 skipped_clash。
    回 {synced, updated, total_seen, skipped_clash}。
    """
    from ldap3 import Connection, SUBTREE
    from ldap3.utils.conv import escape_filter_chars

    s = auth_settings.get()
    backend = s.get("backend", "ldap")
    cfg = s.get("ldap", {})
    svc_dn = (cfg.get("service_dn") or "").strip()
    svc_pw = cfg.get("service_password") or ""
    base = (cfg.get("user_search_base") or "").strip()
    ufilter = (cfg.get("directory_user_filter")
               or "(|(objectClass=inetOrgPerson)(objectClass=posixAccount)"
                  "(&(objectClass=user)(!(objectClass=computer))))")
    disp_attr = cfg.get("displayname_attr", "displayName")
    login_attr = cfg.get("username_attr", "sAMAccountName")
    nc = (name_contains or "").strip()
    if nc:
        ufilter = f"(&{ufilter}({login_attr}=*{escape_filter_chars(nc)}*))"
    if not svc_dn or not svc_pw or not base:
        raise AuthError("Service Account / 使用者搜尋 base DN / 密碼 都需先填妥")
    if "(" in base or ")" in base:
        raise AuthError("「使用者搜尋 base DN」不能包含 ( 或 )；那是 filter 語法。")

    server = _build_server(cfg)
    seen: list[tuple[str, str, str]] = []      # (dn, login, display)
    try:
        with Connection(server, user=svc_dn, password=svc_pw,
                        auto_bind=True, raise_exceptions=True, check_names=False) as conn:
            entries = conn.extend.standard.paged_search(
                search_base=base, search_filter=ufilter,
                search_scope=SUBTREE, attributes=[disp_attr, login_attr],
                paged_size=500, generator=False)
            for e in entries:
                dn = e.get("dn") or ""
                if not dn or e.get("type") != "searchResEntry":
                    continue
                a = e.get("attributes", {}) or {}

                def _one(v):
                    return (v[0] if isinstance(v, list) and v else
                            (v if isinstance(v, str) else None))
                login = _one(a.get(login_attr))
                if not login:
                    continue                    # no username → can't key it
                disp = _one(a.get(disp_attr)) or login
                seen.append((dn, str(login), str(disp)))
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"列舉使用者失敗：{type(exc).__name__}: {exc}")

    conn_db = auth_db.conn()
    synced = updated = skipped = 0
    now = time.time()
    with db.tx(conn_db):
        for dn, login, disp in seen:
            row = conn_db.execute(
                "SELECT id, display_name FROM users WHERE source=? AND external_dn=?",
                (backend, dn)).fetchone()
            if row:
                if disp and row["display_name"] != disp:
                    # 只更新顯示名稱，**絕不動 enabled**（v1.12.70 不變量：鏡射 ≠
                    # 啟用）。舊版此處會 enabled=1，導致「已去啟用的鏡射帳號」在目錄
                    # 端改名後被靜默重新啟用而可登入——安全回歸，已修正。啟用只透過
                    # 本人登入 JIT 或 admin 明確操作。
                    conn_db.execute(
                        "UPDATE users SET display_name=? WHERE id=?",
                        (disp, row["id"]))
                    updated += 1
                continue
            clash = conn_db.execute(
                "SELECT id FROM users WHERE username=? AND source=?",
                (login, backend)).fetchone()
            if clash:
                skipped += 1
                continue
            # enabled=0 → 目錄可見但「未啟用」；不給角色。啟用由本人登入(JIT)或
            # admin 明確操作。已驗證 enabled=0 不擋日後 LDAP 登入。
            conn_db.execute(
                "INSERT INTO users(username, display_name, source, external_dn, "
                "enabled, is_admin_seed, created_at) VALUES (?,?,?,?,0,0,?)",
                (login, disp, backend, dn, now))
            synced += 1
    permissions.invalidate_cache()
    audit_db.log_event("ldap_user_sync",
                       details={"synced": synced, "updated": updated,
                                "total_seen": len(seen), "skipped_clash": skipped})
    return {"synced": synced, "updated": updated, "total_seen": len(seen),
            "skipped_clash": skipped}


def get_group_members(group_dn: str) -> list[dict]:
    """向 AD / LDAP 查某群組的**直接成員**（含尚未登入過本系統的人）。

    用 `(memberOf=<groupDN>)` 在使用者 base 下 paged_search（避開 AD 群組 member
    多值屬性 1500 筆上限的 ranged retrieval）。回 [{name, login, dn}, ...]（依名稱排序）。
    """
    from ldap3 import Connection, SUBTREE
    from ldap3.utils.conv import escape_filter_chars

    group_dn = (group_dn or "").strip()
    if not group_dn:
        raise AuthError("此群組沒有目錄 DN，無法查詢目錄成員。")
    s = auth_settings.get()
    cfg = s.get("ldap", {})
    svc_dn = (cfg.get("service_dn") or "").strip()
    svc_pw = cfg.get("service_password") or ""
    user_base = (cfg.get("user_search_base") or "").strip()
    disp_attr = cfg.get("displayname_attr", "displayName")
    login_attr = cfg.get("username_attr", "sAMAccountName")
    member_filter = (cfg.get("group_member_filter")
                     or "(memberOf={group_dn})")
    if not svc_dn or not svc_pw or not user_base:
        raise AuthError("Service Account / 使用者搜尋 base / Service 密碼 都需先填妥")

    filt = member_filter.replace("{group_dn}", escape_filter_chars(group_dn))
    server = _build_server(cfg)
    out: list[dict] = []
    try:
        with Connection(server, user=svc_dn, password=svc_pw,
                        auto_bind=True, raise_exceptions=True, check_names=False) as conn:
            entries = conn.extend.standard.paged_search(
                search_base=user_base, search_filter=filt,
                search_scope=SUBTREE, attributes=[disp_attr, login_attr],
                paged_size=500, generator=False)
            for e in entries:
                dn = e.get("dn") or ""
                if not dn or e.get("type") != "searchResEntry":
                    continue
                a = e.get("attributes", {}) or {}
                def _one(v):
                    return (v[0] if isinstance(v, list) and v else
                            (v if isinstance(v, str) else None))
                name = _one(a.get(disp_attr))
                login = _one(a.get(login_attr))
                out.append({
                    "name": str(name) if name else (_cn_from_dn(dn) or dn),
                    "login": str(login) if login else "",
                    "dn": dn,
                })
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"查詢目錄成員失敗：{type(exc).__name__}: {exc}")
    out.sort(key=lambda x: (x["name"] or "").lower())
    return out


def _dir_root_base() -> str:
    cfg = auth_settings.get().get("ldap", {})
    return (cfg.get("directory_root_base")
            or cfg.get("user_search_base") or "").strip()


def list_ou_children(parent_dn: str = "") -> list[dict]:
    """列某節點的**直接子 OU / 容器**（給 treeview 逐層展開）。parent_dn 空 = 根
    （directory_root_base 或 user_search_base）。回 [{dn, name, has_children}]。"""
    from ldap3 import Connection, LEVEL
    s = auth_settings.get()
    cfg = s.get("ldap", {})
    svc_dn = (cfg.get("service_dn") or "").strip()
    svc_pw = cfg.get("service_password") or ""
    base = (parent_dn or "").strip() or _dir_root_base()
    if not svc_dn or not svc_pw or not base:
        raise AuthError("Service Account / 目錄 base / 密碼 都需先填妥")
    if "(" in base or ")" in base:
        raise AuthError("節點 DN 不合法。")
    node_filter = (cfg.get("directory_node_filter")
                   or "(|(objectClass=organizationalUnit)(objectClass=container)"
                      "(objectClass=organizationalRole))")
    server = _build_server(cfg)
    out: list[dict] = []
    try:
        with Connection(server, user=svc_dn, password=svc_pw,
                        auto_bind=True, raise_exceptions=True, check_names=False) as conn:
            entries = conn.extend.standard.paged_search(
                search_base=base, search_filter=node_filter,
                search_scope=LEVEL, attributes=["ou", "cn", "objectClass"],
                paged_size=500, generator=False)
            for e in entries:
                dn = e.get("dn") or ""
                if not dn or e.get("type") != "searchResEntry":
                    continue
                a = e.get("attributes", {}) or {}
                nm = a.get("ou") or a.get("cn")
                if isinstance(nm, list):
                    nm = nm[0] if nm else None
                # 從 objectClass 判斷節點型別，讓 treeview 用不同 icon 區分
                # OU / 容器(container) / 群組(group) / organizationalRole。
                ocs = a.get("objectClass") or []
                if isinstance(ocs, str):
                    ocs = [ocs]
                ocl = {str(x).lower() for x in ocs}
                if "organizationalunit" in ocl:
                    ntype = "ou"
                elif "group" in ocl or "groupofnames" in ocl or "posixgroup" in ocl:
                    ntype = "group"
                elif "organizationalrole" in ocl:
                    ntype = "role"
                elif "container" in ocl:
                    ntype = "container"
                else:
                    ntype = "node"
                out.append({"dn": dn, "name": str(nm) if nm else (_cn_from_dn(dn) or dn),
                            "type": ntype,
                            "has_children": True})  # 展開時再確認,先給展開箭頭
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"列目錄節點失敗：{type(exc).__name__}: {exc}")
    out.sort(key=lambda x: (x["name"] or "").lower())
    return out


def list_ou_users(ou_dn: str, recursive: bool = False) -> list[dict]:
    """列某 OU 底下的使用者。recursive=False 只列直屬（LEVEL）。
    回 [{name, login, dn}]（未標本地,由端點標）。"""
    from ldap3 import Connection, LEVEL, SUBTREE
    s = auth_settings.get()
    cfg = s.get("ldap", {})
    svc_dn = (cfg.get("service_dn") or "").strip()
    svc_pw = cfg.get("service_password") or ""
    ou_dn = (ou_dn or "").strip()
    if not svc_dn or not svc_pw or not ou_dn:
        raise AuthError("Service Account / OU DN / 密碼 都需先填妥")
    if "(" in ou_dn or ")" in ou_dn:
        raise AuthError("OU DN 不合法。")
    # 跨目錄相容:AD 用 objectClass=user(排除 computer);OpenLDAP / Univention
    # 用 inetOrgPerson / posixAccount。check_names=False 讓未知 class 交給 server,
    # 三值邏輯下各目錄只會匹配它有的那組。
    user_filter = (cfg.get("directory_user_filter")
                   or "(|(objectClass=inetOrgPerson)(objectClass=posixAccount)"
                      "(&(objectClass=user)(!(objectClass=computer))))")
    disp_attr = cfg.get("displayname_attr", "displayName")
    login_attr = cfg.get("username_attr", "sAMAccountName")
    server = _build_server(cfg)
    out: list[dict] = []
    try:
        with Connection(server, user=svc_dn, password=svc_pw,
                        auto_bind=True, raise_exceptions=True, check_names=False) as conn:
            entries = conn.extend.standard.paged_search(
                search_base=_safe_search_base(ou_dn), search_filter=user_filter,
                search_scope=(SUBTREE if recursive else LEVEL),
                attributes=[disp_attr, login_attr],
                paged_size=500, generator=False)
            for e in entries:
                dn = e.get("dn") or ""
                if not dn or e.get("type") != "searchResEntry":
                    continue
                a = e.get("attributes", {}) or {}
                def _one(v):
                    return (v[0] if isinstance(v, list) and v else
                            (v if isinstance(v, str) else None))
                name = _one(a.get(disp_attr))
                login = _one(a.get(login_attr))
                out.append({"name": str(name) if name else (_cn_from_dn(dn) or dn),
                            "login": str(login) if login else "", "dn": dn})
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"列 OU 使用者失敗：{type(exc).__name__}: {exc}")
    out.sort(key=lambda x: (x["name"] or "").lower())
    return out


def search_selected_objects(rules: list[dict], cap: int = 3000) -> dict:
    """目錄瀏覽「已選定」模式：依 filter 規則搜目錄，回符合的 ou / group / user。

    規則清單見 dir_filter；每條規則各自 SUBTREE 搜一次（base 為規則 base_dn 或
    目錄 root），結果以 DN 去重。回 {objects:[{dn,name,type,login}], count, capped}。
    """
    from ldap3 import Connection, SUBTREE
    from . import dir_filter as _df
    s = auth_settings.get()
    cfg = s.get("ldap", {})
    svc_dn = (cfg.get("service_dn") or "").strip()
    svc_pw = cfg.get("service_password") or ""
    if not svc_dn or not svc_pw:
        raise AuthError("Service Account / 密碼 需先填妥")
    root = _dir_root_base()
    disp_attr = cfg.get("displayname_attr", "displayName")
    login_attr = cfg.get("username_attr", "sAMAccountName")
    server = _build_server(cfg)
    seen: dict[str, dict] = {}
    capped = False

    def _one(v):
        return (v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else None))

    try:
        with Connection(server, user=svc_dn, password=svc_pw, auto_bind=True,
                        raise_exceptions=True, check_names=False) as conn:
            for rule in (rules or []):
                base = _df.rule_base(rule, root)
                # base 內含括號 = 不合法（防注入）；空 base 跳過
                if not base or "(" in base or ")" in base:
                    continue
                filt = _df.build_rule_filter(rule)
                # generator=True → 逐筆串流，達 cap 即 break，不把整個結果集（可能
                # 數十萬筆）一次拉進記憶體。generator=False 會在回傳前全部載入，讓
                # 下方的 cap 檢查擋不住 LDAP fetch 造成 OOM（單 uvicorn worker）。
                entries = conn.extend.standard.paged_search(
                    search_base=base, search_filter=filt, search_scope=SUBTREE,
                    attributes=["ou", "cn", disp_attr, login_attr, "objectClass"],
                    paged_size=500, generator=True)
                for e in entries:
                    dn = e.get("dn") or ""
                    if not dn or e.get("type") != "searchResEntry":
                        continue
                    key = dn.strip().lower()
                    if key in seen:
                        continue
                    a = e.get("attributes", {}) or {}
                    ocs = a.get("objectClass") or []
                    if isinstance(ocs, str):
                        ocs = [ocs]
                    ocl = {str(x).lower() for x in ocs}
                    if "organizationalunit" in ocl:
                        typ = "ou"
                    elif ocl & {"group", "groupofnames", "groupofuniquenames", "posixgroup"}:
                        typ = "group"
                    elif (ocl & {"inetorgperson", "posixaccount", "user"}
                          and "computer" not in ocl):
                        typ = "user"
                    else:
                        typ = "node"
                    nm = (_one(a.get(disp_attr)) or _one(a.get("ou"))
                          or _one(a.get("cn")) or _cn_from_dn(dn) or dn)
                    seen[key] = {"dn": dn, "name": str(nm), "type": typ,
                                 "login": str(_one(a.get(login_attr)) or "")}
                    if len(seen) >= cap:
                        capped = True
                        break
                if capped:
                    break
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"搜尋已選定物件失敗：{type(exc).__name__}: {exc}")
    objs = sorted(seen.values(), key=lambda x: (x["type"], (x["name"] or "").lower()))
    return {"objects": objs, "count": len(objs), "capped": capped}


# 群組目錄成員數快取（LDAP 無便宜 COUNT,只能列舉 → 快取避免每次載入頁都重查）。
import time as _time
_member_count_cache: dict[str, tuple[int, float]] = {}
_MEMBER_COUNT_TTL = 300.0  # 秒


def count_group_members(group_dn: str) -> int:
    """回某群組在目錄的直接成員數（快取 5 分鐘）。給群組清單頁的「成員數」用。"""
    dn = (group_dn or "").strip()
    if not dn:
        return 0
    key = dn.lower()
    hit = _member_count_cache.get(key)
    now = _time.time()
    if hit and (now - hit[1]) < _MEMBER_COUNT_TTL:
        return hit[0]
    n = len(get_group_members(dn))
    _member_count_cache[key] = (n, now)
    return n


def get_user_detail(user_dn: str) -> dict:
    """查單一使用者的完整目錄屬性（base scope）。給目錄瀏覽點某人看細節用。
    回 {dn, attrs: {attr: value|[values]}}。多值屬性回 list,單值回 str。"""
    from ldap3 import Connection, BASE
    user_dn = (user_dn or "").strip()
    if not user_dn:
        raise AuthError("缺少使用者 DN")
    cfg = auth_settings.get().get("ldap", {})
    svc_dn = (cfg.get("service_dn") or "").strip()
    svc_pw = cfg.get("service_password") or ""
    if not svc_dn or not svc_pw:
        raise AuthError("Service Account / 密碼 需先填妥")
    server = _build_server(cfg)
    try:
        with Connection(server, user=svc_dn, password=svc_pw,
                        auto_bind=True, raise_exceptions=True, check_names=False) as conn:
            conn.search(search_base=_safe_search_base(user_dn),
                        search_filter="(objectClass=*)",
                        search_scope=BASE, attributes=["*"])
            if not conn.response:
                raise AuthError("找不到該使用者")
            entry = None
            for e in conn.response:
                if e.get("type") == "searchResEntry":
                    entry = e; break
            if entry is None:
                raise AuthError("找不到該使用者")
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AuthError(f"查詢失敗：{type(exc).__name__}: {exc}")

    raw = entry.get("attributes", {}) or {}
    # 過濾掉二進位 / 敏感 / 太吵的屬性,值轉成可顯示字串。
    HIDE = {"userpassword", "unicodepwd", "jpegphoto", "thumbnailphoto",
            "objectsid", "objectguid", "usercertificate", "krb5key",
            "sambantpassword", "sambalmpassword", "userpkcs12",
            # 密碼歷史雜湊 — 屬憑證類敏感資料，即使是雜湊也不在 UI 顯示。
            "pwhistory", "sambapasswordhistory", "krbprincipalkey",
            "supplementalcredentials", "msds-keycredentiallink"}
    attrs: dict = {}
    for k, v in raw.items():
        if k.lower() in HIDE:
            continue
        if isinstance(v, (bytes, bytearray)):
            continue  # 略過二進位
        if isinstance(v, list):
            vv = [str(x) for x in v if not isinstance(x, (bytes, bytearray))]
            if not vv:
                continue
            attrs[k] = vv if len(vv) > 1 else vv[0]
        else:
            attrs[k] = str(v)
    return {"dn": user_dn, "attrs": attrs}
