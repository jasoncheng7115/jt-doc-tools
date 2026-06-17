"""Just-in-time provisioning for SSO (OIDC / SAML) users.

Mirrors ``auth_ldap._sync_user`` / ``_sync_groups`` but keyed by an OIDC ``sub``
or SAML ``NameID`` instead of an LDAP DN. On first login a local ``users`` row
is created (``source`` = ``oidc`` / ``saml``, ``external_dn`` = the stable
external id) with the ``default-user`` role; IdP groups are synced as ``groups``
rows so the admin can map them to roles in the permission matrix exactly like
AD groups. An optional ``admin_group`` additively grants/revokes the built-in
``admin`` role to its members on each login (other roles are preserved).
"""
from __future__ import annotations

import time
from typing import Optional

from . import auth_db, db, permissions
from ..logging_setup import get_logger

logger = get_logger(__name__)


class SSOProvisionError(Exception):
    pass


def provision(provider: str, *, external_id: str, username: str,
              display_name: str, groups: Optional[list[str]] = None,
              admin_group: str = "") -> dict:
    """Create or refresh the local user for an authenticated SSO identity.
    Returns a user dict: {user_id, username, display_name, source, created}."""
    if provider not in ("oidc", "saml"):
        raise SSOProvisionError("bad provider")
    external_id = (external_id or "").strip()
    if not external_id:
        raise SSOProvisionError("IdP 未提供穩定使用者識別 (sub / NameID)")
    username = (username or external_id).strip()[:64] or external_id
    display_name = (display_name or username).strip()[:64] or username
    groups = [g for g in (groups or []) if g and g.strip()]

    user = _sync_user(provider, external_id, username, display_name)
    _sync_groups(user["user_id"], provider, groups)
    _apply_admin_group(user["user_id"], groups, admin_group)
    permissions.invalidate_cache()
    return user


def _sync_user(provider: str, external_id: str, username: str,
               display_name: str) -> dict:
    conn = auth_db.conn()
    now = time.time()
    row = conn.execute(
        "SELECT id FROM users WHERE source=? AND external_dn=?",
        (provider, external_id),
    ).fetchone()
    if row:
        if not _user_enabled(conn, row["id"]):
            raise SSOProvisionError("此帳號已被停用，請聯絡管理員")
        with db.tx(conn):
            conn.execute(
                "UPDATE users SET display_name=?, last_login_at=? WHERE id=?",
                (display_name, now, row["id"]),
            )
        return {"user_id": row["id"], "username": username,
                "display_name": display_name, "source": provider, "created": False}

    # First login. UNIQUE(username, source) lets the same name coexist across
    # realms (local vs oidc); refuse a *different* external id reusing the same
    # username within this provider to prevent silent identity takeover.
    clash = conn.execute(
        "SELECT external_dn FROM users WHERE username=? AND source=?",
        (username, provider),
    ).fetchone()
    if clash:
        raise SSOProvisionError(
            f"已有另一個 {provider.upper()} 身分使用帳號名「{username}」，"
            f"請聯絡管理員處理同名衝突。")
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO users(username, display_name, source, external_dn, "
            "enabled, is_admin_seed, created_at, last_login_at) "
            "VALUES (?, ?, ?, ?, 1, 0, ?, ?)",
            (username, display_name, provider, external_id, now, now),
        )
        uid = cur.lastrowid
    permissions.set_subject_roles("user", str(uid), ["default-user"])
    from .log_safe import safe_log
    # username comes from an IdP claim — sanitise (strip CR/LF) before logging
    # to prevent log injection (CodeQL #115).
    logger.info("SSO provisioned new %s user id=%s username=%s",
                provider, uid, safe_log(username))
    return {"user_id": uid, "username": username,
            "display_name": display_name, "source": provider, "created": True}


def _user_enabled(conn, user_id: int) -> bool:
    r = conn.execute("SELECT enabled FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(r and r["enabled"])


def _sync_groups(user_id: int, provider: str, group_names: list[str]) -> None:
    """Ensure each IdP group has a local ``groups`` row (source=provider,
    external_dn=group name), then rebuild this user's membership."""
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM group_members WHERE user_id=?", (user_id,))
        for name in group_names:
            row = conn.execute(
                "SELECT id FROM groups WHERE source=? AND external_dn=?",
                (provider, name),
            ).fetchone()
            if row:
                gid = row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO groups(name, source, external_dn, created_at) "
                    "VALUES (?, ?, ?, ?)", (name[:128], provider, name, time.time()),
                )
                gid = cur.lastrowid
            conn.execute(
                "INSERT OR IGNORE INTO group_members(group_id, user_id) VALUES (?,?)",
                (gid, user_id),
            )


def _apply_admin_group(user_id: int, group_names: list[str], admin_group: str) -> None:
    """Convenience: when admin_group is configured, additively grant/revoke the
    built-in admin role to the user based on membership (other roles untouched)."""
    admin_group = (admin_group or "").strip()
    if not admin_group:
        return  # admin role left entirely to the group→role matrix
    if admin_group in group_names:
        permissions.assign_role("user", str(user_id), "admin")
    else:
        permissions.unassign_role("user", str(user_id), "admin")
