"""Role catalogue + CRUD + 6 built-in role seeding.

A role is a stable text id (`'admin'`, `'clerk'`, …) plus a Chinese display
name plus a set of granted tool ids. Assignments to subjects (user / group /
OU) live in `subject_roles` (handled in `permissions.py`).

Built-in roles:

| id              | display      | protected | builtin |
| --------------- | ------------ | --------- | ------- |
| admin           | 管理員        | ✓         | ✓       |  full access (perms ignored — admin always wins)
| default-user    | 一般使用者    | ✓ (perms editable, name/delete locked) | ✓ |  most tools sans pdf-fill / pdf-stamp
| clerk           | 文管          |           | ✓       |  document management subset
| finance         | 財務          |           | ✓       |  default-user + signing/encryption tools
| sales           | 業務          |           | ✓       |  default-user + signing tools
| legal-sec       | 法務資安      |           | ✓       |  default-user + redaction/decrypt
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from . import auth_db, db

logger = logging.getLogger(__name__)


# ---- The default tool grants for each built-in role ----
# Tool ids are the registry ids (see app/tool_registry + each tool's
# metadata.id). 'admin' role intentionally has NO grants here — the perm
# check short-circuits to allow when the user has the admin role at all.
_NON_ADMIN_TOOL_IDS = [
    "pdf-merge", "pdf-split", "pdf-rotate", "pdf-pages", "pdf-pageno",
    "pdf-nup", "pdf-compress", "pdf-watermark",
    "pdf-extract-text", "pdf-extract-images", "pdf-attachments",
    "office-to-pdf", "pdf-to-image", "image-to-pdf",
    "pdf-encrypt", "pdf-decrypt", "pdf-metadata",
    "pdf-hidden-scan", "doc-diff", "text-diff", "doc-deident", "pdf-editor",
    # Sensitive — not in default-user; granted explicitly by finance/sales.
    # "pdf-fill", "pdf-stamp",
]


SEED_ROLES: list[dict] = [
    {
        "id": "admin",
        "display_name": "管理員",
        "description": "完整權限，包含設定區與所有工具",
        "is_builtin": True,
        "is_protected": True,
        "tools": [],   # special: empty list = "all" (admin bypass in resolver)
    },
    {
        "id": "default-user",
        "display_name": "一般使用者",
        "description": "新使用者預設套用；含大部分工具，不含表單填寫 / 用印與簽名",
        "is_builtin": True,
        "is_protected": True,
        "tools": list(_NON_ADMIN_TOOL_IDS),
    },
    {
        "id": "clerk",
        "display_name": "文管",
        "description": "文件管理常用：擷取 / 合併 / 拆分 / 轉檔 / 整理頁面",
        "is_builtin": True,
        "is_protected": False,
        "tools": [
            "pdf-extract-text", "pdf-extract-images", "pdf-attachments",
            "pdf-merge", "pdf-split", "pdf-pages", "pdf-rotate", "pdf-pageno",
            "pdf-nup", "pdf-compress", "office-to-pdf", "pdf-to-image",
            "image-to-pdf",
        ],
    },
    {
        "id": "finance",
        "display_name": "財務",
        "description": "一般使用者 + 表單填寫 / 用印與簽名 / 浮水印 / 加密 / 去識別化",
        "is_builtin": True,
        "is_protected": False,
        "tools": list(_NON_ADMIN_TOOL_IDS) + [
            "pdf-fill", "pdf-stamp", "pdf-watermark",
            "pdf-encrypt", "doc-deident",
        ],
    },
    {
        "id": "sales",
        "display_name": "業務",
        "description": "一般使用者 + 表單填寫 / 用印與簽名 / 浮水印 / 去識別化",
        "is_builtin": True,
        "is_protected": False,
        "tools": list(_NON_ADMIN_TOOL_IDS) + [
            "pdf-fill", "pdf-stamp", "pdf-watermark", "doc-deident",
        ],
    },
    {
        "id": "legal-sec",
        "display_name": "法務資安",
        "description": "一般使用者 + 去識別化 / 隱藏掃描 / Metadata / 差異比對 / 加密解密",
        "is_builtin": True,
        "is_protected": False,
        "tools": list(_NON_ADMIN_TOOL_IDS) + [
            "doc-deident", "pdf-hidden-scan", "pdf-metadata", "doc-diff",
            "pdf-encrypt", "pdf-decrypt",
        ],
    },
]


# ---------- seed on startup ----------

def seed_builtin_roles() -> None:
    """Insert built-in roles + their tool grants if not already present.
    Existing role rows (e.g. from a previous boot, possibly admin-edited)
    are left alone so admin's customisations persist across restarts."""
    conn = auth_db.conn()
    now = time.time()
    with db.tx(conn):
        for r in SEED_ROLES:
            row = conn.execute("SELECT 1 FROM roles WHERE id=?", (r["id"],)).fetchone()
            if row:
                continue
            conn.execute(
                "INSERT INTO roles(id, display_name, description, is_builtin, "
                "is_protected, created_at) VALUES (?,?,?,?,?,?)",
                (r["id"], r["display_name"], r["description"],
                 1 if r["is_builtin"] else 0, 1 if r["is_protected"] else 0, now),
            )
            for tool_id in r["tools"]:
                conn.execute("INSERT OR IGNORE INTO role_perms(role_id, tool_id) "
                             "VALUES (?,?)", (r["id"], tool_id))


# ---------- CRUD ----------

def list_roles() -> list[dict]:
    """Return every role with its tool grants."""
    conn = auth_db.conn()
    rows = conn.execute(
        "SELECT id, display_name, description, is_builtin, is_protected, created_at "
        "FROM roles ORDER BY is_builtin DESC, id"
    ).fetchall()
    out = []
    for r in rows:
        tools = [x["tool_id"] for x in conn.execute(
            "SELECT tool_id FROM role_perms WHERE role_id=? ORDER BY tool_id",
            (r["id"],)).fetchall()]
        out.append({
            "id": r["id"], "display_name": r["display_name"],
            "description": r["description"], "is_builtin": bool(r["is_builtin"]),
            "is_protected": bool(r["is_protected"]),
            "created_at": r["created_at"], "tools": tools,
        })
    return out


def get(role_id: str) -> Optional[dict]:
    for r in list_roles():
        if r["id"] == role_id:
            return r
    return None


def create(role_id: str, display_name: str, description: str = "",
           tools: Optional[list[str]] = None) -> None:
    role_id = (role_id or "").strip()
    if not role_id:
        raise ValueError("role id 不能空白")
    import re
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", role_id):
        raise ValueError("role id 只能用小寫英數加減號，2-31 字元，首字必須是字母")
    display_name = (display_name or "").strip() or role_id
    if len(display_name) > 64:
        raise ValueError("顯示名稱不得超過 64 字元")
    conn = auth_db.conn()
    if conn.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone():
        raise ValueError(f"role id 「{role_id}」已存在")
    with db.tx(conn):
        conn.execute(
            "INSERT INTO roles(id, display_name, description, is_builtin, "
            "is_protected, created_at) VALUES (?,?,?,0,0,?)",
            (role_id, display_name, description or "", time.time()),
        )
        for tool_id in (tools or []):
            conn.execute("INSERT OR IGNORE INTO role_perms(role_id, tool_id) "
                         "VALUES (?,?)", (role_id, tool_id))


def update(role_id: str, *, display_name: Optional[str] = None,
           description: Optional[str] = None,
           tools: Optional[list[str]] = None) -> None:
    """Update display name / description / tool grants. Built-in roles can
    have tools changed; only `is_protected` ones (admin, default-user) get
    their name/delete locked — handled by callers checking the flag."""
    conn = auth_db.conn()
    row = conn.execute("SELECT is_protected FROM roles WHERE id=?",
                       (role_id,)).fetchone()
    if not row:
        raise ValueError(f"role 「{role_id}」不存在")
    is_protected = bool(row["is_protected"])
    with db.tx(conn):
        if display_name is not None and not is_protected:
            display_name = display_name.strip()
            if not display_name:
                raise ValueError("顯示名稱不能空白")
            if len(display_name) > 64:
                raise ValueError("顯示名稱不得超過 64 字元")
            conn.execute("UPDATE roles SET display_name=? WHERE id=?",
                         (display_name, role_id))
        if description is not None:
            conn.execute("UPDATE roles SET description=? WHERE id=?",
                         ((description or "")[:500], role_id))
        if tools is not None:
            # admin role's tools are intentionally empty (means "all"); reject
            # any attempt to set tools for it.
            if role_id == "admin":
                pass  # silently no-op; admin role grants are implicit
            else:
                conn.execute("DELETE FROM role_perms WHERE role_id=?", (role_id,))
                for t in tools:
                    conn.execute("INSERT OR IGNORE INTO role_perms(role_id, tool_id) "
                                 "VALUES (?,?)", (role_id, t))


def delete(role_id: str) -> None:
    conn = auth_db.conn()
    row = conn.execute("SELECT is_protected, is_builtin FROM roles WHERE id=?",
                       (role_id,)).fetchone()
    if not row:
        raise ValueError(f"role 「{role_id}」不存在")
    if row["is_protected"]:
        raise ValueError("此角色受保護，無法刪除")
    with db.tx(conn):
        # CASCADE removes role_perms rows; subject_roles also CASCADE.
        conn.execute("DELETE FROM roles WHERE id=?", (role_id,))
