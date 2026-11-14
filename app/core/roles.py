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
    "pdf-border", "pdf-bookmark", "pdf-page-size", "pdf-nup", "pdf-compress",
            "doc-straighten",
    "pdf-watermark",
    "pdf-extract-text", "pdf-extract-images", "pdf-attachments",
    "office-to-pdf", "office-convert", "pdf-to-image", "image-to-pdf", "scan-merge",
    "pdf-encrypt", "pdf-decrypt", "pdf-metadata",
    "pdf-hidden-scan", "doc-diff", "text-diff", "doc-deident", "text-deident",
    "pdf-editor", "translate-doc", "doc-translate", "pdf-ocr", "text-list", "einvoice-scan", "transit-proof", "business-card-reader",
    "vat-lookup", "pdf-to-office", "pdf-to-slides", "pdf-to-markdown", "markdown-to-doc",
    "pdf-wordcount", "submission-check", "meeting-summary", "meeting-transcribe",
    "official-doc",
    "pdf-annotations", "pdf-annotations-flatten", "pdf-annotations-strip",
    # Sensitive — not in default-user; granted explicitly by finance/sales.
    # "pdf-fill", "pdf-stamp",
    # 事務機掃描取件：基本工具，所有內建角色都有（v1.17.1 起；
    # 原本只給文管）。伺服器只連內網位址與網頁埠，管理員另外可以關掉「使用者自行新增事務機」。
    "mfp-fetch",
]


#: 刻意不放進任何內建角色、只有管理員能用（由管理員在權限設定個別開放）的工具。一定要寫理由。
#: 未啟用登入（單機）時沒有權限檢查，不受影響。
ADMIN_ONLY_TOOL_IDS: dict[str, str] = {
    "paper-backup": "加密紙本備份處理重要秘密（使用者 2026-11-09：預設不給所有人，單機版不受此限）",
}


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
            "pdf-border", "pdf-bookmark", "pdf-page-size", "pdf-nup", "pdf-compress",
            "doc-straighten",
            "office-to-pdf", "office-convert", "pdf-to-image",
            "image-to-pdf", "scan-merge", "pdf-to-office", "pdf-to-slides",
            # 事務機掃描取件：所有內建角色都有（見上面 _NON_ADMIN_TOOL_IDS 的註解）
            "mfp-fetch",
        ],
    },
    {
        "id": "finance",
        "display_name": "財務",
        # 說明必須與實際授權一致 —— 原本寫「+ 浮水印 / 加密 / 去識別化」，
        # 但那三個本來就在一般使用者裡，讀起來會以為這個角色比較窄，其實是
        # 「一般使用者的全部 + 兩個敏感工具」。`tests/test_roles_rbac.py` 會擋。
        "description": "一般使用者的全部工具，另加表單填寫與用印簽名（敏感）",
        "is_builtin": True,
        "is_protected": False,
        # 只列「一般使用者沒有的」—— 其餘重複列出沒有作用，只會讓人以為
        # 這幾個是這個角色的特色。
        "tools": list(_NON_ADMIN_TOOL_IDS) + ["pdf-fill", "pdf-stamp", "pdf-seam-stamp"],
    },
    {
        "id": "sales",
        "display_name": "業務",
        "description": "一般使用者的全部工具，另加表單填寫與用印簽名（敏感）",
        "is_builtin": True,
        "is_protected": False,
        "tools": list(_NON_ADMIN_TOOL_IDS) + ["pdf-fill", "pdf-stamp", "pdf-seam-stamp"],
    },
    {
        "id": "legal-sec",
        "display_name": "法務資安",
        # 這個角色原本寫成 `_NON_ADMIN_TOOL_IDS + [七個]`，而那七個**全部**已經
        # 在一般使用者裡 → 指派這個角色與指派「一般使用者」完全等價，等於這個
        # 角色什麼都沒做，而名稱與說明卻讓人以為它是收窄的專用角色。
        #
        # 改成名實相符的窄清單（同 clerk 的作法）。**既有安裝不會被收窄** ——
        # `seed_builtin_roles()` 只補不減，`role_seed_snapshot` 的差集是空的，
        # 所以已上線的客戶維持現狀（權限只增不減是刻意的設計）。新安裝則拿到
        # 符合最小權限原則的版本。
        "description": "法務 / 資安檢視用：去識別化、隱藏內容掃描、Metadata、差異比對、加密解密",
        "is_builtin": True,
        "is_protected": False,
        "tools": [
            "doc-deident", "text-deident",
            "pdf-hidden-scan", "pdf-metadata", "doc-diff", "text-diff",
            "pdf-encrypt", "pdf-decrypt",
            # 檢視文件內容必備的基本能力（沒有這些連要看的檔案都打不開）
            "pdf-extract-text", "pdf-extract-images", "pdf-attachments",
            "pdf-merge", "pdf-split", "pdf-pages", "pdf-to-image",
            "mfp-fetch",
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

    For roles that ALREADY exist, top-up ONLY with tools that this release
    NEWLY added to SEED_ROLES since the last seed — computed against the
    per-role `role_seed_snapshot` (= the seed definition we recorded last
    time). Tools that admin has deliberately REMOVED from a built-in role are
    NOT re-added, because they're already in the snapshot (not "newly
    introduced"). Admin's own ADDITIONS (tools not in SEED_ROLES) are likewise
    never touched. See auth_db `_m9_role_seed_snapshot`.

    This fixes two things at once:
      1. "升級後新工具沒人看得見" — genuinely new tools DO propagate to
         existing customers' built-in roles.
      2. "改過預設角色的權限，升級又被改回來" — admin's removals now persist.

    Bootstrap (first run after the snapshot table lands): a built-in role with
    NO snapshot rows yet is treated conservatively — we record the current seed
    definition as the baseline and add NOTHING this run (so pre-existing admin
    removals survive). New tools introduced in that same release rely on an
    explicit backfill migration (the m4/m5 pattern), same as before.
    """
    conn = auth_db.conn()
    now = time.time()
    with db.tx(conn):
        for r in SEED_ROLES:
            row = conn.execute("SELECT 1 FROM roles WHERE id=?", (r["id"],)).fetchone()
            seed_tools = set(r["tools"])
            if row:
                # Existing role — diff this release's seed def against the
                # snapshot of the seed def we last recorded.
                snapshot = {x["tool_id"] for x in conn.execute(
                    "SELECT tool_id FROM role_seed_snapshot WHERE role_id=?",
                    (r["id"],)).fetchall()}
                if snapshot:
                    current = {x["tool_id"] for x in conn.execute(
                        "SELECT tool_id FROM role_perms WHERE role_id=?", (r["id"],)
                    ).fetchall()}
                    newly_introduced = seed_tools - snapshot
                    for tool_id in newly_introduced:
                        if tool_id not in current:
                            conn.execute(
                                "INSERT OR IGNORE INTO role_perms(role_id, tool_id) "
                                "VALUES (?,?)", (r["id"], tool_id))
                # else: no snapshot yet → bootstrap, add nothing this run.
                # Refresh the snapshot to this release's seed definition either
                # way, so the next upgrade diffs against the right baseline.
                # 說明如果還是**以前出廠的那一段**就換成這一版的（見
                # `_LEGACY_DESCRIPTIONS`）。管理員改過的不動。
                legacy = _LEGACY_DESCRIPTIONS.get(r["id"])
                if legacy:
                    cur_desc = conn.execute(
                        "SELECT description FROM roles WHERE id=?",
                        (r["id"],)).fetchone()
                    if cur_desc and cur_desc["description"] in legacy:
                        conn.execute("UPDATE roles SET description=? WHERE id=?",
                                     (r["description"], r["id"]))
                conn.execute("DELETE FROM role_seed_snapshot WHERE role_id=?",
                             (r["id"],))
                for tool_id in seed_tools:
                    conn.execute(
                        "INSERT OR IGNORE INTO role_seed_snapshot(role_id, tool_id) "
                        "VALUES (?,?)", (r["id"], tool_id))
                continue
            # Brand-new role — insert metadata + tools + snapshot baseline.
            conn.execute(
                "INSERT INTO roles(id, display_name, description, is_builtin, "
                "is_protected, created_at) VALUES (?,?,?,?,?,?)",
                (r["id"], r["display_name"], r["description"],
                 1 if r["is_builtin"] else 0, 1 if r["is_protected"] else 0, now),
            )
            for tool_id in seed_tools:
                conn.execute("INSERT OR IGNORE INTO role_perms(role_id, tool_id) "
                             "VALUES (?,?)", (r["id"], tool_id))
                conn.execute("INSERT OR IGNORE INTO role_seed_snapshot(role_id, "
                             "tool_id) VALUES (?,?)", (r["id"], tool_id))
    # Ensure exactly one role is flagged as the new-user default (default-user
    # unless admin has picked another). Kept out of the migration because the
    # roles rows may not exist at migration time on a fresh install.
    _ensure_default_role_flag()
    # Invalidate the in-memory permissions cache so changes take effect immediately
    try:
        from . import permissions as _perm
        _perm.invalidate_cache()
    except Exception:
        pass


# ---------- new-user default role ----------

# Roles that must NEVER be the auto-assigned default for brand-new users:
# admin (would auto-grant full access) and auditor (separation-of-duties +
# forced 2FA; JIT-provisioned users shouldn't land there).
_INELIGIBLE_DEFAULT_ROLES = frozenset({"admin", "auditor"})

FALLBACK_DEFAULT_ROLE = "default-user"


def _ensure_default_role_flag() -> None:
    """Guarantee exactly one role has is_default_for_new=1. If none does
    (fresh install / just-migrated existing DB), pick `default-user`."""
    conn = auth_db.conn()
    row = conn.execute(
        "SELECT id FROM roles WHERE is_default_for_new=1 LIMIT 1").fetchone()
    if row:
        return
    exists = conn.execute("SELECT 1 FROM roles WHERE id=?",
                          (FALLBACK_DEFAULT_ROLE,)).fetchone()
    if exists:
        with db.tx(conn):
            conn.execute("UPDATE roles SET is_default_for_new=1 WHERE id=?",
                         (FALLBACK_DEFAULT_ROLE,))


def get_default_role_id() -> str:
    """Return the role id assigned to brand-new users who aren't given an
    explicit role. Falls back to `default-user` if the flagged role was
    somehow deleted or no flag is set."""
    conn = auth_db.conn()
    row = conn.execute(
        "SELECT id FROM roles WHERE is_default_for_new=1 LIMIT 1").fetchone()
    if row:
        return row["id"]
    # Self-heal if possible, then fall back.
    _ensure_default_role_flag()
    row = conn.execute(
        "SELECT id FROM roles WHERE is_default_for_new=1 LIMIT 1").fetchone()
    return row["id"] if row else FALLBACK_DEFAULT_ROLE


def set_default_role_id(role_id: str) -> None:
    """Mark `role_id` as the new-user default (clears the flag on every other
    role). Rejects admin/auditor and non-existent roles."""
    role_id = (role_id or "").strip()
    if role_id in _INELIGIBLE_DEFAULT_ROLES:
        raise ValueError("admin / auditor 角色不可設為新使用者預設角色")
    conn = auth_db.conn()
    if not conn.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone():
        raise ValueError(f"role 「{role_id}」不存在")
    with db.tx(conn):
        conn.execute("UPDATE roles SET is_default_for_new=0 "
                     "WHERE is_default_for_new=1")
        conn.execute("UPDATE roles SET is_default_for_new=1 WHERE id=?",
                     (role_id,))


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
        "SELECT id, display_name, description, is_builtin, is_protected, "
        "is_default_for_new, created_at "
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
            "is_default_for_new": bool(r["is_default_for_new"]),
            # 刻意**不**回 created_at —— 這份結果會被整包序列化進管理頁，
            # 原始 Unix 時間戳會被掃描器判為資訊洩漏（Low），而畫面上完全沒用到它。
            # 需要建立時間的地方請直接查 DB。
            "tools": tools,
        })
    return out


def get(role_id: str) -> Optional[dict]:
    for r in list_roles():
        if r["id"] == role_id:
            return r
    return None


#: **以前出廠過**的說明字串。`seed_builtin_roles()` 只做 INSERT OR IGNORE，
#: 所以說明在既有安裝裡永遠停在當初那一版 —— 而 finance / sales / legal-sec
#: 這三段當初正是**因為描述了角色其實沒有的權限**才被改掉的（見 SEED_ROLES 的
#: 註解）。管理員在指派角色時讀到的是錯的說明，而且那些字串不在語系檔裡，
#: 英文介面下也翻不出來（2026-09-06 使用者截圖回報）。
#:
#: **只有值還完全等於某個舊出廠值時才更新** —— 管理員自己改過的說明是他的資料。
_LEGACY_DESCRIPTIONS: dict[str, set[str]] = {
    "finance": {"一般使用者 + 表單填寫 / 用印與簽名 / 浮水印 / 加密 / 去識別化"},
    "sales": {"一般使用者 + 表單填寫 / 用印與簽名 / 浮水印 / 去識別化"},
    "legal-sec": {"一般使用者 + 去識別化 / 隱藏掃描 / Metadata / 差異比對 / 加密解密"},
}

#: 內建角色的**出廠**名稱與說明。判斷「這個角色有沒有被管理員改過名字」用。
_SEED_DISPLAY = {r["id"]: r["display_name"] for r in SEED_ROLES}
_SEED_DESC = {r["id"]: r["description"] for r in SEED_ROLES}


def localize_for_display(rows: list[dict], tr) -> list[dict]:
    """替每個角色補上**顯示用**的 `display_label` / `description_label`。

    判準是「**現在的值還等於出廠預設**」，不是「is_builtin」——因為四個內建
    角色（文管 / 財務 / 業務 / 法務資安）`is_protected=False`，**管理員可以改
    名字**。改過就是他的資料，任何語言都要照他寫的顯示；照 `is_builtin` 翻的話
    會把「會計部」變回「Finance」，那是把使用者的資料蓋掉。

    原本的 `display_name` / `description` **原封不動**：編輯表單一律用原值，
    否則英文介面下按一次儲存，資料庫裡的中文名稱就變成英文了
    （同 CLAUDE.md 記過的「`title:` 在別的地方是資料不是顯示文字」）。
    """
    out = []
    for r in rows:
        r = dict(r)
        rid = r.get("id")
        name, desc = r.get("display_name", ""), r.get("description", "")
        # **另外附欄位，不覆寫原值** —— 角色管理頁的重新命名表單要用原值，
        # 覆寫的話英文介面按一次儲存就把資料庫裡的中文名稱換成英文了。
        r["display_label"] = (tr(name) if rid in _SEED_DISPLAY
                              and name == _SEED_DISPLAY[rid] else name)
        r["description_label"] = (tr(desc) if rid in _SEED_DESC
                                  and desc == _SEED_DESC[rid] else desc)
        out.append(r)
    return out


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
    # 權限快取失效收進變更函式本身（defense-in-depth）：不再只靠端點層記得呼叫，
    # 未來任何 caller（CLI / 內部路徑）改角色工具都不會留下過度授權的 stale 視窗。
    from . import permissions as _perm
    _perm.invalidate_cache()


def delete(role_id: str) -> None:
    conn = auth_db.conn()
    row = conn.execute(
        "SELECT is_protected, is_builtin, is_default_for_new FROM roles WHERE id=?",
        (role_id,)).fetchone()
    if not row:
        raise ValueError(f"role 「{role_id}」不存在")
    if row["is_protected"]:
        raise ValueError("此角色受保護，無法刪除")
    if row["is_default_for_new"]:
        raise ValueError("此角色被設為新使用者預設角色，請先把預設改指向其他角色再刪除")
    with db.tx(conn):
        # CASCADE removes role_perms rows; subject_roles also CASCADE.
        conn.execute("DELETE FROM roles WHERE id=?", (role_id,))
    from . import permissions as _perm
    _perm.invalidate_cache()   # 刪除角色後立即失效權限快取
