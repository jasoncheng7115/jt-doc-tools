"""High-level CRUD for local groups.

External (LDAP/AD) groups land in this table at user-login time (M3); they
get source!='local' and external_dn populated. Admin can edit role
assignments for those too, but cannot rename / delete them (the directory
owns them).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from . import auth_db, db, permissions

logger = logging.getLogger(__name__)


def list_groups(source: Optional[str] = None) -> list[dict]:
    conn = auth_db.conn()
    if source:
        rows = conn.execute(
            "SELECT id, name, source, external_dn, description, created_at "
            "FROM groups WHERE source=? ORDER BY name", (source,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, source, external_dn, description, created_at "
            "FROM groups ORDER BY source, name"
        ).fetchall()
    out = []
    for r in rows:
        member_rows = conn.execute(
            "SELECT user_id FROM group_members WHERE group_id=?", (r["id"],)
        ).fetchall()
        member_ids = [m["user_id"] for m in member_rows]
        out.append({
            "id": r["id"], "name": r["name"], "source": r["source"],
            "external_dn": r["external_dn"], "description": r["description"],
            "created_at": r["created_at"],
            "member_count": len(member_ids),
            "member_ids": member_ids,
            "roles": permissions.list_roles_for_subject("group", str(r["id"])),
        })
    return out


def get(group_id: int) -> Optional[dict]:
    for g in list_groups():
        if g["id"] == group_id:
            return g
    return None


def create_local(name: str, description: str = "") -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("群組名稱不能空白")
    if len(name) > 64:
        raise ValueError("群組名稱不得超過 64 字元")
    conn = auth_db.conn()
    if conn.execute("SELECT 1 FROM groups WHERE source='local' AND name=?",
                    (name,)).fetchone():
        raise ValueError(f"群組 「{name}」 已存在")
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO groups(name, source, description, created_at) "
            "VALUES (?, 'local', ?, ?)",
            (name, (description or "")[:500], time.time()),
        )
    return cur.lastrowid


def update(group_id: int, *, name: Optional[str] = None,
           description: Optional[str] = None,
           roles: Optional[list[str]] = None) -> None:
    conn = auth_db.conn()
    row = conn.execute("SELECT source FROM groups WHERE id=?",
                       (group_id,)).fetchone()
    if not row:
        raise ValueError(f"群組 id={group_id} 不存在")
    is_local = row["source"] == "local"
    with db.tx(conn):
        if name is not None:
            if not is_local:
                raise ValueError("外部目錄的群組無法改名")
            name = name.strip()
            if not name:
                raise ValueError("群組名稱不能空白")
            if len(name) > 64:
                raise ValueError("群組名稱不得超過 64 字元")
            conn.execute("UPDATE groups SET name=? WHERE id=?", (name, group_id))
        if description is not None:
            conn.execute("UPDATE groups SET description=? WHERE id=?",
                         ((description or "")[:500], group_id))
    if roles is not None:
        permissions.set_subject_roles("group", str(group_id), roles)


def delete(group_id: int) -> None:
    conn = auth_db.conn()
    row = conn.execute("SELECT source FROM groups WHERE id=?",
                       (group_id,)).fetchone()
    if not row:
        raise ValueError(f"群組 id={group_id} 不存在")
    if row["source"] != "local":
        raise ValueError("外部目錄的群組無法刪除")
    with db.tx(conn):
        conn.execute(
            "DELETE FROM subject_roles WHERE subject_type='group' AND subject_key=?",
            (str(group_id),))
        conn.execute(
            "DELETE FROM subject_perms WHERE subject_type='group' AND subject_key=?",
            (str(group_id),))
        conn.execute("DELETE FROM groups WHERE id=?", (group_id,))
    permissions.invalidate_cache()


def set_members(group_id: int, user_ids: list[int]) -> None:
    """Replace the membership list for a local group."""
    conn = auth_db.conn()
    row = conn.execute("SELECT source FROM groups WHERE id=?",
                       (group_id,)).fetchone()
    if not row:
        raise ValueError(f"群組 id={group_id} 不存在")
    if row["source"] != "local":
        raise ValueError("外部目錄的群組成員由目錄端管理")
    with db.tx(conn):
        conn.execute("DELETE FROM group_members WHERE group_id=?", (group_id,))
        for uid in user_ids:
            conn.execute(
                "INSERT OR IGNORE INTO group_members(group_id, user_id) "
                "VALUES (?,?)", (group_id, uid),
            )
    permissions.invalidate_cache()


def list_members(group_id: int) -> list[dict]:
    conn = auth_db.conn()
    rows = conn.execute(
        "SELECT u.id, u.username, u.display_name, u.enabled "
        "FROM users u JOIN group_members gm ON gm.user_id=u.id "
        "WHERE gm.group_id=? ORDER BY u.username", (group_id,)
    ).fetchall()
    return [{"id": r["id"], "username": r["username"],
             "display_name": r["display_name"] or r["username"],
             "enabled": bool(r["enabled"])} for r in rows]
