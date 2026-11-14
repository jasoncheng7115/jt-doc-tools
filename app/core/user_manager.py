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


def last_full_directory_scan_at() -> float:
    """上一次**完整**目錄同步的時間（epoch 秒）。沒有做過就回 0。

    「這個帳號在目錄裡已經找不到」只能相對於一次完整掃描來判定。帶了名稱過濾的
    同步只看得到一部分目錄，拿它當基準會把整個組織誤標成離職 —— 所以沒有完整
    掃描過的時候，這個功能一律**不下任何結論**。
    """
    try:
        from . import directory_sync
        return float(directory_sync.get_settings().get("last_full_scan_at") or 0)
    except Exception:  # noqa: BLE001 — 讀不到設定就當作沒掃過
        logger.exception("讀取上次完整目錄掃描時間失敗")
        return 0.0


def _is_missing(row, full_scan_at: float) -> bool:
    """這個帳號在上一次完整目錄掃描時是不是已經不存在了。

    只對目錄帳號有意義；本機與 SSO 帳號永遠回 False（它們本來就不在 AD 裡，
    標成「離職」會是徹底的誤報）。
    """
    if full_scan_at <= 0:
        return False
    if row["source"] not in ("ldap", "ad"):
        return False
    if not (row["external_dn"] or ""):
        return False
    return float(row["directory_seen_at"] or 0) < full_scan_at


#: 密碼到期前幾天開始提醒。
#: 兩週：短於一週的話，休假回來的人可能已經來不及在到期前改。
PWD_WARN_DAYS = 14


def _dir_state(r) -> dict:
    """目錄端的帳號狀態（AD 才有；其他來源一律「不知道」）。

    `dir_disabled` 是三態：True 停用 / False 正常 / **None 不知道**。
    OpenLDAP、本機、SSO 帳號都是 None —— 把 None 顯示成「正常」等於替目錄
    做了它沒說過的保證。
    """
    try:
        raw = r["dir_disabled"]
    except (KeyError, IndexError):
        raw = None
    disabled = None if raw is None else bool(raw)
    try:
        exp = r["pwd_expires_at"]
    except (KeyError, IndexError):
        exp = None
    days = None
    if exp:
        days = int((exp - time.time()) // 86400)
    return {
        "dir_disabled": disabled,
        "pwd_expires_at": exp,
        # 已經過期的（負數）也要提醒 —— 那個人現在正卡在改密碼畫面前
        "pwd_expiry_days": days,
        "pwd_expiring_soon": days is not None and days <= PWD_WARN_DAYS,
    }


def _view_where(view: str) -> tuple[str, tuple]:
    """(WHERE 子句, 參數) —— 使用者管理的檢視篩選（見 list_users）。

    **時間戳一律走參數綁定，不要格式化進 SQL 字串**：`f"{base:.3f}"` 會四捨五入到
    毫秒，剛好等於基準時間的那些人（也就是這次掃描才剛看到的人）會因為誤差被判成
    「不見了」。第一版就是這樣，測試當場抓到。
    """
    mirror = ("source IN ('ldap','ad') AND enabled=0 "
              "AND COALESCE(last_login_at,0)=0")
    if view == "active":
        return f"WHERE NOT ({mirror})", ()
    if view == "directory":
        return f"WHERE {mirror}", ()
    if view == "missing":
        # 目錄裡已經找不到的帳號（離職 / 停用 / 被移出同步範圍）。
        #
        # 判定基準是「上一次完整掃描的時間」：那次掃描有看到的人 seen_at 會被更新
        # 成掃描時間，沒看到的人維持舊值或 NULL。沒有完整掃描過就回一個永遠不成立
        # 的條件 —— **寧可什麼都不顯示，也不要把整個組織標成離職**。
        base = last_full_directory_scan_at()
        if base <= 0:
            return "WHERE 0", ()
        return ("WHERE source IN ('ldap','ad') AND external_dn IS NOT NULL "
                "AND external_dn<>'' AND COALESCE(directory_seen_at,0) < ?"), (base,)
    if view == "dir_disabled":
        # AD 端已停用（userAccountControl 的 ACCOUNTDISABLE）。這些人登不進來，
        # 但本站的角色指派與群組成員關係全都還在 —— 內控盤點要看得到。
        return "WHERE dir_disabled = 1", ()
    if view == "pwd_expiring":
        # 密碼即將到期（含已經過期的）。到期的人會卡在 AD 的改密碼流程，
        # 對他來說症狀是「突然登不進來」，管理員要能事先看到。
        return ("WHERE pwd_expires_at IS NOT NULL AND pwd_expires_at < ?"), (
            time.time() + PWD_WARN_DAYS * 86400,)
    return "", ()


def count_users(view: str = "all") -> int:
    where, params = _view_where(view)
    return auth_db.conn().execute(
        f"SELECT COUNT(*) AS c FROM users {where}", params).fetchone()["c"]


def list_users(view: str = "all") -> list[dict]:
    """List users with their role assignments.

    `view` filters the population so the 使用者管理 page doesn't render the
    thousands of directory users mirrored as a catalog (which OOM'd the browser):
      - 'active'    → everyone EXCEPT the never-activated mirror catalog
                      (i.e. exclude source ldap/ad that is enabled=0 AND never
                      logged in). This is the default 使用者管理 view.
      - 'directory' → ONLY the never-activated mirror catalog.
      - 'all'       → everyone (backward-compatible default of this function).

    Batched: roles for every user load in one query, not N+1."""
    where, params = _view_where(view)
    _full_scan_at = last_full_directory_scan_at()
    conn = auth_db.conn()
    rows = conn.execute(
        "SELECT id, username, display_name, source, external_dn, enabled, email, "
        "is_admin_seed, is_audit_seed, password_hash, created_at, last_login_at, "
        "directory_seen_at, dir_disabled, pwd_expires_at "
        f"FROM users {where} ORDER BY username", params
    ).fetchall()
    roles_by_user = permissions.list_roles_for_subjects(
        "user", [str(r["id"]) for r in rows])
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "username": r["username"],
            "display_name": r["display_name"] or r["username"],
            "source": r["source"], "external_dn": r["external_dn"],
            "enabled": bool(r["enabled"]),
            "is_admin_seed": bool(r["is_admin_seed"]),
            "is_audit_seed": bool(r["is_audit_seed"]),
            "password_set": r["password_hash"] is not None,
            # 作業完成通知的預設收件信箱。AD / LDAP / SSO 帳號由來源帶入，
            # 本機帳號由管理員或本人填。
            "email": r["email"] or "",
            "created_at": r["created_at"], "last_login_at": r["last_login_at"],
            # 上一次完整目錄掃描時還看不看得到這個帳號。None = 沒被同步涵蓋過。
            "directory_seen_at": r["directory_seen_at"],
            "directory_missing": _is_missing(r, _full_scan_at),
            "roles": roles_by_user.get(str(r["id"]), []),
            **_dir_state(r),
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

    Default role assignment: the admin-configured new-user default role (via
    roles.get_default_role_id(), normally 'default-user') if `roles` is None
    (admin-friendly common case). Pass `roles=[]` to explicitly create with no
    roles.
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
    from . import roles as _roles
    role_ids = list(roles) if roles is not None else [_roles.get_default_role_id()]
    permissions.set_subject_roles("user", str(new_id), role_ids)
    return new_id


def normalise_email(v: str) -> str:
    """清理信箱字串。

    * 去掉控制字元 —— 含換行的值會讓寄信在送出當下失敗（標頭注入本身被
      Python 的 email 模組擋住，但使用者只會看到「通知都沒收到」而查不出原因）。
    * 長度上限 200。
    * **不做嚴格格式驗證**：目錄裡什麼都有（有人填 `姓名 <a@b.c>`、有人填內部
      別名），擋掉反而讓同步不進來。真正的驗證交給寄信時的伺服器。
    """
    v = "".join(ch for ch in str(v or "") if ch == "\t" or ord(ch) >= 0x20)
    return v.strip()[:200]


def update(user_id: int, *, display_name: Optional[str] = None,
           enabled: Optional[bool] = None,
           roles: Optional[list[str]] = None,
           groups: Optional[list[int]] = None,
           email: Optional[str] = None) -> None:
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
        if email is not None:
            conn.execute("UPDATE users SET email=? WHERE id=?",
                         (normalise_email(email), user_id))
        if enabled is not None:
            # Refuse to disable the seed admin (would lock everyone out).
            if existing["is_admin_seed"] and not enabled:
                raise ValueError("不能停用初始管理員帳號")
            # admin_disabled 跟著設：目錄帳號登入時看它擋（enabled 會被名冊啟用寫回 1，不能只靠它）
            conn.execute("UPDATE users SET enabled=?, admin_disabled=? WHERE id=?",
                         (1 if enabled else 0, 0 if enabled else 1, user_id))
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
    row = conn.execute(
        "SELECT is_admin_seed, is_audit_seed FROM users WHERE id=?",
        (user_id,)).fetchone()
    if not row:
        raise ValueError(f"使用者 id={user_id} 不存在")
    if row["is_admin_seed"]:
        raise ValueError("不能刪除初始管理員帳號")
    if row["is_audit_seed"]:
        raise ValueError("不能刪除內建稽核員帳號（jtdt-auditor）")
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
    # **磁碟上的檔案也要清**。只清資料庫的話 `data/workspace/u<id>/` 會留著，
    # 而保留期設成「永久保留」時那就是永久留著離職者的檔案；管理區的用量
    # 統計還會繼續列出一個已經不存在的帳號。
    try:
        from . import workspace
        workspace.purge_user(user_id)
    except Exception:  # noqa: BLE001 — 帳號已經刪掉了，清檔失敗不該讓整件事失敗
        from .log_safe import safe_log
        logger.warning(
            "刪除帳號後清理工作區失敗 user_id=%s", safe_log(str(user_id)))
    # 會議摘要記住的背景（「上一次分析這份逐字稿時填的」）—— 裡面常有別人的姓名職稱
    try:
        from . import meeting_context_memory
        meeting_context_memory.purge_user(user_id)
    except Exception:  # noqa: BLE001 — 同上
        from .log_safe import safe_log
        logger.warning(
            "刪除帳號後清理會議背景失敗 user_id=%s", safe_log(str(user_id)))
    # 事務機掃描取件：他自己新增的事務機連線（含最近一次連線測試的報告）
    try:
        from . import mfp_store
        mfp_store.purge_user(user_id)
    except Exception:  # noqa: BLE001 — 同上
        from .log_safe import safe_log
        logger.warning(
            "刪除帳號後清理事務機連線失敗 user_id=%s", safe_log(str(user_id)))
    # 名片辨識：他的辨識批次（原圖、裁圖、聯絡人）
    try:
        from ..tools.business_card_reader import store as bc_store
        if bc_store.db_path().exists():
            bc_store.purge_owner(f"u{int(user_id)}")
            from ..tools.business_card_reader import books as bc_books
            bc_books.purge_owner(f"u{int(user_id)}", int(user_id))
    except Exception:  # noqa: BLE001 — 同上
        from .log_safe import safe_log
        logger.warning(
            "刪除帳號後清理名片辨識批次失敗 user_id=%s", safe_log(str(user_id)))


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


#: 使用者管理一頁顯示幾筆。超過這個數量就改走伺服器端分頁 —— 一次 render
#: 幾千列會把瀏覽器打爆（客戶做完 AD 全量同步後實際踩過）。
PAGE_SIZE = 100


def list_users_page(*, view: str = "all", q: str = "", offset: int = 0,
                    limit: int = PAGE_SIZE, source: str = "",
                    enabled: Optional[bool] = None,
                    never_logged_in: bool = False) -> dict:
    """分頁 + 伺服器端篩選的使用者清單。

    回 `{"rows": [...], "total": N}`。篩選都在 SQL 做 —— 前端過濾只能過濾
    「已經 render 出來的那一頁」，使用者以為篩了全部其實只篩了眼前 100 筆，
    這種半套篩選比沒有更危險。
    """
    where, params = _view_where(view)
    clauses = [where[6:]] if where.startswith("WHERE ") else []
    args = list(params)
    if q:
        clauses.append("(username LIKE ? OR display_name LIKE ? OR email LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like]
    if source:
        clauses.append("source = ?")
        args.append(source)
    if enabled is not None:
        clauses.append("enabled = ?")
        args.append(1 if enabled else 0)
    if never_logged_in:
        clauses.append("COALESCE(last_login_at,0) = 0")
    sql_where = ("WHERE " + " AND ".join(f"({c})" for c in clauses)) if clauses else ""

    conn = auth_db.conn()
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM users {sql_where}", args).fetchone()["c"]
    rows = conn.execute(
        "SELECT id, username, display_name, source, external_dn, enabled, email, "
        "is_admin_seed, is_audit_seed, password_hash, created_at, last_login_at, "
        "directory_seen_at, dir_disabled, pwd_expires_at "
        f"FROM users {sql_where} ORDER BY username "
        "LIMIT ? OFFSET ?", args + [int(limit), int(offset)]).fetchall()
    full_scan_at = last_full_directory_scan_at()
    roles_by_user = permissions.list_roles_for_subjects(
        "user", [str(r["id"]) for r in rows])
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "username": r["username"],
            "display_name": r["display_name"] or r["username"],
            "source": r["source"], "external_dn": r["external_dn"],
            "enabled": bool(r["enabled"]),
            "is_admin_seed": bool(r["is_admin_seed"]),
            "is_audit_seed": bool(r["is_audit_seed"]),
            "password_set": r["password_hash"] is not None,
            "email": r["email"] or "",
            "created_at": r["created_at"], "last_login_at": r["last_login_at"],
            "directory_seen_at": r["directory_seen_at"],
            "directory_missing": _is_missing(r, full_scan_at),
            "roles": roles_by_user.get(str(r["id"]), []),
            **_dir_state(r),
        })
    return {"rows": out, "total": total}
