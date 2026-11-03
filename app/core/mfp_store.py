"""事務機掃描取件：事務機連線、共用事務機的成員、掃描紀錄（`data/mfp.sqlite`）。

**每一筆連線都屬於一個擁有者**（`owner_scope`）：
* 使用者自己新增的：啟用認證時是 `u<user_id>`，未啟用認證時是 `local`。所有讀寫都要帶擁有者 ——
  拿別人的 id 查不到、改不到、刪不到（回「不存在」，不透露那筆是別人的）。同一個 IP 不同人各自新增，互不影響。
* **共用事務機**（`shared`，使用者 2026-10-14）：管理員在設定裡建立，再把使用者、群組、組織單位加成成員
  （`mfp_members`，主體的寫法跟權限系統同一套）。成員看得到、可以測試與掃描，不能改名或移除。

目前（Beta、尚未實機驗證）只存設備位址、辨識結果與最近一次連線測試的報告（已遮蔽），
**不存任何帳密** —— 規格書規定「尚未驗證的密碼不永久保存」。機內信箱取件（`mfp_box`，推測實作）的
信箱密碼與設備帳密只在那次請求、那件取件作業的記憶體裡。

`mfp_scans` 記的是作業：從事務機掃描（`kind='scan'`）、機內信箱取件（`kind='box'`）、檔案分享取件（`kind='share'`）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import db

#: 每位使用者最多幾台。
MAX_PER_OWNER = 20
#: 存進資料庫的報告上限（位元組）。正常一份只有幾十 KB。
MAX_REPORT_BYTES = 512 * 1024


class StoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


DUPLICATE_MESSAGE = "這台事務機（同一個位址與連線方式）已經在你的清單裡"
LIMIT_MESSAGE = "已達每人可新增的事務機上限"
NOT_FOUND_MESSAGE = "找不到這台事務機"


def db_path() -> Path:
    from ..config import settings
    return settings.data_dir / "mfp.sqlite"


def _m1_initial(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mfp_connections (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_scope    TEXT NOT NULL,
            owner_user_id  INTEGER,
            display_name   TEXT NOT NULL,
            host           TEXT NOT NULL,
            port           INTEGER,
            scheme         TEXT,
            vendor         TEXT,
            family         TEXT,
            model          TEXT,
            firmware       TEXT,
            detect_status  TEXT,
            last_test_at   REAL,
            last_reachable INTEGER,
            last_report    TEXT,
            config_version INTEGER NOT NULL DEFAULT 1,
            enabled        INTEGER NOT NULL DEFAULT 1,
            created_at     REAL NOT NULL,
            updated_at     REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_mfp_conn_target
            ON mfp_connections(owner_scope, host, COALESCE(port, 0), COALESCE(scheme, ''));
        CREATE INDEX IF NOT EXISTS idx_mfp_conn_owner
            ON mfp_connections(owner_scope, created_at);
        CREATE INDEX IF NOT EXISTS idx_mfp_conn_user
            ON mfp_connections(owner_user_id);
    """)


def _m2_shared_and_scans(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mfp_members (
            conn_id      INTEGER NOT NULL REFERENCES mfp_connections(id) ON DELETE CASCADE,
            subject_type TEXT NOT NULL,
            subject_key  TEXT NOT NULL,
            added_at     REAL NOT NULL,
            PRIMARY KEY (conn_id, subject_type, subject_key)
        );
        CREATE INDEX IF NOT EXISTS idx_mfp_members_subject ON mfp_members(subject_type, subject_key);
        CREATE TABLE IF NOT EXISTS mfp_scans (
            id                TEXT PRIMARY KEY,
            conn_id           INTEGER,
            owner_scope       TEXT NOT NULL,
            owner_user_id     INTEGER,
            device            TEXT NOT NULL,
            conn_name         TEXT,
            status            TEXT NOT NULL,
            settings          TEXT,
            pages             INTEGER,
            bytes             INTEGER,
            file_name         TEXT,
            result_path       TEXT,
            workspace_file_id TEXT,
            workspace_note    TEXT,
            tls_verified      INTEGER,
            error_code        TEXT,
            error_detail      TEXT,
            created_at        REAL NOT NULL,
            started_at        REAL,
            finished_at       REAL
        );
        CREATE INDEX IF NOT EXISTS idx_mfp_scans_owner ON mfp_scans(owner_scope, created_at);
        CREATE INDEX IF NOT EXISTS idx_mfp_scans_user ON mfp_scans(owner_user_id);
    """)


def _m3_job_kind_and_items(conn: sqlite3.Connection) -> None:
    """掃描紀錄多記「哪一種作業」與「每份文件、每個檔案」（使用者 2026-10-14：信箱取件、JPEG 每頁一個檔）。

    `ADD COLUMN` 有預設值，不重建表（舊紀錄一律是掃描）。"""
    conn.executescript("""
        ALTER TABLE mfp_scans ADD COLUMN kind TEXT NOT NULL DEFAULT 'scan';
        ALTER TABLE mfp_scans ADD COLUMN items TEXT;
    """)


def _m4_prefs(conn: sqlite3.Connection) -> None:
    """每人每台的信任與偏好（v1.17.5）。原本存在瀏覽器（localStorage）：換一台電腦就要重新信任憑證、
    重新同意 HTTP、信箱編號也不見了（客戶回報「新增的事務機好像只有在該台機器有」時查出來的）。

    `owner_scope`：`u<id>` / `local` 是個人；`shared` 是共用事務機由管理員設定、成員沿用的那一份。
    只存允許清單裡的欄位（`_PREF_RULES`）；**不存任何密碼**。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mfp_prefs (
            owner_scope TEXT NOT NULL,
            conn_id     INTEGER NOT NULL REFERENCES mfp_connections(id) ON DELETE CASCADE,
            kind        TEXT NOT NULL,
            data        TEXT NOT NULL,
            updated_at  REAL NOT NULL,
            PRIMARY KEY (owner_scope, conn_id, kind)
        );
    """)


_MIGRATIONS = [_m1_initial, _m2_shared_and_scans, _m3_job_kind_and_items, _m4_prefs]
_INITED: set[str] = set()
_INIT_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    p = db_path()
    key = str(p)
    if key not in _INITED:
        with _INIT_LOCK:
            if key not in _INITED:
                p.parent.mkdir(parents=True, exist_ok=True)
                db.migrate(p, _MIGRATIONS)
                _INITED.add(key)
    return db.get_conn(p)


#: 共用事務機的擁有者範圍（管理員建立；成員在 `mfp_members`）。
SHARED = "shared"
#: 共用事務機最多幾台。
MAX_SHARED = 100
_OWNER_RE = re.compile(r"^(u\d+|local|shared)$")


def _check_owner(owner: str) -> str:
    if not _OWNER_RE.match(owner or ""):
        raise ValueError("invalid owner scope")
    return owner


def clean_name(name: str, fallback: str) -> str:
    s = "".join(ch for ch in (name or "") if ch.isprintable()).strip()
    return (s or fallback)[:64]


def _report_of(r: sqlite3.Row) -> Optional[dict]:
    try:
        return json.loads(r["last_report"]) if r["last_report"] else None
    except ValueError:
        return None


def _row(r: sqlite3.Row, with_report: bool = False) -> dict[str, Any]:
    d = {k: r[k] for k in r.keys() if k != "last_report"}
    d["last_reachable"] = None if r["last_reachable"] is None else bool(r["last_reachable"])
    d["enabled"] = bool(r["enabled"])
    d["shared"] = r["owner_scope"] == SHARED
    d.pop("owner_scope", None)
    d.pop("owner_user_id", None)
    rep = _report_of(r)
    # 掃描能力（連線測試時讀到的 eSCL 能力表）—— 清單上決定要不要出現「掃描」
    d["escl"] = (rep or {}).get("escl") or None
    # 連得上的協定與連接埠（機內信箱取件沿用連線測試連得上的那一組）
    d["reachable_on"] = [str(x) for x in ((rep or {}).get("summary") or {}).get("reachable_on") or []][:4]
    # 用舊的辨識規則判斷、而且結果是認不出來那一類：提示「可重新測試」（規則更新後可能認得出來）
    from . import mfp_profiles
    rules = ((rep or {}).get("detection") or {}).get("rules")
    d["rules_stale"] = bool(rep) and r["detect_status"] in mfp_profiles.RECHECK_STATUSES \
        and rules != mfp_profiles.rules_signature()
    # 存起來的結果套上目前的已驗證清單（之前判成實驗功能、現在已驗證的型號 → 已支援；不必重新測試）
    d["detect_status"] = mfp_profiles.effective_status(r["vendor"], r["model"], r["detect_status"])
    if with_report:
        if rep and isinstance(rep.get("detection"), dict):
            det = rep["detection"]
            st = mfp_profiles.effective_status(det.get("vendor"), det.get("model"), det.get("status"))
            if st != det.get("status"):
                rep = dict(rep, detection=dict(det, status=st, message=mfp_profiles.STATUS_MESSAGES.get(st, det.get("message"))))
        d["last_report"] = rep
    return d


def _report_json(report: Optional[dict]) -> Optional[str]:
    if report is None:
        return None
    s = json.dumps(report, ensure_ascii=False)
    if len(s.encode("utf-8")) > MAX_REPORT_BYTES:
        slim = {k: v for k, v in report.items() if k not in ("attempts",)}
        slim["truncated"] = True
        s = json.dumps(slim, ensure_ascii=False)
    return s


def _fields_from_report(report: Optional[dict]) -> dict[str, Any]:
    d = (report or {}).get("detection") or {}
    summ = (report or {}).get("summary") or {}
    model = d.get("model")
    if not model and not d.get("family"):
        # 不在型號辨識名單裡的（例如 HP、Epson）：eSCL 能力表自己報的型號名稱
        model = ((report or {}).get("escl") or {}).get("model") or None
    return {"vendor": d.get("vendor"), "family": d.get("family"), "model": model,
            "firmware": d.get("firmware"), "detect_status": d.get("status"),
            "last_reachable": 1 if summ.get("reachable") else 0}


def list_for(owner: str) -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM mfp_connections WHERE owner_scope=? ORDER BY created_at",
                     (_check_owner(owner),)).fetchall()
    return [_row(r) for r in rows]


def get_for(owner: str, conn_id: int, with_report: bool = False) -> Optional[dict]:
    r = _conn().execute("SELECT * FROM mfp_connections WHERE id=? AND owner_scope=?",
                        (int(conn_id), _check_owner(owner))).fetchone()
    return _row(r, with_report) if r else None


def create(owner: str, owner_user_id: Optional[int], host: str, port: Optional[int],
           scheme: Optional[str], display_name: str, report: Optional[dict]) -> dict:
    _check_owner(owner)
    c = _conn()
    now = time.time()
    fields = _fields_from_report(report)
    with db.tx(c):
        n = c.execute("SELECT COUNT(*) FROM mfp_connections WHERE owner_scope=?", (owner,)).fetchone()[0]
        if n >= (MAX_SHARED if owner == SHARED else MAX_PER_OWNER):
            raise StoreError("LIMIT", LIMIT_MESSAGE)
        try:
            cur = c.execute(
                "INSERT INTO mfp_connections(owner_scope, owner_user_id, display_name, host, port, scheme,"
                " vendor, family, model, firmware, detect_status, last_test_at, last_reachable,"
                " last_report, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (owner, owner_user_id, clean_name(display_name, host), host, port, scheme,
                 fields["vendor"], fields["family"], fields["model"], fields["firmware"],
                 fields["detect_status"], now if report else None,
                 fields["last_reachable"] if report else None, _report_json(report), now, now))
        except sqlite3.IntegrityError:
            raise StoreError("DUPLICATE", DUPLICATE_MESSAGE) from None
        new_id = cur.lastrowid
    return get_for(owner, new_id)  # type: ignore[return-value]


def rename(owner: str, conn_id: int, display_name: str) -> Optional[dict]:
    cur = get_for(owner, conn_id)
    if cur is None:
        return None
    c = _conn()
    with db.tx(c):
        c.execute("UPDATE mfp_connections SET display_name=?, updated_at=? WHERE id=? AND owner_scope=?",
                  (clean_name(display_name, cur["host"]), time.time(), int(conn_id), owner))
    return get_for(owner, conn_id)


def save_test(owner: str, conn_id: int, report: dict) -> Optional[dict]:
    if get_for(owner, conn_id) is None:
        return None
    f = _fields_from_report(report)
    c = _conn()
    now = time.time()
    with db.tx(c):
        c.execute("UPDATE mfp_connections SET vendor=?, family=?, model=?, firmware=?, detect_status=?,"
                  " last_test_at=?, last_reachable=?, last_report=?, updated_at=?"
                  " WHERE id=? AND owner_scope=?",
                  (f["vendor"], f["family"], f["model"], f["firmware"], f["detect_status"], now,
                   f["last_reachable"], _report_json(report), now, int(conn_id), owner))
    return get_for(owner, conn_id)


def delete(owner: str, conn_id: int) -> bool:
    c = _conn()
    with db.tx(c):
        n = c.execute("DELETE FROM mfp_connections WHERE id=? AND owner_scope=?",
                      (int(conn_id), _check_owner(owner))).rowcount
    return n > 0


def purge_user(user_id: int) -> int:
    """帳號被刪除時一起清掉他的連線、共用事務機的成員資格、掃描紀錄（規格 §14）。

    **他建立的共用事務機不刪**（那是給別人用的；建立者欄位清掉）。"""
    p = db_path()
    if not p.exists():
        return 0
    c = _conn()
    uid = int(user_id)
    with db.tx(c):
        n = c.execute("DELETE FROM mfp_connections WHERE owner_scope=? OR (owner_user_id=? AND owner_scope<>?)",
                      (f"u{uid}", uid, SHARED)).rowcount
        c.execute("UPDATE mfp_connections SET owner_user_id=NULL WHERE owner_scope=? AND owner_user_id=?",
                  (SHARED, uid))
        c.execute("DELETE FROM mfp_members WHERE subject_type='user' AND subject_key=?", (str(uid),))
        c.execute("DELETE FROM mfp_prefs WHERE owner_scope=?", (f"u{uid}",))
        rows = c.execute("SELECT result_path FROM mfp_scans WHERE owner_user_id=? OR owner_scope=?",
                         (uid, f"u{uid}")).fetchall()
        c.execute("DELETE FROM mfp_scans WHERE owner_user_id=? OR owner_scope=?", (uid, f"u{uid}"))
    _remove_result_files([r["result_path"] for r in rows])
    return n


def purge_subject(subject_type: str, subject_key: str) -> int:
    """群組 / 組織單位被刪除時，從所有共用事務機的成員裡拿掉。"""
    p = db_path()
    if not p.exists():
        return 0
    c = _conn()
    with db.tx(c):
        return c.execute("DELETE FROM mfp_members WHERE subject_type=? AND subject_key=?",
                         (str(subject_type), str(subject_key))).rowcount


# --------------------------------------------------------------------------- 看得到哪些

def list_visible(owner: str, subjects: list[tuple[str, str]], *, all_shared: bool = False) -> list[dict]:
    """「我的事務機」：自己新增的＋自己是成員的共用事務機（`all_shared`：未啟用登入時全部共用的都看得到）。"""
    own = list_for(owner)
    c = _conn()
    if all_shared:
        rows = c.execute("SELECT * FROM mfp_connections WHERE owner_scope=? ORDER BY display_name",
                         (SHARED,)).fetchall()
    elif subjects:
        cond = " OR ".join(["(m.subject_type=? AND m.subject_key=?)"] * len(subjects))
        args: list[Any] = [SHARED]
        for st, sk in subjects:
            args += [str(st), str(sk)]
        rows = c.execute(
            "SELECT DISTINCT c.* FROM mfp_connections c JOIN mfp_members m ON m.conn_id=c.id "
            f"WHERE c.owner_scope=? AND ({cond}) ORDER BY c.display_name", args).fetchall()
    else:
        rows = []
    return own + [_row(r) for r in rows]


def get_visible(owner: str, subjects: list[tuple[str, str]], conn_id: int, *,
                all_shared: bool = False, with_report: bool = False) -> Optional[dict]:
    mine = get_for(owner, conn_id, with_report)
    if mine:
        return mine
    r = _conn().execute("SELECT * FROM mfp_connections WHERE id=? AND owner_scope=?",
                        (int(conn_id), SHARED)).fetchone()
    if not r:
        return None
    if all_shared or is_member(int(conn_id), subjects):
        return _row(r, with_report)
    return None


def is_member(conn_id: int, subjects: list[tuple[str, str]]) -> bool:
    if not subjects:
        return False
    cond = " OR ".join(["(subject_type=? AND subject_key=?)"] * len(subjects))
    args: list[Any] = [int(conn_id)]
    for st, sk in subjects:
        args += [str(st), str(sk)]
    return _conn().execute(f"SELECT 1 FROM mfp_members WHERE conn_id=? AND ({cond}) LIMIT 1",
                           args).fetchone() is not None


# --------------------------------------------------------------------------- 共用事務機（管理員）

def list_shared() -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM mfp_connections WHERE owner_scope=? ORDER BY display_name",
                     (SHARED,)).fetchall()
    counts = {r["conn_id"]: r["n"] for r in c.execute(
        "SELECT conn_id, COUNT(*) AS n FROM mfp_members GROUP BY conn_id").fetchall()}
    out = []
    for r in rows:
        d = _row(r)
        d["member_count"] = counts.get(r["id"], 0)
        out.append(d)
    return out


#: 成員的主體種類（跟權限系統一樣；組織單位的 key 是 DN）。
MEMBER_TYPES = ("user", "group", "ou")
MAX_MEMBERS = 500


def get_members(conn_id: int) -> list[dict]:
    rows = _conn().execute(
        "SELECT subject_type, subject_key FROM mfp_members WHERE conn_id=? ORDER BY subject_type, subject_key",
        (int(conn_id),)).fetchall()
    return [{"type": r["subject_type"], "key": r["subject_key"]} for r in rows]


def set_members(conn_id: int, members: list[tuple[str, str]]) -> list[dict]:
    """整份換掉成員清單（只限共用事務機）。種類不對或太多丟 `ValueError`。"""
    if get_for(SHARED, conn_id) is None:
        raise LookupError("not a shared connection")
    clean: list[tuple[str, str]] = []
    for st, sk in members:
        st, sk = str(st), str(sk).strip()
        if st not in MEMBER_TYPES or not sk or len(sk) > 500:
            raise ValueError("invalid member")
        if st in ("user", "group") and not sk.isdigit():
            raise ValueError("invalid member")
        if (st, sk) not in clean:
            clean.append((st, sk))
    if len(clean) > MAX_MEMBERS:
        raise ValueError("too many members")
    c = _conn()
    now = time.time()
    with db.tx(c):
        c.execute("DELETE FROM mfp_members WHERE conn_id=?", (int(conn_id),))
        c.executemany("INSERT INTO mfp_members(conn_id, subject_type, subject_key, added_at) VALUES (?,?,?,?)",
                      [(int(conn_id), st, sk, now) for st, sk in clean])
    return get_members(conn_id)


# --------------------------------------------------------------------------- 掃描紀錄

#: 每個擁有者保留最近幾筆掃描紀錄。
MAX_SCANS_PER_OWNER = 50
#: 「先暫存、最後合併」的掃描（`workspace_note='HELD'`）：保留期內不算進上面的筆數、不會被擠掉
#: —— 不然暫存了十幾次再掃，最早那幾頁的紀錄與檔案就被刪了（合併時才發現少頁）。
HELD_NOTE = "HELD"
HELD_TTL = 24 * 3600


def _scan_row(r: sqlite3.Row) -> dict[str, Any]:
    d = {k: r[k] for k in r.keys()}
    for k in ("settings", "error_detail", "items"):
        try:
            d[k] = json.loads(d[k]) if d[k] else None
        except ValueError:
            d[k] = None
    d["tls_verified"] = None if d.get("tls_verified") is None else bool(d["tls_verified"])
    return d


JOB_KINDS = ("scan", "box", "share")


def create_scan(scan_id: str, *, conn_id: int, owner: str, owner_user_id: Optional[int], device: str,
                conn_name: str, settings: dict, file_name: str, kind: str = "scan") -> dict:
    _check_owner(owner)
    if kind not in JOB_KINDS:
        raise ValueError(kind)
    c = _conn()
    with db.tx(c):
        c.execute("INSERT INTO mfp_scans(id, conn_id, owner_scope, owner_user_id, device, conn_name, status,"
                  " settings, file_name, created_at, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (scan_id, int(conn_id), owner, owner_user_id, device, (conn_name or "")[:64], "queued",
                   json.dumps(settings, ensure_ascii=False), file_name, time.time(), kind))
        old = c.execute("SELECT id, result_path FROM mfp_scans WHERE owner_scope=? AND status NOT IN"
                        " ('queued','running') AND NOT (COALESCE(workspace_note,'')=? AND finished_at>?)"
                        " ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                        (owner, HELD_NOTE, time.time() - HELD_TTL, MAX_SCANS_PER_OWNER)).fetchall()
        if old:
            c.executemany("DELETE FROM mfp_scans WHERE id=?", [(r["id"],) for r in old])
    _remove_result_files([r["result_path"] for r in old])
    return get_scan(owner, scan_id)  # type: ignore[return-value]


_SCAN_FIELDS = {"status", "started_at", "finished_at", "pages", "bytes", "result_path", "workspace_file_id",
                "workspace_note", "error_code", "error_detail", "settings", "tls_verified", "items", "file_name"}
#: `items`（每份文件、每個檔案）最多存多少（位元組）—— 每次列清單都會讀。
MAX_ITEMS_BYTES = 256 * 1024


def update_scan(scan_id: str, **fields) -> None:
    sets, args = [], []
    for k, v in fields.items():
        if k not in _SCAN_FIELDS:
            raise ValueError(k)
        if k in ("settings", "error_detail", "items") and v is not None:
            v = json.dumps(v, ensure_ascii=False)
            if len(v.encode("utf-8")) > MAX_ITEMS_BYTES:
                raise ValueError(f"{k} too large")
        if k == "tls_verified" and v is not None:
            v = 1 if v else 0
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return
    c = _conn()
    with db.tx(c):
        c.execute(f"UPDATE mfp_scans SET {', '.join(sets)} WHERE id=?", (*args, scan_id))


def finish_scan(scan_id: str, *, status: str, **fields) -> None:
    update_scan(scan_id, status=status, finished_at=time.time(), **fields)


def get_scan(owner: str, scan_id: str) -> Optional[dict]:
    r = _conn().execute("SELECT * FROM mfp_scans WHERE id=? AND owner_scope=?",
                        (str(scan_id), _check_owner(owner))).fetchone()
    return _scan_row(r) if r else None


def get_scan_any(scan_id: str) -> Optional[dict]:
    r = _conn().execute("SELECT * FROM mfp_scans WHERE id=?", (str(scan_id),)).fetchone()
    return _scan_row(r) if r else None


def list_held(owner: str, conn_id: Optional[int] = None) -> list[dict]:
    """還在保留期內、暫存著還沒合併的掃描（舊的在前）。"""
    sql = ("SELECT * FROM mfp_scans WHERE owner_scope=? AND kind='scan' AND status='done'"
           " AND workspace_note=? AND finished_at>?")
    args: list = [_check_owner(owner), HELD_NOTE, time.time() - HELD_TTL]
    if conn_id is not None:
        sql += " AND conn_id=?"
        args.append(int(conn_id))
    return [_scan_row(r) for r in _conn().execute(sql + " ORDER BY created_at", args).fetchall()]


def count_held_or_pending(owner: str) -> int:
    """暫存中＋還在排隊 / 掃描中的暫存掃描（上限檢查用）。"""
    r = _conn().execute("SELECT COUNT(*) AS n FROM mfp_scans WHERE owner_scope=? AND kind='scan' AND"
                        " ((status='done' AND workspace_note=? AND finished_at>?) OR"
                        "  (status IN ('queued','running') AND settings LIKE '%\"hold\": true%'))",
                        (_check_owner(owner), HELD_NOTE, time.time() - HELD_TTL)).fetchone()
    return int(r["n"])


def list_scans(owner: str, limit: int = 20) -> list[dict]:
    rows = _conn().execute("SELECT * FROM mfp_scans WHERE owner_scope=? ORDER BY created_at DESC LIMIT ?",
                           (_check_owner(owner), max(1, min(int(limit), MAX_SCANS_PER_OWNER)))).fetchall()
    return [_scan_row(r) for r in rows]


def mark_interrupted_scans() -> int:
    """服務重啟：還在排隊或掃描中的（隊伍只在記憶體裡）一律標成中斷。"""
    p = db_path()
    if not p.exists():
        return 0
    c = _conn()
    with db.tx(c):
        return c.execute("UPDATE mfp_scans SET status='error', error_code='SCAN_INTERRUPTED', finished_at=?"
                         " WHERE status IN ('queued','running')", (time.time(),)).rowcount


def _remove_result_files(paths: list) -> None:
    import shutil
    from ..config import settings
    root = (settings.temp_dir / "mfp-scans").resolve()
    for rp in paths:
        if not rp:
            continue
        try:
            rel = Path(rp).resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        if len(rel.parts) >= 2:                 # 作業自己的目錄（信箱取件的檔案在它底下一層）
            shutil.rmtree(root / rel.parts[0], ignore_errors=True)


# --------------------------------------------------------------------------- 信任與偏好（v1.17.5）

#: 可以存的欄位與檢查方式。`trust`＝信任的憑證指紋；`http` / `plain`＝同意以未加密方式傳送；其餘是上次用的值。
#: **密碼一律不存。**
PREF_KINDS = ("box", "share")
_PREF_RULES: dict[str, Any] = {
    "trust": lambda v: isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) is not None,
    "http": lambda v: isinstance(v, bool),
    "plain": lambda v: isinstance(v, bool),
    "box": lambda v: isinstance(v, (str, int)) and re.fullmatch(r"\d{1,9}", str(v)) is not None,
    "user": lambda v: isinstance(v, str) and len(v) <= 128 and "\x00" not in v,
    "target": lambda v: isinstance(v, str) and re.fullmatch(r"(https?):\d{1,5}", v) is not None,
    "route": lambda v: isinstance(v, str) and re.fullmatch(r"[a-z_]{2,40}", v) is not None,
    "mode": lambda v: isinstance(v, str) and re.fullmatch(r"[a-z_]{2,20}", v) is not None,
}
#: 共用事務機：這幾個由管理員決定、成員沿用（管理員確認過的憑證，成員不用、也不能自己改信任別的）
SHARED_FIELDS = ("trust", "http", "plain")


def clean_prefs(patch: Any) -> dict:
    """只留允許的欄位、型別正確的值；空字串＝清掉（存成 None）。不認得的欄位丟掉，不報錯。"""
    out: dict[str, Any] = {}
    if not isinstance(patch, dict):
        return out
    for k, v in patch.items():
        rule = _PREF_RULES.get(k)
        if rule is None:
            continue
        if v is None or v == "":
            out[k] = None
        elif rule(v):
            out[k] = str(v) if k == "box" else v
    return out


def _get_prefs(c, scope: str, conn_id: int, kind: str) -> dict:
    r = c.execute("SELECT data FROM mfp_prefs WHERE owner_scope=? AND conn_id=? AND kind=?",
                  (scope, int(conn_id), kind)).fetchone()
    if not r:
        return {}
    try:
        d = json.loads(r["data"])
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def get_prefs(owner: str, conn: dict, kind: str) -> dict:
    """這個人用這台時的設定。共用事務機：信任與同意以管理員那一份為準（有設的話），其餘用自己的。"""
    if kind not in PREF_KINDS:
        raise ValueError(kind)
    c = _conn()
    mine = _get_prefs(c, _check_owner(owner), conn["id"], kind)
    out = {k: v for k, v in mine.items() if v is not None}
    if conn.get("shared"):
        admin = _get_prefs(c, SHARED, conn["id"], kind)
        for k in SHARED_FIELDS:
            if admin.get(k) is not None:
                out[k] = admin[k]
        out["managed"] = [k for k in SHARED_FIELDS if admin.get(k) is not None]
    return out


def save_prefs(owner: str, conn: dict, kind: str, patch: dict, *, admin: bool) -> dict:
    """存設定。共用事務機的信任與同意：管理員存在共用那一份；成員不可以蓋掉管理員確認過的憑證
    （回 `StoreError("PREF_MANAGED")`），管理員沒設過的話，成員可以替自己信任（跟個人事務機一樣）。"""
    if kind not in PREF_KINDS:
        raise ValueError(kind)
    clean = clean_prefs(patch)
    scope = _check_owner(owner)
    c = _conn()
    with db.tx(c):
        if conn.get("shared"):
            shared_part = {k: v for k, v in clean.items() if k in SHARED_FIELDS}
            cur = _get_prefs(c, SHARED, conn["id"], kind)
            if shared_part:
                if admin:
                    _put(c, SHARED, conn["id"], kind, {**cur, **shared_part})
                else:
                    locked = [k for k in shared_part if cur.get(k) is not None and cur.get(k) != shared_part[k]]
                    if locked:
                        raise StoreError("PREF_MANAGED", "這台共用事務機的憑證與連線方式由管理員設定；設備換了憑證，請管理員確認")
                clean = {k: v for k, v in clean.items() if k not in SHARED_FIELDS or (not admin and k not in cur)}
        if clean:
            cur = _get_prefs(c, scope, conn["id"], kind)
            _put(c, scope, conn["id"], kind, {**cur, **clean})
    return get_prefs(owner, conn, kind)


def _put(c, scope: str, conn_id: int, kind: str, data: dict) -> None:
    data = {k: v for k, v in data.items() if v is not None}
    c.execute("INSERT INTO mfp_prefs(owner_scope, conn_id, kind, data, updated_at) VALUES (?,?,?,?,?) "
              "ON CONFLICT(owner_scope, conn_id, kind) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
              (scope, int(conn_id), kind, json.dumps(data, ensure_ascii=False), time.time()))


# --------------------------------------------------------------------------- 未啟用登入時新增的（v1.17.5）

def count_local() -> int:
    """未啟用登入時新增的事務機（記在「本機」名下）。啟用登入之後任何帳號都看不到它們。"""
    p = db_path()
    if not p.exists():
        return 0
    return _conn().execute("SELECT COUNT(*) FROM mfp_connections WHERE owner_scope='local'").fetchone()[0]


def adopt_local_as_shared() -> int:
    """把未啟用登入時新增的事務機轉成共用事務機（管理員按了才做；之後再加成員）。
    已經有同一個位址與連線方式的共用事務機就不轉（留著，不覆蓋），也不超過共用上限。"""
    c = _conn()
    moved = 0
    with db.tx(c):
        rows = c.execute("SELECT id, host, port, scheme FROM mfp_connections WHERE owner_scope='local' "
                         "ORDER BY created_at").fetchall()
        n_shared = c.execute("SELECT COUNT(*) FROM mfp_connections WHERE owner_scope=?", (SHARED,)).fetchone()[0]
        for r in rows:
            if n_shared >= MAX_SHARED:
                break
            dup = c.execute("SELECT 1 FROM mfp_connections WHERE owner_scope=? AND host=? AND COALESCE(port,0)=COALESCE(?,0) "
                            "AND COALESCE(scheme,'')=COALESCE(?,'')", (SHARED, r["host"], r["port"], r["scheme"])).fetchone()
            if dup:
                continue
            c.execute("UPDATE mfp_connections SET owner_scope=?, owner_user_id=NULL, updated_at=? WHERE id=?",
                      (SHARED, time.time(), r["id"]))
            # 那時候記的信任與同意（「本機」那一份）成為共用的那一份
            for pr in c.execute("SELECT kind, data FROM mfp_prefs WHERE owner_scope='local' AND conn_id=?", (r["id"],)).fetchall():
                try:
                    d = json.loads(pr["data"])
                except ValueError:
                    d = {}
                part = {k: v for k, v in (d or {}).items() if k in SHARED_FIELDS}
                if part:
                    _put(c, SHARED, r["id"], pr["kind"], part)
            moved += 1
            n_shared += 1
    return moved
