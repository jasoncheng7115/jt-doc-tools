"""High-level CRUD for local users (admin-facing).

External (LDAP/AD) users land in the same `users` table at login time
(populated by `auth_ldap.py`); admin can still see/edit their role
assignments here, but cannot reset password / change username (those
are owned by the directory).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from . import auth_db, db, passwords, permissions, sessions

logger = logging.getLogger(__name__)


_USERNAME_RE = re.compile(r"[A-Za-z0-9._\-]+")


def _validate_username(username: str) -> str:
    username = (username or "").strip()
    if not username:
        raise ValueError("帳號不能空白")
    if len(username) > 64:
        raise ValueError("帳號不得超過 64 字元")
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError("帳號只能用英數、點、底線、減號")
    return username


def list_users() -> list[dict]:
    conn = auth_db.conn()
    rows = conn.execute(
        "SELECT id, username, display_name, source, external_dn, enabled, "
        "is_admin_seed, created_at, last_login_at FROM users ORDER BY username"
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "username": r["username"],
            "display_name": r["display_name"] or r["username"],
            "source": r["source"], "external_dn": r["external_dn"],
            "enabled": bool(r["enabled"]),
            "is_admin_seed": bool(r["is_admin_seed"]),
            "created_at": r["created_at"], "last_login_at": r["last_login_at"],
            "roles": permissions.list_roles_for_subject("user", str(r["id"])),
        })
    return out


def get_by_id(user_id: int) -> Optional[dict]:
    for u in list_users():
        if u["id"] == user_id:
            return u
    return None


def get_by_username(username: str) -> Optional[dict]:
    for u in list_users():
        if u["username"] == username:
            return u
    return None


def create_local(username: str, display_name: str, password: str,
                 *, enabled: bool = True,
                 roles: Optional[list[str]] = None) -> int:
    """Create a local-mode user. Returns new user_id.

    Default role assignment: 'default-user' if `roles` is None (admin-friendly
    common case). Pass `roles=[]` to explicitly create with no roles.
    """
    username = _validate_username(username)
    display_name = (display_name or "").strip() or username
    if len(display_name) > 64:
        raise ValueError("顯示名稱不得超過 64 字元")
    ok, err = passwords.validate_password(password)
    if not ok:
        raise ValueError(err)
    pw_hash = passwords.hash_password(password)
    conn = auth_db.conn()
    if conn.execute("SELECT 1 FROM users WHERE username=? AND source='local'",
                    (username,)).fetchone():
        raise ValueError(f"帳號 「{username}」 已存在")
    now = time.time()
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO users(username, display_name, password_hash, source, "
            "enabled, is_admin_seed, created_at) "
            "VALUES (?, ?, ?, 'local', ?, 0, ?)",
            (username, display_name, pw_hash, 1 if enabled else 0, now),
        )
        new_id = cur.lastrowid
    # Assign roles outside the tx (calls invalidate_cache, etc).
    role_ids = list(roles) if roles is not None else ["default-user"]
    permissions.set_subject_roles("user", str(new_id), role_ids)
    return new_id


def update(user_id: int, *, display_name: Optional[str] = None,
           enabled: Optional[bool] = None,
           roles: Optional[list[str]] = None,
           groups: Optional[list[int]] = None) -> None:
    """Update a user's mutable attributes. None = no change."""
    conn = auth_db.conn()
    existing = conn.execute(
        "SELECT is_admin_seed FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not existing:
        raise ValueError(f"使用者 id={user_id} 不存在")

    with db.tx(conn):
        if display_name is not None:
            display_name = display_name.strip()
            if not display_name:
                raise ValueError("顯示名稱不能空白")
            if len(display_name) > 64:
                raise ValueError("顯示名稱不得超過 64 字元")
            conn.execute("UPDATE users SET display_name=? WHERE id=?",
                         (display_name, user_id))
        if enabled is not None:
            # Refuse to disable the seed admin (would lock everyone out).
            if existing["is_admin_seed"] and not enabled:
                raise ValueError("不能停用初始管理員帳號")
            conn.execute("UPDATE users SET enabled=? WHERE id=?",
                         (1 if enabled else 0, user_id))
            if not enabled:
                # Disabled → revoke active sessions.
                conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        if groups is not None:
            conn.execute("DELETE FROM group_members WHERE user_id=?", (user_id,))
            for gid in groups:
                conn.execute(
                    "INSERT OR IGNORE INTO group_members(group_id, user_id) "
                    "VALUES (?,?)", (gid, user_id),
                )

    if roles is not None:
        # Refuse to remove `admin` role from the seed admin.
        if existing["is_admin_seed"] and "admin" not in roles:
            raise ValueError("不能移除初始管理員的 admin 角色")
        permissions.set_subject_roles("user", str(user_id), roles)
    elif groups is not None:
        # Group membership change affects effective perms — invalidate cache.
        permissions.invalidate_cache()


def reset_password(user_id: int, new_password: str) -> None:
    """Admin-initiated password reset (bypasses the 'know-old-password' check)."""
    ok, err = passwords.validate_password(new_password)
    if not ok:
        raise ValueError(err)
    conn = auth_db.conn()
    row = conn.execute("SELECT source FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise ValueError(f"使用者 id={user_id} 不存在")
    if row["source"] != "local":
        raise ValueError("LDAP/AD 使用者的密碼由目錄端管理，無法在這裡重設")
    pw_hash = passwords.hash_password(new_password)
    with db.tx(conn):
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (pw_hash, user_id))
        # Revoke all active sessions so all browser cookies stop working.
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def change_password(user_id: int, old_password: str, new_password: str,
                    keep_current_session: Optional[str] = None) -> None:
    """User self-service password change. Verifies old_password before
    updating; revokes other sessions but keeps the current one (passed via
    `keep_current_session` raw token) so the user doesn't get logged out
    of the tab they used to change the password.

    Raises ValueError on: wrong old password, weak new password, non-local
    user (LDAP/AD passwords are managed by the directory).
    """
    ok, err = passwords.validate_password(new_password)
    if not ok:
        raise ValueError(err)
    if old_password == new_password:
        raise ValueError("新密碼不能與舊密碼相同")
    conn = auth_db.conn()
    row = conn.execute(
        "SELECT source, password_hash FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"使用者 id={user_id} 不存在")
    if row["source"] != "local":
        raise ValueError("LDAP/AD 使用者的密碼由目錄端管理，請聯絡 IT 改 AD/LDAP 密碼")
    if not passwords.verify_password(old_password, row["password_hash"]):
        # Constant-time mismatch path: don't leak whether user exists.
        raise ValueError("舊密碼錯誤")
    new_hash = passwords.hash_password(new_password)
    # Hash the keep token to compare with sessions.token_hash (sessions
    # stores SHA-256 of raw token).
    keep_hash = ""
    if keep_current_session:
        import hashlib
        keep_hash = hashlib.sha256(keep_current_session.encode("utf-8")).hexdigest()
    with db.tx(conn):
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (new_hash, user_id))
        if keep_hash:
            conn.execute(
                "DELETE FROM sessions WHERE user_id=? AND token_hash<>?",
                (user_id, keep_hash))
        else:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def delete(user_id: int) -> None:
    conn = auth_db.conn()
    row = conn.execute("SELECT is_admin_seed FROM users WHERE id=?",
                       (user_id,)).fetchone()
    if not row:
        raise ValueError(f"使用者 id={user_id} 不存在")
    if row["is_admin_seed"]:
        raise ValueError("不能刪除初始管理員帳號")
    # If this is the last admin, refuse — would lock everyone out.
    if "admin" in permissions.list_roles_for_subject("user", str(user_id)):
        # Count other admins.
        admin_count = _count_admin_users(conn)
        if admin_count <= 1:
            raise ValueError("這是最後一位管理員，無法刪除")
    with db.tx(conn):
        # CASCADE: sessions, group_members. subject_roles uses role_id FK,
        # but the (user, role) rows are keyed by string subject_key NOT
        # FK'd to users.id, so manually clean those.
        conn.execute(
            "DELETE FROM subject_roles WHERE subject_type='user' AND subject_key=?",
            (str(user_id),))
        conn.execute(
            "DELETE FROM subject_perms WHERE subject_type='user' AND subject_key=?",
            (str(user_id),))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    permissions.invalidate_cache()


def _count_admin_users(conn) -> int:
    """How many ENABLED users have the admin role (directly or via groups)?
    We count direct user→admin role assignments only (the common case);
    nested via group is possible but rare for admin role and usually a
    misconfiguration."""
    rows = conn.execute(
        "SELECT u.id FROM users u "
        "JOIN subject_roles sr ON sr.subject_type='user' AND sr.subject_key=CAST(u.id AS TEXT) "
        "WHERE u.enabled=1 AND sr.role_id='admin'"
    ).fetchall()
    return len(rows)
