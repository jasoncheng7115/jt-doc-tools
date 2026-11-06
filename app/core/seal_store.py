"""用印申請：流程範本、案件、版本、核准票、事件（`data/seal_requests.sqlite`）與案件檔案。

規格 `docs-share/seal-approval/jtdt-seal-approval-spec-v1.md`。這一層只管存取，規則在 `seal_flow`。

* 案件檔案放 `<資料目錄>/seal_cases/<case_id>/v<版本>/`（不放暫存區、不放工作區）。
  `v<已送出版本+1>` 是「編輯中」那一份：草稿、補件時改的都是它，送出時才變成正式版本。
* 每次送出都是新版本，當下的流程關卡、候選人、文件雜湊、印章圖雜湊一起凍結在 `case_versions`。
* 狀態轉換一律 `UPDATE … WHERE id=? AND rev=?`（樂觀鎖）：兩個人同時按，只有一個成功。
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import db

CASE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def db_path() -> Path:
    from ..config import settings
    return settings.data_dir / "seal_requests.sqlite"


def cases_root() -> Path:
    from ..config import settings
    return settings.data_dir / "seal_cases"


def _m1_initial(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS seal_flows (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            enabled       INTEGER NOT NULL DEFAULT 1,
            version       INTEGER NOT NULL DEFAULT 1,
            applicants    TEXT NOT NULL DEFAULT '[]',
            assets        TEXT NOT NULL DEFAULT '[]',
            stages        TEXT NOT NULL DEFAULT '[]',
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seal_cases (
            id              TEXT PRIMARY KEY,
            number          TEXT NOT NULL UNIQUE,
            applicant_id    INTEGER NOT NULL,
            applicant_label TEXT NOT NULL DEFAULT '',
            flow_id         INTEGER,
            flow_name       TEXT NOT NULL DEFAULT '',
            subject         TEXT NOT NULL DEFAULT '',
            purpose         TEXT NOT NULL DEFAULT '',
            recipient       TEXT NOT NULL DEFAULT '',
            doc_type        TEXT NOT NULL DEFAULT '',
            due_date        TEXT NOT NULL DEFAULT '',
            note            TEXT NOT NULL DEFAULT '',
            assets          TEXT NOT NULL DEFAULT '[]',
            placements      TEXT NOT NULL DEFAULT '[]',
            state           TEXT NOT NULL,
            stage_idx       INTEGER NOT NULL DEFAULT 0,
            version         INTEGER NOT NULL DEFAULT 0,
            rev             INTEGER NOT NULL DEFAULT 1,
            created_at      REAL NOT NULL,
            updated_at      REAL NOT NULL,
            submitted_at    REAL,
            closed_at       REAL
        );
        CREATE INDEX IF NOT EXISTS idx_seal_cases_applicant ON seal_cases(applicant_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_seal_cases_state ON seal_cases(state, updated_at DESC);
        CREATE TABLE IF NOT EXISTS seal_case_versions (
            case_id       TEXT NOT NULL REFERENCES seal_cases(id) ON DELETE CASCADE,
            version       INTEGER NOT NULL,
            flow_version  INTEGER NOT NULL,
            doc_name      TEXT NOT NULL,
            doc_sha256    TEXT NOT NULL,
            doc_pages     INTEGER NOT NULL,
            assets        TEXT NOT NULL,
            placements    TEXT NOT NULL,
            stages        TEXT NOT NULL,
            attachments   TEXT NOT NULL,
            fields        TEXT NOT NULL,
            created_at    REAL NOT NULL,
            PRIMARY KEY (case_id, version)
        );
        CREATE TABLE IF NOT EXISTS seal_votes (
            case_id     TEXT NOT NULL REFERENCES seal_cases(id) ON DELETE CASCADE,
            version     INTEGER NOT NULL,
            stage_idx   INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            user_label  TEXT NOT NULL DEFAULT '',
            decision    TEXT NOT NULL,
            comment     TEXT NOT NULL DEFAULT '',
            ts          REAL NOT NULL,
            PRIMARY KEY (case_id, version, stage_idx, user_id)
        );
        CREATE TABLE IF NOT EXISTS seal_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id     TEXT NOT NULL REFERENCES seal_cases(id) ON DELETE CASCADE,
            ts          REAL NOT NULL,
            actor_id    INTEGER,
            actor_label TEXT NOT NULL DEFAULT '',
            action      TEXT NOT NULL,
            version     INTEGER NOT NULL DEFAULT 0,
            stage_idx   INTEGER,
            comment     TEXT NOT NULL DEFAULT '',
            detail      TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_seal_events_case ON seal_events(case_id, ts);
    """)


def _m2_stamping(conn: sqlite3.Connection) -> None:
    """第 3 版：保管人用印、成果、作廢、取件紀錄。"""
    for ddl in (
        "ALTER TABLE seal_cases ADD COLUMN final_placements TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE seal_cases ADD COLUMN stamp_exec TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE seal_cases ADD COLUMN stamp_started_at REAL",
        "ALTER TABLE seal_cases ADD COLUMN stamp_error TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE seal_cases ADD COLUMN stamped_by INTEGER",
        "ALTER TABLE seal_cases ADD COLUMN stamped_by_label TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE seal_cases ADD COLUMN stamped_sha256 TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE seal_cases ADD COLUMN done_at REAL",
        "ALTER TABLE seal_cases ADD COLUMN first_download_at REAL",
        "ALTER TABLE seal_cases ADD COLUMN void_reason TEXT NOT NULL DEFAULT ''",
    ):
        conn.execute(ddl)


def _m3_watermark(conn: sqlite3.Connection) -> None:
    """流程可以設定用印時附加浮水印；送出時凍結到版本裡（之後改流程不影響已送出的案件）。"""
    conn.execute("ALTER TABLE seal_flows ADD COLUMN watermark TEXT NOT NULL DEFAULT '{}'")
    conn.execute("ALTER TABLE seal_case_versions ADD COLUMN watermark TEXT NOT NULL DEFAULT '{}'")


_MIGRATIONS = [_m1_initial, _m2_stamping, _m3_watermark]
_INITED: set[str] = set()
_INIT_LOCK = threading.Lock()


def conn() -> sqlite3.Connection:
    p = db_path()
    key = str(p)
    if key not in _INITED:
        with _INIT_LOCK:
            if key not in _INITED:
                p.parent.mkdir(parents=True, exist_ok=True)
                db.migrate(p, _MIGRATIONS)
                _INITED.add(key)
    return db.get_conn(p)


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _l(s: Optional[str], default: Any) -> Any:
    try:
        return json.loads(s) if s else default
    except ValueError:
        return default


# --------------------------------------------------------------------------- 流程範本

def _flow_row(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "description": r["description"], "enabled": bool(r["enabled"]),
            "version": r["version"], "applicants": _l(r["applicants"], []), "assets": _l(r["assets"], []),
            "stages": _l(r["stages"], []), "watermark": _l(r["watermark"], {}) or {},
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


def list_flows() -> list[dict]:
    return [_flow_row(r) for r in conn().execute("SELECT * FROM seal_flows ORDER BY name, id").fetchall()]


def get_flow(flow_id: int) -> Optional[dict]:
    r = conn().execute("SELECT * FROM seal_flows WHERE id=?", (int(flow_id),)).fetchone()
    return _flow_row(r) if r else None


def save_flow(flow_id: Optional[int], data: dict) -> dict:
    """新增或修改（修改＝版本 +1；進行中的案件用自己凍結的那份，不受影響）。`data` 已經過 `seal_flow` 檢查。"""
    c = conn()
    now = time.time()
    with db.tx(c):
        if flow_id is None:
            cur = c.execute(
                "INSERT INTO seal_flows(name, description, enabled, version, applicants, assets, stages, "
                "watermark, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (data["name"], data["description"], 1 if data["enabled"] else 0, 1, _j(data["applicants"]),
                 _j(data["assets"]), _j(data["stages"]), _j(data.get("watermark") or {}), now, now))
            flow_id = cur.lastrowid
        else:
            cur = c.execute(
                "UPDATE seal_flows SET name=?, description=?, enabled=?, version=version+1, applicants=?, "
                "assets=?, stages=?, watermark=?, updated_at=? WHERE id=?",
                (data["name"], data["description"], 1 if data["enabled"] else 0, _j(data["applicants"]),
                 _j(data["assets"]), _j(data["stages"]), _j(data.get("watermark") or {}), now, int(flow_id)))
            if cur.rowcount == 0:
                raise LookupError("flow not found")
    return get_flow(int(flow_id))


def delete_flow(flow_id: int) -> bool:
    """刪除範本。已經用過的案件保留自己凍結的那份與名稱，`flow_id` 不清（只是查不到範本）。"""
    c = conn()
    with db.tx(c):
        return c.execute("DELETE FROM seal_flows WHERE id=?", (int(flow_id),)).rowcount > 0


# --------------------------------------------------------------------------- 案件

CASE_FIELDS = ("subject", "purpose", "recipient", "doc_type", "due_date", "note")


def _case_row(r: sqlite3.Row) -> dict:
    d = {k: r[k] for k in r.keys()}
    d["assets"] = _l(r["assets"], [])
    d["placements"] = _l(r["placements"], [])
    d["final_placements"] = _l(d.get("final_placements"), [])
    return d


def _next_number(c: sqlite3.Connection, now: float) -> str:
    day = time.strftime("%Y%m%d", time.localtime(now))
    n = c.execute("SELECT COUNT(*) FROM seal_cases WHERE number LIKE ?", (f"SR-{day}-%",)).fetchone()[0]
    return f"SR-{day}-{n + 1:04d}"


def create_case(applicant_id: int, applicant_label: str, flow: dict) -> dict:
    c = conn()
    now = time.time()
    cid = uuid.uuid4().hex
    with db.tx(c):
        number = _next_number(c, now)
        c.execute(
            "INSERT INTO seal_cases(id, number, applicant_id, applicant_label, flow_id, flow_name, state, "
            "stage_idx, version, rev, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, number, int(applicant_id), applicant_label, flow["id"], flow["name"], "draft", 0, 0, 1, now, now))
    return get_case(cid)


def get_case(case_id: str) -> Optional[dict]:
    if not isinstance(case_id, str) or not CASE_ID_RE.match(case_id):
        return None
    r = conn().execute("SELECT * FROM seal_cases WHERE id=?", (case_id,)).fetchone()
    return _case_row(r) if r else None


def update_case(case_id: str, rev: int, **cols) -> Optional[dict]:
    """改欄位＋ rev +1；`rev` 對不上（別人剛改過）回 None。"""
    allowed = set(CASE_FIELDS) | {"flow_id", "flow_name", "assets", "placements", "state", "stage_idx",
                                  "version", "submitted_at", "closed_at", "final_placements", "stamp_exec",
                                  "stamp_started_at", "stamp_error", "stamped_by", "stamped_by_label",
                                  "stamped_sha256", "done_at", "first_download_at", "void_reason"}
    sets, args = [], []
    for k, v in cols.items():
        if k not in allowed:
            raise KeyError(k)
        sets.append(f"{k}=?")
        args.append(_j(v) if k in ("assets", "placements", "final_placements") else v)
    sets += ["rev=rev+1", "updated_at=?"]
    args += [time.time(), case_id, int(rev)]
    c = conn()
    with db.tx(c):
        n = c.execute(f"UPDATE seal_cases SET {', '.join(sets)} WHERE id=? AND rev=?", args).rowcount
    return get_case(case_id) if n else None


def list_cases(where: str = "1=1", args: tuple = (), limit: int = 200) -> list[dict]:
    rows = conn().execute(f"SELECT * FROM seal_cases WHERE {where} ORDER BY updated_at DESC LIMIT ?",
                          (*args, int(limit))).fetchall()
    return [_case_row(r) for r in rows]


def delete_case(case_id: str) -> None:
    c = conn()
    with db.tx(c):
        c.execute("DELETE FROM seal_cases WHERE id=?", (case_id,))
    shutil.rmtree(case_dir(case_id), ignore_errors=True)


# --------------------------------------------------------------------------- 版本、票、事件

def _version_row(r: sqlite3.Row) -> dict:
    return {"version": r["version"], "flow_version": r["flow_version"], "doc_name": r["doc_name"],
            "doc_sha256": r["doc_sha256"], "doc_pages": r["doc_pages"], "assets": _l(r["assets"], []),
            "placements": _l(r["placements"], []), "stages": _l(r["stages"], []),
            "attachments": _l(r["attachments"], []), "fields": _l(r["fields"], {}),
            "watermark": _l(r["watermark"], {}) or {}, "created_at": r["created_at"]}


def add_version(c: sqlite3.Connection, case_id: str, v: dict) -> None:
    """在呼叫端的交易裡寫入（跟狀態轉換一起成功或一起失敗）。"""
    c.execute(
        "INSERT INTO seal_case_versions(case_id, version, flow_version, doc_name, doc_sha256, doc_pages, assets, "
        "placements, stages, attachments, fields, watermark, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (case_id, v["version"], v["flow_version"], v["doc_name"], v["doc_sha256"], v["doc_pages"], _j(v["assets"]),
         _j(v["placements"]), _j(v["stages"]), _j(v["attachments"]), _j(v["fields"]),
         _j(v.get("watermark") or {}), time.time()))


def get_version(case_id: str, version: int) -> Optional[dict]:
    r = conn().execute("SELECT * FROM seal_case_versions WHERE case_id=? AND version=?",
                       (case_id, int(version))).fetchone()
    return _version_row(r) if r else None


def set_version_stages(case_id: str, version: int, stages: list) -> None:
    c = conn()
    with db.tx(c):
        c.execute("UPDATE seal_case_versions SET stages=? WHERE case_id=? AND version=?",
                  (_j(stages), case_id, int(version)))


def votes(case_id: str, version: int, stage_idx: Optional[int] = None) -> list[dict]:
    q = "SELECT * FROM seal_votes WHERE case_id=? AND version=?"
    args: list[Any] = [case_id, int(version)]
    if stage_idx is not None:
        q += " AND stage_idx=?"
        args.append(int(stage_idx))
    return [dict(r) for r in conn().execute(q + " ORDER BY ts", args).fetchall()]


def add_event(c: sqlite3.Connection, case_id: str, action: str, *, actor_id: Optional[int], actor_label: str,
              version: int, stage_idx: Optional[int] = None, comment: str = "", detail: Optional[dict] = None) -> None:
    c.execute(
        "INSERT INTO seal_events(case_id, ts, actor_id, actor_label, action, version, stage_idx, comment, detail) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (case_id, time.time(), actor_id, actor_label, action, int(version), stage_idx, comment, _j(detail or {})))


def events(case_id: str) -> list[dict]:
    out = []
    for r in conn().execute("SELECT * FROM seal_events WHERE case_id=? ORDER BY ts, id", (case_id,)).fetchall():
        d = dict(r)
        d["detail"] = _l(r["detail"], {})
        out.append(d)
    return out


def participant_case_ids(user_id: int) -> set[str]:
    """這個人投過票或做過動作的案件（「我已處理」）。"""
    c = conn()
    a = {r[0] for r in c.execute("SELECT DISTINCT case_id FROM seal_votes WHERE user_id=?", (int(user_id),))}
    b = {r[0] for r in c.execute("SELECT DISTINCT case_id FROM seal_events WHERE actor_id=? AND action IN "
                                 "('approve','return','reject')", (int(user_id),))}
    return a | b


# --------------------------------------------------------------------------- 檔案

def case_dir(case_id: str) -> Path:
    if not CASE_ID_RE.match(case_id or ""):
        raise ValueError("bad case id")
    return cases_root() / case_id


def version_dir(case_id: str, version: int) -> Path:
    return case_dir(case_id) / f"v{int(version)}"
