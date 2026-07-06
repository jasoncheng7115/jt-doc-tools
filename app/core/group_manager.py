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
    """List groups with member ids + role assignments.

    Batched: one query for the rows, one for ALL group_members, one for ALL
    role assignments — instead of the old 2N+1 (a members query AND a roles
    query per group), which made 群組管理 slow with thousands of groups.

    `member_count` = local group_members count (people who have logged in).
    `dir_member_count` = the directory's real count, cached by the scheduled
    sync (NULL until first synced); `dir_synced_at` = when. The page shows
    `dir_member_count` when available so it never fires a live LDAP query
    per row on load."""
    conn = auth_db.conn()
    if source:
        rows = conn.execute(
            "SELECT id, name, source, external_dn, description, created_at, "
            "member_count, member_count_synced_at, parent_dn "
            "FROM groups WHERE source=? ORDER BY name", (source,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, source, external_dn, description, created_at, "
            "member_count, member_count_synced_at, parent_dn "
            "FROM groups ORDER BY source, name"
        ).fetchall()
    # Batch: all members in one pass, all roles in one query.
    members_by_group: dict[int, list[int]] = {}
    for m in conn.execute("SELECT group_id, user_id FROM group_members").fetchall():
        members_by_group.setdefault(m["group_id"], []).append(m["user_id"])
    roles_by_group = permissions.list_roles_for_subjects(
        "group", [str(r["id"]) for r in rows])
    out = []
    for r in rows:
        member_ids = members_by_group.get(r["id"], [])
        out.append({
            "id": r["id"], "name": r["name"], "source": r["source"],
            "external_dn": r["external_dn"], "description": r["description"],
            "created_at": r["created_at"],
            "member_count": len(member_ids),
            "member_ids": member_ids,
            "dir_member_count": r["member_count"],
            "dir_synced_at": r["member_count_synced_at"],
            "parent_dn": r["parent_dn"] or "",
            "roles": roles_by_group.get(str(r["id"]), []),
        })
    return out


def order_groups_as_tree(groups: list[dict]) -> list[dict]:
    """Reorder a flat group list into parent-before-children tree order, adding
    a `depth` key to each (0 = root). Nesting comes from `parent_dn` pointing at
    another group's `external_dn` (nested AD/LDAP groups). Groups whose parent is
    not in the set (or local groups) are roots. Cycle-safe; every input group is
    emitted exactly once."""
    by_dn: dict[str, dict] = {}
    for g in groups:
        dn = (g.get("external_dn") or "").strip().lower()
        if dn:
            by_dn[dn] = g
    children: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for g in groups:
        own = (g.get("external_dn") or "").strip().lower()
        pdn = (g.get("parent_dn") or "").strip().lower()
        if pdn and pdn in by_dn and pdn != own:
            children.setdefault(pdn, []).append(g)
        else:
            roots.append(g)
    out: list[dict] = []
    visited: set = set()

    def emit(g: dict, depth: int) -> None:
        if g["id"] in visited:
            return
        visited.add(g["id"])
        g2 = dict(g)
        g2["depth"] = depth
        out.append(g2)
        own = (g.get("external_dn") or "").strip().lower()
        for c in sorted(children.get(own, []), key=lambda x: (x.get("name") or "")):
            emit(c, depth + 1)

    for r in sorted(roots, key=lambda x: (x.get("source") or "", x.get("name") or "")):
        emit(r, 0)
    for g in groups:                       # orphaned by a cycle → surface at root
        if g["id"] not in visited:
            g2 = dict(g)
            g2["depth"] = 0
            out.append(g2)
            visited.add(g["id"])
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
