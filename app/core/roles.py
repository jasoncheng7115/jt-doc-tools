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
    "pdf-hidden-scan", "doc-diff", "text-diff", "doc-deident", "text-deident",
    "pdf-editor", "translate-doc", "pdf-ocr",
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
            "pdf-encrypt", "doc-deident", "text-deident",
        ],
    },
    {
        "id": "sales",
        "display_name": "業務",
        "description": "一般使用者 + 表單填寫 / 用印與簽名 / 浮水印 / 去識別化",
        "is_builtin": True,
        "is_protected": False,
        "tools": list(_NON_ADMIN_TOOL_IDS) + [
            "pdf-fill", "pdf-stamp", "pdf-watermark",
            "doc-deident", "text-deident",
        ],
    },
    {
        "id": "legal-sec",
        "display_name": "法務資安",
        "description": "一般使用者 + 去識別化 / 隱藏掃描 / Metadata / 差異比對 / 加密解密",
        "is_builtin": True,
        "is_protected": False,
        "tools": list(_NON_ADMIN_TOOL_IDS) + [
            "doc-deident", "text-deident",
            "pdf-hidden-scan", "pdf-metadata", "doc-diff",
            "pdf-encrypt", "pdf-decrypt",
        ],
    },
    {
        # 稽核員 — v1.4.99 起新增。職責分離：admin 管系統 / 工具，auditor
        # 看稽核紀錄與歷史檔案。auditor 沒任何工具，也看不到 admin 設定區
        # （除了稽核 / 歷史 / 上傳記錄 / 系統狀態）。本機帳號專用，強制 TOTP
        # 2FA。is_protected=True 不可刪也不可改（避免誤把 audit 角色變廢）。
        "id": "auditor",
        "display_name": "稽核員",
        "description": "唯讀存取稽核紀錄與檔案歷史（不可使用工具、不可改設定）。本機帳號 + 強制 2FA。",
        "is_builtin": True,
        "is_protected": True,
        "tools": [],   # 沒有工具權限；稽核相關 admin 頁靠 is_auditor() 額外開放
    },
]


# v1.5.0 起：稽核員「能」看的 admin 頁清單。實際 access control 走
# `app/web/deps.py:_AUDITOR_SHARED_PREFIXES` + `_AUDITOR_EXCLUSIVE_PREFIXES`
# 兩組（admin 只能看 SHARED；EXCLUSIVE 連 admin 都擋）。這個 constant 保留
# 給 search keyword / 老 import 相容用。
AUDIT_VISIBLE_ADMIN_URLS = (
    "/admin/audit",
    "/admin/history",
    "/admin/uploads",
    "/admin/system-status",
)


# ---------- seed on startup ----------

def seed_builtin_roles() -> None:
    """Insert built-in roles + their tool grants if not already present.
    For roles that ALREADY exist, top-up with any new tools that have been
    added to SEED_ROLES since the last seed (e.g. when a new release adds
    a tool that should be available to default-user / finance / etc.).
    Only ADDs — never removes a tool, so admin's prior 'unselect' edits
    can be reversed by upgrade if a new release adds it back, but admin's
    own ADDITIONS to built-in roles are preserved across upgrades.

    This is what fixes the "升級後新工具沒人看得見" bug — without this
    top-up, customers who installed BEFORE a new tool was introduced never
    got the tool in their built-in roles, even though new SEED_ROLES code
    listed it.
    """
    conn = auth_db.conn()
    now = time.time()
    with db.tx(conn):
        for r in SEED_ROLES:
            row = conn.execute("SELECT 1 FROM roles WHERE id=?", (r["id"],)).fetchone()
            if row:
                # Existing role — top up with any tools added since last seed.
                # Read what's currently granted, compute diff, INSERT OR IGNORE
                # the rest. Tools removed by admin won't come back here unless
                # they're in SEED_ROLES (then they return — accepted trade-off
                # so new tools propagate to existing customers).
                current = {x["tool_id"] for x in conn.execute(
                    "SELECT tool_id FROM role_perms WHERE role_id=?", (r["id"],)
                ).fetchall()}
                for tool_id in r["tools"]:
                    if tool_id not in current:
                        conn.execute(
                            "INSERT OR IGNORE INTO role_perms(role_id, tool_id) "
                            "VALUES (?,?)", (r["id"], tool_id))
                continue
            # Brand-new role — insert metadata + tools.
            conn.execute(
                "INSERT INTO roles(id, display_name, description, is_builtin, "
                "is_protected, created_at) VALUES (?,?,?,?,?,?)",
                (r["id"], r["display_name"], r["description"],
                 1 if r["is_builtin"] else 0, 1 if r["is_protected"] else 0, now),
            )
            for tool_id in r["tools"]:
                conn.execute("INSERT OR IGNORE INTO role_perms(role_id, tool_id) "
                             "VALUES (?,?)", (r["id"], tool_id))
    # Invalidate the in-memory permissions cache so changes take effect immediately
    try:
        from . import permissions as _perm
        _perm.invalidate_cache()
    except Exception:
        pass


# 內建稽核員帳號名稱 — 升級時若不存在會自動建立（v1.5.0 起）
DEFAULT_AUDITOR_USERNAME = "jtdt-auditor"


def seed_default_auditor_user() -> bool:
    """確保內建本機稽核員帳號 `jtdt-auditor` 存在。

    場景：
      - 全新安裝（auth ON 後）：第一次啟動建立此帳號，admin 用
        `sudo jtdt reset-password jtdt-auditor` 設密碼後即可使用
      - 既有客戶升級（沒有任何 auditor）：升上 v1.5.0 自動補建，**不會**
        覆蓋使用者已自建的 jtdt-auditor 帳號（INSERT OR IGNORE 由 username
        unique 把關）
      - admin 已用 `jtdt audit-user create xxx` 建過自訂稽核員：仍會建
        jtdt-auditor，因為使用者明確說「**如果本來沒有 jtdt-auditor 要自動
        建立**」

    建立的 row 設定：
      - source='local'
      - password_hash=NULL → login 直接被 verify_password 拒，必須先
        `jtdt reset-password jtdt-auditor` 才能用
      - totp_required=1（auditor role + DB 欄位雙重保險）
      - is_audit_seed=1 → user_manager.delete 拒絕刪除
      - 自動指派 'auditor' role
      - 寫一筆 `audit_seed_create` audit event

    Returns: True 若這次有新建，False 若已存在不動。
    """
    from . import auth_db, audit_db
    conn = auth_db.conn()
    row = conn.execute(
        "SELECT id FROM users WHERE username=? AND source='local'",
        (DEFAULT_AUDITOR_USERNAME,),
    ).fetchone()
    if row:
        # 已存在 — 確保 auditor role 至少有指派（避免 admin 不小心移走後沒救）
        uid = row["id"]
        has_role = conn.execute(
            "SELECT 1 FROM subject_roles WHERE subject_type='user' "
            "AND subject_key=? AND role_id='auditor'",
            (str(uid),),
        ).fetchone()
        if not has_role:
            with db.tx(conn):
                conn.execute(
                    "INSERT OR IGNORE INTO subject_roles(subject_type, "
                    "subject_key, role_id) VALUES ('user', ?, 'auditor')",
                    (str(uid),),
                )
        return False
    # 新建
    now = time.time()
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO users(username, display_name, password_hash, source, "
            "enabled, is_admin_seed, is_audit_seed, created_at, last_login_at, "
            "totp_secret, totp_enabled, totp_required) "
            "VALUES (?, ?, NULL, 'local', 1, 0, 1, ?, 0, NULL, 0, 1)",
            (DEFAULT_AUDITOR_USERNAME, "稽核員", now),
        )
        uid = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO subject_roles(subject_type, subject_key, "
            "role_id) VALUES ('user', ?, 'auditor')",
            (str(uid),),
        )
    try:
        audit_db.log_event(
            "audit_seed_create", username="system", target=DEFAULT_AUDITOR_USERNAME,
            details={"reason": "auto-create on startup",
                     "next_step": "sudo jtdt reset-password jtdt-auditor"},
        )
    except Exception:
        pass
    try:
        from . import permissions as _perm
        _perm.invalidate_cache()
    except Exception:
        pass
    return True


def enforce_auditor_isolation() -> dict:
    """職責分離強制執行：每個有 `auditor` 角色的 user，DB 內**不該有**其他
    角色、不該有直接工具授權（subject_perms）、且 totp_required 必為 1。

    跑時機：
      - 每次啟動（main.py startup）— 升級時把舊 DB 不一致的狀態清乾淨
      - admin 透過 /admin/permissions 把 auditor 角色給某 user 之後
        （permissions.assign_role 內 hook）

    清掉的資料寫進 audit log，admin 看得到稽核員身上原本還有什麼權限。

    Returns dict: {users_cleaned, roles_removed, perms_removed, totp_forced}
    """
    from . import auth_db, audit_db, db, totp as _totp
    conn = auth_db.conn()
    summary = {
        "users_cleaned": 0,
        "roles_removed": [],     # list of (user_id, role_id)
        "perms_removed": [],     # list of (user_id, tool_id)
        "totp_forced": [],       # list of user_id
    }
    auditor_uids = [
        r["subject_key"] for r in conn.execute(
            "SELECT subject_key FROM subject_roles "
            "WHERE subject_type='user' AND role_id='auditor'"
        ).fetchall()
    ]
    if not auditor_uids:
        return summary
    for uid_str in auditor_uids:
        try:
            uid = int(uid_str)
        except (TypeError, ValueError):
            continue
        # 1. 找出這個 user 還掛著哪些**其他**角色
        other_roles = [
            r["role_id"] for r in conn.execute(
                "SELECT role_id FROM subject_roles "
                "WHERE subject_type='user' AND subject_key=? AND role_id<>'auditor'",
                (uid_str,),
            ).fetchall()
        ]
        # 2. 直接 subject_perms（admin 用「進階」拐角繞 role 直接給工具）
        direct_perms = [
            r["tool_id"] for r in conn.execute(
                "SELECT tool_id FROM subject_perms "
                "WHERE subject_type='user' AND subject_key=?", (uid_str,),
            ).fetchall()
        ]
        # 3. totp_required 狀態
        urow = conn.execute(
            "SELECT username, totp_required FROM users WHERE id=?", (uid,)
        ).fetchone()
        username = urow["username"] if urow else f"user_id={uid}"
        needs_totp = bool(urow and not urow["totp_required"])
        if not (other_roles or direct_perms or needs_totp):
            continue   # 已經乾淨
        with db.tx(conn):
            if other_roles:
                conn.execute(
                    "DELETE FROM subject_roles "
                    "WHERE subject_type='user' AND subject_key=? AND role_id<>'auditor'",
                    (uid_str,),
                )
            if direct_perms:
                conn.execute(
                    "DELETE FROM subject_perms "
                    "WHERE subject_type='user' AND subject_key=?", (uid_str,),
                )
            if needs_totp:
                # Inline UPDATE — _totp.set_required() opens its own
                # transaction, which would nest inside this `with db.tx`
                # block (sqlite refuses).
                conn.execute(
                    "UPDATE users SET totp_required=1 WHERE id=?", (uid,))
        for r in other_roles:
            summary["roles_removed"].append((uid, r))
        for t in direct_perms:
            summary["perms_removed"].append((uid, t))
        if needs_totp:
            summary["totp_forced"].append(uid)
        summary["users_cleaned"] += 1
        try:
            audit_db.log_event(
                "auditor_isolation_cleanup",
                username="system", target=username,
                details={
                    "user_id": uid,
                    "removed_roles": other_roles,
                    "removed_direct_tools": direct_perms,
                    "totp_forced": needs_totp,
                },
            )
        except Exception:
            pass
    if summary["users_cleaned"]:
        try:
            from . import permissions as _perm
            _perm.invalidate_cache()
        except Exception:
            pass
    return summary


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
            # auditor role MUST have empty tools (separation-of-duties) —
            # silently no-op even if admin POSTs a non-empty list.
            if role_id in ("admin", "auditor"):
                pass
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
