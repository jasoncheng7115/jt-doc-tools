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

# ---------- subject key normalisation ----------

def canon_subject_key(subject_type: str, subject_key: str) -> str:
    """正規化 subject key，讓「指派時寫的」和「登入時比對的」對得上。

    **只對 `ou` 動手**（它的 key 是一段 DN）；`user` / `group` 的 key 是
    數字 id 或群組 id，原樣返回。

    問題：OU 授權的比對是**精確字串相等**（SQL `subject_key=?`），但 LDAP 的
    DN 屬性型別**不分大小寫**、逗號後可有空白。於是 `OU=Sales,DC=x`（AD 文件 /
    PowerShell 慣用大寫，或使用者手貼）和目錄實際回傳的 `ou=Sales,dc=x` 會被
    當成兩個不同對象 —— 指派看起來成功、清單也列著，底下的人登入卻**無聲地**
    拿不到權限（實測 2026-08-24）。管理介面正常操作踩不到（送出的 dn 與目錄
    樹同源），但手動輸入 / 匯入改過的設定 / 換目錄來源就會。

    正規化規則（保守，**只動結構、不動值**）：
      * 逗號切成 RDN 分量，各自去頭尾空白
      * 每個分量的**屬性型別**（`=` 左邊）轉小寫；**值**（右邊）原樣保留
      * 以 `,` 重新接起（不留空白）

    值的大小寫刻意不碰：LDAP 值多半也 caseIgnore，但把值一起壓平的風險是
    「兩個剛好同名不同大小寫的 OU 被併成一個」—— 那是往「多給權限」的方向錯，
    寧可不做。屬性型別轉小寫已經解掉最常見的 `OU=`/`DC=` 問題。
    """
    if subject_type != "ou" or not subject_key:
        return subject_key
    parts = []
    for rdn in str(subject_key).split(","):
        rdn = rdn.strip()
        if "=" in rdn:
            attr, _, val = rdn.partition("=")
            parts.append(f"{attr.strip().lower()}={val.strip()}")
        elif rdn:
            parts.append(rdn)
    return ",".join(parts)


def assign_role(subject_type: str, subject_key: str, role_id: str) -> None:
    if subject_type not in ("user", "group", "ou"):
        raise ValueError("invalid subject_type")
    subject_key = canon_subject_key(subject_type, subject_key)
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
    subject_key = canon_subject_key(subject_type, subject_key)
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
    subject_key = canon_subject_key(subject_type, subject_key)
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
    subject_key = canon_subject_key(subject_type, subject_key)
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
    subject_key = canon_subject_key(subject_type, subject_key)
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "DELETE FROM subject_perms WHERE subject_type=? AND subject_key=? "
            "AND tool_id=?", (subject_type, subject_key, tool_id),
        )
    invalidate_cache()


def list_direct_tools_for_subject(subject_type: str, subject_key: str) -> list[str]:
    subject_key = canon_subject_key(subject_type, subject_key)
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


#: 巢狀群組往上追的深度上限。AD 的群組巢狀通常兩三層，10 層已經遠超實務；
#: 有上限才不會被目錄端的環狀關係拖死（環也另外用 seen 擋）。
_NESTED_GROUP_MAX_DEPTH = 10


def _user_groups_local(conn, user_id: int) -> list[str]:
    """這位使用者屬於哪些群組（group_id 字串），**含巢狀的上層群組**。

    ## 為什麼要往上追

    企業 AD 幾乎都是「角色群組巢在部門群組底下」：把權限指派給上層的部門群組，
    底下各子群組的成員理當都拿得到。原本這裡只讀 `group_members` 的直接成員，
    所以「權限給了上層群組卻沒生效」—— 而群組管理頁**還畫了樹狀縮排**，更容易
    讓管理員以為會繼承（客戶反映「AD 帳號管理還有精進空間」時盤點到的）。

    ## 怎麼追

    同步時已經把每個群組的上層 DN 存進 `groups.parent_dn`（由 `memberOf` 推得），
    這裡沿著它往上走。**要防環**：目錄端設定錯誤造成 A→B→A 時，沒有 seen 集合
    就會無窮迴圈把請求卡死。
    """
    rows = conn.execute(
        "SELECT group_id FROM group_members WHERE user_id=?", (user_id,)
    ).fetchall()
    direct = [str(r["group_id"]) for r in rows]
    if not direct:
        return []

    # id → parent_dn，dn → id：一次撈完，不要在迴圈裡逐筆查
    grows = conn.execute(
        "SELECT id, external_dn, parent_dn FROM groups").fetchall()
    parent_of = {str(g["id"]): (g["parent_dn"] or "").strip() for g in grows}
    id_by_dn = {(g["external_dn"] or "").strip(): str(g["id"])
                for g in grows if (g["external_dn"] or "").strip()}

    out: list[str] = []
    seen: set[str] = set()
    stack = list(direct)
    depth = 0
    while stack and depth <= _NESTED_GROUP_MAX_DEPTH:
        nxt: list[str] = []
        for gid in stack:
            if gid in seen:
                continue                 # 目錄端的環狀關係：走過就不再走
            seen.add(gid)
            out.append(gid)
            pdn = parent_of.get(gid, "")
            if pdn and pdn in id_by_dn:
                nxt.append(id_by_dn[pdn])
        stack = nxt
        depth += 1
    return out


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
        # 登入時算出的 OU subject 要走**同一道正規化**，才能跟寫入端存進 DB
        # 的 key 對得上（DN 屬性型別不分大小寫，見 canon_subject_key）。
        return [(st, canon_subject_key(st, sk))
                for st, sk in auth_ldap.get_ou_subjects_for_dn(row["external_dn"])]
    except Exception:
        return []


def known_ous(limit: int = 1000) -> list[dict]:
    """本站帳號與群組所在的組織單位（OU），給「指派給組織單位」的畫面用選的，不用手打 DN。

    從目錄帳號與群組的 DN（`external_dn`）推出每一層 OU（同 `auth_ldap.get_ou_subjects_for_dn`），
    鍵走 `canon_subject_key` —— 跟登入時算出來的 OU 對得上。`users` 是 DN 在這個 OU 底下（含下層）的帳號數。
    SAML / OIDC 與本機帳號沒有 DN，不會出現在這裡。回 `[{key, dn, name, users}]`，帳號多的在前。"""
    from . import auth_ldap
    conn = auth_db.conn()
    counts: dict[str, int] = {}
    shown: dict[str, str] = {}

    def add(dn: str, n: int) -> None:
        for _st, ou in auth_ldap.get_ou_subjects_for_dn(dn or ""):
            k = canon_subject_key("ou", ou)
            counts[k] = counts.get(k, 0) + n
            shown.setdefault(k, ou)

    for r in conn.execute("SELECT external_dn FROM users WHERE COALESCE(external_dn,'')<>''").fetchall():
        add(r["external_dn"], 1)
    for r in conn.execute("SELECT external_dn FROM groups WHERE COALESCE(external_dn,'')<>''").fetchall():
        add(r["external_dn"], 0)

    def label(dn: str) -> str:
        ous, dcs = [], []
        for rdn in dn.split(","):
            a, _, v = rdn.strip().partition("=")
            if a.strip().upper() == "OU":
                ous.append(v.strip())
            elif a.strip().upper() == "DC":
                dcs.append(v.strip())
        return " / ".join(ous) + (f"（{'.'.join(dcs)}）" if dcs else "")

    out = [{"key": k, "dn": shown[k], "name": label(shown[k]), "users": n} for k, n in counts.items()]
    out.sort(key=lambda o: (-o["users"], o["name"].lower()))
    return out[:limit]


def subjects_for_user(user_id: int) -> list[tuple[str, str]]:
    """這位使用者「是」哪些主體：使用者本身、所屬群組（含巢狀的上層）、組織單位。

    權限解析用的就是這一份；其他要「指派給使用者 / 群組 / 組織單位」的功能（例如共用事務機的成員）
    也用它判斷，不要另外寫一套 —— 巢狀群組、OU 的正規化都在這裡。"""
    conn = auth_db.conn()
    subjects: list[tuple[str, str]] = [("user", str(user_id))]
    for gid in _user_groups_local(conn, user_id):
        subjects.append(("group", gid))
    subjects.extend(_user_external_subjects(conn, user_id))
    return subjects


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
    subjects = subjects_for_user(user_id)

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
    subject_key = canon_subject_key(subject_type, subject_key)
    conn = auth_db.conn()
    rows = conn.execute(
        "SELECT role_id FROM subject_roles WHERE subject_type=? AND subject_key=?",
        (subject_type, str(subject_key)),
    ).fetchall()
    return [r["role_id"] for r in rows]


def list_roles_for_subjects(
    subject_type: str, subject_keys: list[str]
) -> dict[str, list[str]]:
    """Batch version of list_roles_for_subject: one query for many subjects.

    Returns {subject_key: [role_id, ...]} covering exactly the given keys
    (keys with no roles map to []). Kills the N+1 that made the 群組管理 /
    使用者管理 pages slow with thousands of rows (one SELECT per row → one
    SELECT total). SQLite has a variable limit (~999/32766); we chunk to be
    safe on very large directories."""
    out: dict[str, list[str]] = {str(k): [] for k in subject_keys}
    keys = [str(k) for k in subject_keys]
    if not keys:
        return out
    conn = auth_db.conn()
    CHUNK = 900
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i:i + CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT subject_key, role_id FROM subject_roles "
            f"WHERE subject_type=? AND subject_key IN ({placeholders})",
            (subject_type, *chunk),
        ).fetchall()
        for r in rows:
            out.setdefault(str(r["subject_key"]), []).append(r["role_id"])
    return out


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


def explain_effective_tools(user_id: int) -> dict:
    """「這個人最終能用哪些工具，每一項是從哪裡來的」。

    ## 為什麼需要

    權限來源散在四個地方 —— 直接給使用者的角色、群組（含巢狀上層）、OU、以及
    直接工具授權。出事時管理員無法自證也無法排查：他只看得到「這個 subject 有
    哪些角色」，看不到「A 這個人加總後實際能用什麼、是哪一條規則給的」。
    稽核回應與交接文件都要用這個。

    ## 回傳形狀

    ```
    {
      "admin": bool,          # 有 admin 角色（全開）
      "auditor": bool,        # 稽核員（硬牆，一律 0 個工具）
      "subjects": [{"type","key","label"}...],   # 這個人「是」哪些主體
      "roles":    [{"id","via"}...],             # 每個角色從哪個主體來
      "tools":    {tool_id: [來源說明, ...]},
      "tool_ids": [...],
    }
    ```

    刻意**重算一次**而不是讀 `effective_tools` 的快取 —— 這個函式是拿來查真相的，
    讀到快取就可能解釋到一份過期的結果。
    """
    conn = auth_db.conn()

    subjects: list[tuple[str, str]] = [("user", str(user_id))]
    group_ids = _user_groups_local(conn, user_id)
    direct_group_ids = {
        str(r["group_id"]) for r in conn.execute(
            "SELECT group_id FROM group_members WHERE user_id=?",
            (user_id,)).fetchall()}
    for gid in group_ids:
        subjects.append(("group", gid))
    ou_subjects = _user_external_subjects(conn, user_id)
    subjects.extend(ou_subjects)

    gname = {str(r["id"]): r["name"] for r in
             conn.execute("SELECT id, name FROM groups").fetchall()}

    def _label(st: str, sk: str) -> str:
        if st == "user":
            return "直接指派給這個帳號"
        if st == "group":
            n = gname.get(sk, f"群組 #{sk}")
            return (f"群組「{n}」" if sk in direct_group_ids
                    else f"上層群組「{n}」（巢狀繼承）")
        if st == "ou":
            return f"OU {sk}"
        return f"{st} {sk}"

    role_via: dict[str, list[str]] = {}
    direct_tool_via: dict[str, list[str]] = {}
    for st, sk in subjects:
        lab = _label(st, sk)
        for r in conn.execute(
                "SELECT role_id FROM subject_roles WHERE subject_type=? "
                "AND subject_key=?", (st, sk)).fetchall():
            role_via.setdefault(r["role_id"], []).append(lab)
        for r in conn.execute(
                "SELECT tool_id FROM subject_perms WHERE subject_type=? "
                "AND subject_key=?", (st, sk)).fetchall():
            direct_tool_via.setdefault(r["tool_id"], []).append(lab)

    is_auditor = "auditor" in role_via
    is_admin = (not is_auditor) and "admin" in role_via

    tools: dict[str, list[str]] = {}
    if not is_auditor and not is_admin:
        for tid, vias in direct_tool_via.items():
            tools.setdefault(tid, []).extend(f"直接授權（{v}）" for v in vias)
        if role_via:
            ph = ",".join("?" * len(role_via))
            rows = conn.execute(
                f"SELECT role_id, tool_id FROM role_perms WHERE role_id IN ({ph})",
                tuple(role_via)).fetchall()
            rnames = {r["id"]: r["display_name"] for r in
                      conn.execute("SELECT id, display_name FROM roles").fetchall()}
            for r in rows:
                rid = r["role_id"]
                nice = rnames.get(rid, rid)
                for v in role_via[rid]:
                    tools.setdefault(r["tool_id"], []).append(f"角色「{nice}」← {v}")

    return {
        "admin": is_admin,
        "auditor": is_auditor,
        "subjects": [{"type": st, "key": sk, "label": _label(st, sk)}
                     for st, sk in subjects],
        "roles": [{"id": rid, "via": vias} for rid, vias in sorted(role_via.items())],
        "tools": {k: sorted(set(v)) for k, v in tools.items()},
        "tool_ids": sorted(tools),
    }
