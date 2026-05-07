"""Permission resolver: subject (user/group/OU) → roles → tools.

For each user request:
    effective_tools(user) = union(
        direct (user → tool),
        roles assigned directly to user,
        roles assigned to any group user is in,
        roles assigned to any OU user is under,
        direct (group/OU → tool) grants,
    )

Plus: if any of those resolved roles is `admin`, the answer is "all tools"
(admin bypass).

Cached in-memory per-user with invalidation on any role/permission change
(see `invalidate_cache`).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from . import auth_db, db

logger = logging.getLogger(__name__)


# ---------- assignment CRUD ----------

def assign_role(subject_type: str, subject_key: str, role_id: str) -> None:
    if subject_type not in ("user", "group", "ou"):
        raise ValueError("invalid subject_type")
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "INSERT OR IGNORE INTO subject_roles(subject_type, subject_key, role_id) "
            "VALUES (?,?,?)", (subject_type, subject_key, role_id),
        )
    invalidate_cache()
    # 職責分離：給 auditor 角色就一併把該 user 其他 role / 直接工具授權清掉
    if role_id == "auditor" and subject_type == "user":
        try:
            from . import roles as _roles
            _roles.enforce_auditor_isolation()
        except Exception:
            pass


def unassign_role(subject_type: str, subject_key: str, role_id: str) -> None:
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "DELETE FROM subject_roles WHERE subject_type=? AND subject_key=? "
            "AND role_id=?", (subject_type, subject_key, role_id),
        )
    invalidate_cache()


def set_subject_roles(subject_type: str, subject_key: str, role_ids: list[str]) -> None:
    """Replace the role set for a subject in one shot."""
    if subject_type not in ("user", "group", "ou"):
        raise ValueError("invalid subject_type")
    # 職責分離：auditor 不可和其他 role 並存。若 caller 同時送入 auditor +
    # 其他角色，silently 砍成只剩 auditor — 不靜默失敗也不丟例外（admin
    # 在 UI 同時勾兩個 role 也會走到這），讓最終 DB 狀態一致。
    if "auditor" in role_ids:
        role_ids = ["auditor"]
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "DELETE FROM subject_roles WHERE subject_type=? AND subject_key=?",
            (subject_type, subject_key),
        )
        for rid in role_ids:
            conn.execute(
                "INSERT OR IGNORE INTO subject_roles(subject_type, subject_key, role_id) "
                "VALUES (?,?,?)", (subject_type, subject_key, rid),
            )
    invalidate_cache()
    if subject_type == "user" and "auditor" in role_ids:
        try:
            from . import roles as _roles
            _roles.enforce_auditor_isolation()
        except Exception:
            pass


def grant_tool(subject_type: str, subject_key: str, tool_id: str) -> None:
    """Direct subject→tool grant (advanced; usually use roles)."""
    if subject_type not in ("user", "group", "ou"):
        raise ValueError("invalid subject_type")
    # 職責分離：auditor user 不可有任何直接工具授權
    if subject_type == "user":
        conn0 = auth_db.conn()
        is_aud = conn0.execute(
            "SELECT 1 FROM subject_roles WHERE subject_type='user' "
            "AND subject_key=? AND role_id='auditor'",
            (str(subject_key),),
        ).fetchone()
        if is_aud:
            raise ValueError(
                "稽核員角色不得直接指派工具（職責分離）。請先移除該使用者的 "
                "稽核員角色，再進行工具指派。")
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "INSERT OR IGNORE INTO subject_perms(subject_type, subject_key, tool_id) "
            "VALUES (?,?,?)", (subject_type, subject_key, tool_id),
        )
    invalidate_cache()


def revoke_tool(subject_type: str, subject_key: str, tool_id: str) -> None:
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "DELETE FROM subject_perms WHERE subject_type=? AND subject_key=? "
            "AND tool_id=?", (subject_type, subject_key, tool_id),
        )
    invalidate_cache()


def list_roles_for_subject(subject_type: str, subject_key: str) -> list[str]:
    conn = auth_db.conn()
    rows = conn.execute(
        "SELECT role_id FROM subject_roles WHERE subject_type=? AND subject_key=? "
        "ORDER BY role_id", (subject_type, subject_key),
    ).fetchall()
    return [r["role_id"] for r in rows]


def list_direct_tools_for_subject(subject_type: str, subject_key: str) -> list[str]:
    conn = auth_db.conn()
    rows = conn.execute(
        "SELECT tool_id FROM subject_perms WHERE subject_type=? AND subject_key=? "
        "ORDER BY tool_id", (subject_type, subject_key),
    ).fetchall()
    return [r["tool_id"] for r in rows]


# ---------- effective resolver ----------

# In-memory cache: user_id (int) → (effective_tools_set | "ALL", expires_at)
_CACHE: dict[int, tuple] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 60.0   # seconds; cleared on any perm change


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _user_groups_local(conn, user_id: int) -> list[str]:
    """Return group_id (as text) for every local group this user belongs to."""
    rows = conn.execute(
        "SELECT group_id FROM group_members WHERE user_id=?", (user_id,)
    ).fetchall()
    return [str(r["group_id"]) for r in rows]


def _user_external_subjects(conn, user_id: int) -> list[tuple[str, str]]:
    """Return (subject_type, subject_key) for OU subjects that derive from
    the user's external_dn (AD/LDAP). Group memberships from AD are mirrored
    into local `groups` + `group_members` tables at login time, so they're
    already covered by the regular group lookup; we only need to add OU
    ancestors here."""
    row = conn.execute(
        "SELECT external_dn FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not row or not row["external_dn"]:
        return []
    try:
        from . import auth_ldap
        return auth_ldap.get_ou_subjects_for_dn(row["external_dn"])
    except Exception:
        return []


def effective_tools(user_id: int) -> set[str] | str:
    """Return either the set of allowed tool ids, or the string ``"ALL"``
    if the user has the admin role (full access bypass)."""
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(user_id)
        if cached and cached[1] > now:
            return cached[0]

    conn = auth_db.conn()
    # Subjects this user "is": user itself + local groups + (later) AD groups/OUs
    subjects: list[tuple[str, str]] = [("user", str(user_id))]
    for gid in _user_groups_local(conn, user_id):
        subjects.append(("group", gid))
    subjects.extend(_user_external_subjects(conn, user_id))

    # All roles assigned to any of these subjects
    role_ids: set[str] = set()
    direct_tools: set[str] = set()
    for st, sk in subjects:
        for r in conn.execute(
            "SELECT role_id FROM subject_roles WHERE subject_type=? AND subject_key=?",
            (st, sk)
        ).fetchall():
            role_ids.add(r["role_id"])
        for r in conn.execute(
            "SELECT tool_id FROM subject_perms WHERE subject_type=? AND subject_key=?",
            (st, sk)
        ).fetchall():
            direct_tools.add(r["tool_id"])

    # Auditor role is a HARD WALL — separation of duties wins over
    # everything else. Even if some upstream code path (group / OU /
    # direct perm / coexisting admin role) would have granted tools,
    # an auditor gets ZERO tools. This catches the
    #   auditor role (direct) + admin role (via group)
    # bypass scenario where group-level admin would otherwise win.
    if "auditor" in role_ids:
        result: set[str] | str = set()
    # Admin role short-circuit (only if auditor isn't present)
    elif "admin" in role_ids:
        result = "ALL"
    else:
        tools: set[str] = set(direct_tools)
        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            for r in conn.execute(
                f"SELECT DISTINCT tool_id FROM role_perms WHERE role_id IN ({placeholders})",
                tuple(role_ids)
            ).fetchall():
                tools.add(r["tool_id"])
        result = tools

    with _CACHE_LOCK:
        _CACHE[user_id] = (result, now + _CACHE_TTL)
    return result


def user_can_use_tool(user_id: int, tool_id: str) -> bool:
    et = effective_tools(user_id)
    if et == "ALL":
        return True
    return tool_id in et


def is_admin(user_id: int) -> bool:
    """Convenience: true iff this user has the `admin` role."""
    return effective_tools(user_id) == "ALL"


def list_roles_for_subject(subject_type: str, subject_key: str) -> list[str]:
    """Return list of role_ids assigned to (subject_type, subject_key).
    Re-export here as a thin alias for callers that don't want to import
    from the lower-level module — and to keep is_auditor() local."""
    conn = auth_db.conn()
    rows = conn.execute(
        "SELECT role_id FROM subject_roles WHERE subject_type=? AND subject_key=?",
        (subject_type, str(subject_key)),
    ).fetchall()
    return [r["role_id"] for r in rows]


def is_auditor(user_id: int) -> bool:
    """True iff user has the `auditor` role (direct or via local group).
    Used to gate audit / history / uploads / system-status admin pages
    so non-admin auditors can read those without getting full admin
    powers (separation of duties / mail-archive style compliance)."""
    if not user_id:
        return False
    # Direct user assignment
    if "auditor" in list_roles_for_subject("user", str(user_id)):
        return True
    # Via local groups — inline query to avoid pulling group_manager
    try:
        conn = auth_db.conn()
        rows = conn.execute(
            "SELECT gm.group_id FROM group_members gm WHERE gm.user_id=?",
            (int(user_id),),
        ).fetchall()
        for r in rows:
            if "auditor" in list_roles_for_subject("group", str(r["group_id"])):
                return True
    except Exception:
        pass
    return False
