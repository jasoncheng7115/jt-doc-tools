"""名片辨識的資料：辨識批次、頁、卡面、聯絡人（`business_cards.sqlite`＋`<資料目錄>/business_cards/<批次>/`）。

**存取控制在這一層**：每個批次有擁有者（`owner`：啟用登入＝`u<使用者 id>`，單機＝`local`），
所有讀寫都帶擁有者條件；別人的批次跟不存在一樣（回 `NotFound`），不分「不存在」與「沒權限」。
編號一律 32 位十六進位，檔案路徑只由編號組成，到不了資料夾外面。

名片簿（個人 / 共用、查詢權限、保留期限）是第 2 版，另外的表；這裡只有「這一次的辨識工作」。
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
from typing import Any, Iterable, Optional

from ...core import db

ID_RE = re.compile(r"^[0-9a-f]{32}$")
BATCH_STATUSES = ("detecting", "frames", "recognizing", "review", "failed")
CARD_STATUSES = ("pending", "ocr", "done", "failed", "excluded")
CONTACT_STATUSES = ("check", "confirmed", "excluded")

#: 上限（規格第 7 節的初始建議；依主機壓測再調）
MAX_FILES = 50
MAX_PAGES = 100
MAX_CARDS = 300
MAX_ACTIVE_BATCHES = 20
#: 批次預設保留天數（管理員在「檔案保留 / 清理」改 `business_cards_days`；0 或負數＝不自動刪）
DEFAULT_KEEP_DAYS = 30


def keep_days() -> int:
    """目前的保留天數（讀全站保留設定）。讀不到就用預設，不會變成「永久保留」。"""
    try:
        from ...core import retention
        return int(retention.get().get("business_cards_days", DEFAULT_KEEP_DAYS))
    except Exception:
        return DEFAULT_KEEP_DAYS


def expires_at_of(created_at: float, days: Optional[int] = None) -> Optional[float]:
    """畫面顯示的「幾時自動刪除」：依**現在的**設定從建立時間起算（管理員改了設定，舊批次也跟著改）。"""
    days = keep_days() if days is None else days
    return float(created_at) + days * 86400 if days > 0 else None


class NotFound(Exception):
    pass


class LimitError(Exception):
    pass


def db_path() -> Path:
    from ...config import settings
    return settings.data_dir / "business_cards.sqlite"


def files_root() -> Path:
    from ...config import settings
    return settings.data_dir / "business_cards"


def batch_dir(batch_id: str) -> Path:
    _check_id(batch_id)
    return files_root() / batch_id


def _check_id(x: str) -> str:
    if not isinstance(x, str) or not ID_RE.fullmatch(x):
        raise NotFound()
    return x


def new_id() -> str:
    return uuid.uuid4().hex


def _m1_initial(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bc_batches (
            id          TEXT PRIMARY KEY,
            owner       TEXT NOT NULL,
            owner_label TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            mode        TEXT NOT NULL,              -- single / multi
            status      TEXT NOT NULL,
            notes       TEXT NOT NULL DEFAULT '{}', -- 整批的活動 / 來源 / 標籤 / 備註（人工輸入，不是名片原文）
            langs       TEXT NOT NULL DEFAULT '',
            error       TEXT NOT NULL DEFAULT '',
            expires_at  REAL NOT NULL DEFAULT 0     -- 0＝不自動刪
        );
        CREATE INDEX IF NOT EXISTS bc_batches_owner ON bc_batches(owner, created_at);
        CREATE TABLE IF NOT EXISTS bc_pages (
            id         TEXT PRIMARY KEY,
            batch_id   TEXT NOT NULL REFERENCES bc_batches(id) ON DELETE CASCADE,
            idx        INTEGER NOT NULL,
            filename   TEXT NOT NULL DEFAULT '',
            page_no    INTEGER NOT NULL DEFAULT 1,
            width      INTEGER NOT NULL DEFAULT 0,
            height     INTEGER NOT NULL DEFAULT 0,
            status     TEXT NOT NULL DEFAULT 'pending',  -- pending / detected / zero
            multi_hint INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS bc_pages_batch ON bc_pages(batch_id, idx);
        CREATE TABLE IF NOT EXISTS bc_cards (
            id         TEXT PRIMARY KEY,
            batch_id   TEXT NOT NULL REFERENCES bc_batches(id) ON DELETE CASCADE,
            page_id    TEXT NOT NULL REFERENCES bc_pages(id) ON DELETE CASCADE,
            idx        INTEGER NOT NULL,
            quad       TEXT NOT NULL,
            rotate     INTEGER NOT NULL DEFAULT 0,
            source     TEXT NOT NULL DEFAULT 'auto',     -- auto / manual / whole
            flags      TEXT NOT NULL DEFAULT '[]',
            status     TEXT NOT NULL DEFAULT 'pending',
            error      TEXT NOT NULL DEFAULT '',
            ocr        TEXT NOT NULL DEFAULT '{}',       -- {"lines":[...], "engine", "rotation", "quality", "qr":[...]}
            memo       TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS bc_cards_batch ON bc_cards(batch_id, page_id, idx);
        CREATE TABLE IF NOT EXISTS bc_contacts (
            id         TEXT PRIMARY KEY,
            batch_id   TEXT NOT NULL REFERENCES bc_batches(id) ON DELETE CASCADE,
            card_id    TEXT NOT NULL DEFAULT '',          -- 主要來源卡面（背面在 card_ids）
            card_ids   TEXT NOT NULL DEFAULT '[]',
            idx        INTEGER NOT NULL DEFAULT 0,
            status     TEXT NOT NULL DEFAULT 'check',
            fields     TEXT NOT NULL DEFAULT '[]',
            notes      TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS bc_contacts_batch ON bc_contacts(batch_id, idx);
    """)


def _m2_books(conn: sqlite3.Connection) -> None:
    """名片簿（第 2 版）：名片簿、成員、人、每一張存進來的名片（當時的公司 / 職稱與名片圖）、互動紀錄、設定。

    辨識批次（上面那幾張表）只屬於本人、會過期；存進名片簿的才長期保留、才有分享。只新增表，不動既有資料。
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bc_books (
            id         TEXT PRIMARY KEY,
            kind       TEXT NOT NULL,                     -- personal / shared
            owner      TEXT NOT NULL DEFAULT '',          -- 個人名片簿的擁有者鍵（u<id> / local）；共用的是空字串
            name       TEXT NOT NULL DEFAULT '',
            keep_days  INTEGER NOT NULL DEFAULT 0,        -- 0 ＝永久保留；依「最後更新」起算
            created_by TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS bc_books_personal ON bc_books(owner) WHERE kind='personal';
        CREATE TABLE IF NOT EXISTS bc_book_members (
            book_id      TEXT NOT NULL REFERENCES bc_books(id) ON DELETE CASCADE,
            subject_type TEXT NOT NULL,                   -- user / group / ou（同權限系統）
            subject_key  TEXT NOT NULL,
            role         TEXT NOT NULL DEFAULT 'view',    -- view（查詢）/ edit（編修）
            PRIMARY KEY (book_id, subject_type, subject_key)
        );
        CREATE TABLE IF NOT EXISTS bc_people (
            id         TEXT PRIMARY KEY,
            book_id    TEXT NOT NULL REFERENCES bc_books(id) ON DELETE CASCADE,
            fields     TEXT NOT NULL DEFAULT '[]',        -- 目前的資料（最新一張名片＋人工修改）
            notes      TEXT NOT NULL DEFAULT '{}',
            search     TEXT NOT NULL DEFAULT '',          -- 查詢用（小寫、去空白的欄位值）
            match_keys TEXT NOT NULL DEFAULT '',          -- 找同一人用：「 e:email m:手機 n:姓名|公司 」
            created_by TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS bc_people_book ON bc_people(book_id, updated_at);
        CREATE TABLE IF NOT EXISTS bc_person_cards (
            id          TEXT PRIMARY KEY,
            person_id   TEXT NOT NULL REFERENCES bc_people(id) ON DELETE CASCADE,
            book_id     TEXT NOT NULL,
            fields      TEXT NOT NULL DEFAULT '[]',       -- 這張名片當時的內容（換工作、升職都看得到）
            notes       TEXT NOT NULL DEFAULT '{}',
            image       INTEGER NOT NULL DEFAULT 0,       -- 有沒有名片圖（檔案在 books/<名片簿>/<這筆>.jpg）
            source      TEXT NOT NULL DEFAULT 'card',     -- card / import / manual
            captured_at REAL NOT NULL,                    -- 拿到名片（辨識）的時間
            saved_at    REAL NOT NULL,
            saved_by    TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS bc_person_cards_person ON bc_person_cards(person_id, captured_at);
        CREATE TABLE IF NOT EXISTS bc_interactions (
            id         TEXT PRIMARY KEY,
            person_id  TEXT NOT NULL REFERENCES bc_people(id) ON DELETE CASCADE,
            book_id    TEXT NOT NULL,
            at         REAL NOT NULL,                     -- 互動發生的時間
            kind       TEXT NOT NULL DEFAULT 'other',     -- meeting / call / email / visit / other
            note       TEXT NOT NULL DEFAULT '',
            by_label   TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS bc_interactions_person ON bc_interactions(person_id, at);
        CREATE TABLE IF NOT EXISTS bc_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        -- 同一批裡合併兩位（正反面、重複的人）：留下合併前的樣子，解除合併時照原樣還原
        ALTER TABLE bc_contacts ADD COLUMN merged TEXT NOT NULL DEFAULT '[]';
    """)


_MIGRATIONS = [_m1_initial, _m2_books]
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
    return json.dumps(v, ensure_ascii=False)


def _l(s: str, default: Any) -> Any:
    try:
        return json.loads(s) if s else default
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- 批次

def create_batch(owner: str, owner_label: str, mode: str, notes: dict, langs: str,
                 keep: Optional[int] = None) -> str:
    if mode not in ("single", "multi"):
        raise ValueError("mode")
    c = conn()
    active = c.execute("SELECT COUNT(*) AS n FROM bc_batches WHERE owner=?", (owner,)).fetchone()["n"]
    if active >= MAX_ACTIVE_BATCHES:
        raise LimitError(f"同時最多保留 {MAX_ACTIVE_BATCHES} 個辨識批次，請先刪除用不到的")
    bid = new_id()
    now = time.time()
    with db.tx(c):
        c.execute("INSERT INTO bc_batches(id, owner, owner_label, created_at, updated_at, mode, status, notes, langs,"
                  " expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (bid, owner, owner_label[:120], now, now, mode, "detecting", _j(clean_notes(notes)), langs,
                   expires_at_of(now, keep) or 0))
    batch_dir(bid).mkdir(parents=True, exist_ok=True)
    return bid


def clean_notes(n: Optional[dict]) -> dict:
    """人工補充（活動、來源、標籤、備註）：長度限制、去控制字元；跟名片原文分開存。"""
    n = n or {}

    def s(v, lim):
        v = "" if v is None else str(v)
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v)[:lim].strip()
    tags = n.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,，、\s]+", tags) if t]
    return {"event": s(n.get("event"), 120), "source": s(n.get("source"), 120),
            "tags": [s(t, 40) for t in tags[:20] if s(t, 40)], "memo": s(n.get("memo"), 2000)}


def get_batch(owner: str, bid: str) -> dict:
    _check_id(bid)
    r = conn().execute("SELECT * FROM bc_batches WHERE id=? AND owner=?", (bid, owner)).fetchone()
    if not r:
        raise NotFound()
    d = dict(r)
    d["notes"] = _l(d["notes"], {})
    return d


def list_batches(owner: str) -> list[dict]:
    c = conn()
    rows = c.execute("SELECT * FROM bc_batches WHERE owner=? ORDER BY created_at DESC", (owner,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["notes"] = _l(d["notes"], {})
        cnt = c.execute("SELECT status, COUNT(*) AS n FROM bc_cards WHERE batch_id=? GROUP BY status", (d["id"],)).fetchall()
        d["cards"] = {x["status"]: x["n"] for x in cnt}
        ct = c.execute("SELECT status, COUNT(*) AS n FROM bc_contacts WHERE batch_id=? GROUP BY status", (d["id"],)).fetchall()
        d["contacts"] = {x["status"]: x["n"] for x in ct}
        d["pages"] = c.execute("SELECT COUNT(*) AS n FROM bc_pages WHERE batch_id=?", (d["id"],)).fetchone()["n"]
        out.append(d)
    return out


def set_batch(owner: str, bid: str, **kw) -> None:
    get_batch(owner, bid)
    allowed = {"status", "error", "notes", "mode"}
    cols = {k: v for k, v in kw.items() if k in allowed}
    if "notes" in cols:
        cols["notes"] = _j(clean_notes(cols["notes"]))
    if not cols:
        return
    cols["updated_at"] = time.time()
    c = conn()
    with db.tx(c):
        c.execute(f"UPDATE bc_batches SET {', '.join(k + '=?' for k in cols)} WHERE id=? AND owner=?",
                  (*cols.values(), bid, owner))


def delete_batch(owner: str, bid: str) -> None:
    get_batch(owner, bid)
    c = conn()
    with db.tx(c):
        c.execute("DELETE FROM bc_contacts WHERE batch_id=?", (bid,))
        c.execute("DELETE FROM bc_cards WHERE batch_id=?", (bid,))
        c.execute("DELETE FROM bc_pages WHERE batch_id=?", (bid,))
        c.execute("DELETE FROM bc_batches WHERE id=? AND owner=?", (bid, owner))
    shutil.rmtree(batch_dir(bid), ignore_errors=True)


def expire_due(now: Optional[float] = None, days: Optional[int] = None) -> int:
    """保留期限到了的批次：連同原圖、裁圖一起刪（排程呼叫）。

    依**建立時間＋現在的保留天數**判斷（不是建立當下寫進去的 `expires_at`）——管理員把保留期改短，
    舊批次也要照新設定清；改成 0（不自動刪）就一筆都不刪。還在找名片 / 辨識中的那一批不動，下一輪再清。
    """
    now = now or time.time()
    days = keep_days() if days is None else days
    if days <= 0:
        return 0
    try:
        rows = conn().execute("SELECT id, owner FROM bc_batches WHERE created_at<=? AND status NOT IN"
                              " ('detecting','recognizing')", (now - days * 86400,)).fetchall()
    except sqlite3.Error:
        return 0
    n = 0
    for r in rows:
        try:
            delete_batch(r["owner"], r["id"])
            n += 1
        except NotFound:
            pass
    return n


def purge_owner(owner: str) -> int:
    """刪除帳號時：這個人的辨識批次全部刪掉（含原圖、裁圖、聯絡人）。"""
    if not owner:
        return 0
    try:
        ids = [r["id"] for r in conn().execute("SELECT id FROM bc_batches WHERE owner=?", (owner,)).fetchall()]
    except sqlite3.Error:
        return 0
    n = 0
    for bid in ids:
        try:
            delete_batch(owner, bid)
            n += 1
        except NotFound:
            pass
    return n


def stats() -> dict:
    """給「檔案保留 / 清理」頁：佔用空間、批次數、最舊一批。"""
    size = 0
    root = files_root()
    if root.is_dir():
        for f in root.rglob("*"):
            try:
                if f.is_file():
                    size += f.stat().st_size
            except OSError:
                pass
    count, oldest = 0, None
    if db_path().exists():
        try:
            r = conn().execute("SELECT COUNT(*) AS n, MIN(created_at) AS m FROM bc_batches").fetchone()
            count, oldest = int(r["n"] or 0), r["m"]
        except sqlite3.Error:
            pass
    return {"size_mb": size / 1024 / 1024, "files": count,
            "oldest_days": None if not oldest else max(0.0, (time.time() - float(oldest)) / 86400.0)}


# --------------------------------------------------------------------------- 頁

def add_page(bid: str, idx: int, filename: str, page_no: int, width: int, height: int) -> str:
    pid = new_id()
    c = conn()
    with db.tx(c):
        c.execute("INSERT INTO bc_pages(id, batch_id, idx, filename, page_no, width, height) VALUES (?,?,?,?,?,?,?)",
                  (pid, bid, idx, filename[:200], page_no, width, height))
    return pid


def page_image_path(bid: str, pid: str) -> Path:
    _check_id(pid)
    return batch_dir(bid) / "src" / f"{pid}.jpg"


def card_image_path(bid: str, cid: str) -> Path:
    _check_id(cid)
    return batch_dir(bid) / "cards" / f"{cid}.jpg"


def pages(bid: str) -> list[dict]:
    return [dict(r) for r in conn().execute("SELECT * FROM bc_pages WHERE batch_id=? ORDER BY idx", (bid,)).fetchall()]


def get_page(owner: str, bid: str, pid: str) -> dict:
    get_batch(owner, bid)
    _check_id(pid)
    r = conn().execute("SELECT * FROM bc_pages WHERE id=? AND batch_id=?", (pid, bid)).fetchone()
    if not r:
        raise NotFound()
    return dict(r)


def set_page(pid: str, **kw) -> None:
    cols = {k: v for k, v in kw.items() if k in ("status", "multi_hint")}
    if cols:
        c = conn()
        with db.tx(c):
            c.execute(f"UPDATE bc_pages SET {', '.join(k + '=?' for k in cols)} WHERE id=?", (*cols.values(), pid))


# --------------------------------------------------------------------------- 卡面

def _card(r) -> dict:
    d = dict(r)
    d["quad"] = _l(d["quad"], [])
    d["flags"] = _l(d["flags"], [])
    d["ocr"] = _l(d["ocr"], {})
    return d


def cards(bid: str, page_id: Optional[str] = None) -> list[dict]:
    q = "SELECT c.* FROM bc_cards c JOIN bc_pages p ON p.id=c.page_id WHERE c.batch_id=?"
    args: list = [bid]
    if page_id:
        q += " AND c.page_id=?"
        args.append(page_id)
    q += " ORDER BY p.idx, c.idx"
    return [_card(r) for r in conn().execute(q, args).fetchall()]


def get_card(owner: str, bid: str, cid: str) -> dict:
    get_batch(owner, bid)
    _check_id(cid)
    r = conn().execute("SELECT * FROM bc_cards WHERE id=? AND batch_id=?", (cid, bid)).fetchone()
    if not r:
        raise NotFound()
    return _card(r)


def count_cards(bid: str) -> int:
    return conn().execute("SELECT COUNT(*) AS n FROM bc_cards WHERE batch_id=?", (bid,)).fetchone()["n"]


def replace_page_cards(bid: str, pid: str, items: list[dict]) -> list[str]:
    """一頁的卡面框整批換掉（偵測完、或使用者改框）。`items`：`{id?, quad, rotate, source, flags, memo}`。
    框沒變的卡保留辨識結果；改過框或角度的回到待辨識（只重跑受影響的卡）。回「要重新辨識」的卡面編號。"""
    c = conn()
    old = {x["id"]: x for x in cards(bid, pid)}
    keep_ids: set[str] = set()
    todo: list[str] = []
    now = time.time()
    if count_cards(bid) - len(old) + len(items) > MAX_CARDS:
        raise LimitError(f"一個批次最多 {MAX_CARDS} 張卡面")
    with db.tx(c):
        for i, it in enumerate(items):
            cid = it.get("id") if it.get("id") in old else None
            quad = _j([[round(float(x), 1), round(float(y), 1)] for x, y in it["quad"]])
            rot = int(it.get("rotate") or 0) % 360
            memo = str(it.get("memo") or "")[:500]
            if cid:
                o = old[cid]
                changed = _j(o["quad"]) != quad or o["rotate"] != rot
                c.execute("UPDATE bc_cards SET idx=?, quad=?, rotate=?, source=?, flags=?, memo=?, updated_at=?"
                          + (", status='pending', ocr='{}', error=''" if changed else "")
                          + " WHERE id=?", (i, quad, rot, it.get("source") or o["source"],
                                            _j(it.get("flags") or []), memo, now, cid))
                if changed:
                    todo.append(cid)
                keep_ids.add(cid)
            else:
                cid = new_id()
                c.execute("INSERT INTO bc_cards(id, batch_id, page_id, idx, quad, rotate, source, flags, memo, updated_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (cid, bid, pid, i, quad, rot, it.get("source") or "manual", _j(it.get("flags") or []), memo, now))
                todo.append(cid)
                keep_ids.add(cid)
        for cid in set(old) - keep_ids:
            c.execute("DELETE FROM bc_contacts WHERE batch_id=? AND card_id=?", (bid, cid))
            c.execute("DELETE FROM bc_cards WHERE id=?", (cid,))
    for cid in set(old) - keep_ids:
        card_image_path(bid, cid).unlink(missing_ok=True)
    return todo


def set_card(cid: str, **kw) -> None:
    cols = {k: v for k, v in kw.items() if k in ("status", "error", "ocr", "memo", "rotate")}
    if "ocr" in cols:
        cols["ocr"] = _j(cols["ocr"])
    cols["updated_at"] = time.time()
    c = conn()
    with db.tx(c):
        c.execute(f"UPDATE bc_cards SET {', '.join(k + '=?' for k in cols)} WHERE id=?", (*cols.values(), cid))


def claim_card(cid: str) -> bool:
    """背景辨識搶一張待辨識的卡（交易內確認狀態，兩條執行緒不會辨識同一張）。"""
    c = conn()
    with db.tx(c):
        cur = c.execute("UPDATE bc_cards SET status='ocr', updated_at=? WHERE id=? AND status='pending'",
                        (time.time(), cid))
    return cur.rowcount == 1


def reset_stuck(older_than: float = 600) -> int:
    """服務重啟時卡在「辨識中」的卡回到待辨識。"""
    c = conn()
    with db.tx(c):
        cur = c.execute("UPDATE bc_cards SET status='pending' WHERE status='ocr' AND updated_at<?",
                        (time.time() - older_than,))
    return cur.rowcount


def reset_stuck_batch(bid: str) -> int:
    """這一批背景已經停了，還標著「辨識中」的卡（中途當掉留下的）回到待辨識。"""
    c = conn()
    with db.tx(c):
        cur = c.execute("UPDATE bc_cards SET status='pending' WHERE batch_id=? AND status='ocr'", (bid,))
    return cur.rowcount


def pending_cards(bid: str) -> list[str]:
    return [r["id"] for r in conn().execute(
        "SELECT c.id FROM bc_cards c JOIN bc_pages p ON p.id=c.page_id WHERE c.batch_id=? AND c.status='pending'"
        " ORDER BY p.idx, c.idx", (bid,)).fetchall()]


# --------------------------------------------------------------------------- 聯絡人

def _contact(r) -> dict:
    d = dict(r)
    d["fields"] = _l(d["fields"], [])
    d["notes"] = _l(d["notes"], {})
    d["card_ids"] = _l(d["card_ids"], [])
    d["merged"] = _l(d.get("merged") or "[]", [])
    return d


def merge_contacts(owner: str, bid: str, keep_id: str, other_id: str) -> dict:
    """同一批裡把 other 併進 keep（正反面、同一人拿到兩張）：欄位合併、卡面都算 keep 的，other 刪掉。

    中文面與英文面：keep 已經有中文姓名 / 公司時，other 的英文姓名 / 公司放進外文欄位；其他單值欄位以 keep 為準；
    電話、Email 這類多值欄位合併、不重複。合併前的兩位原樣存在 `merged`，解除合併時還原。
    """
    from .extract import normalize_phone
    keep, other = get_contact(owner, bid, keep_id), get_contact(owner, bid, other_id)
    if keep_id == other_id:
        raise ValueError("same")
    latin = lambda v: bool(re.search(r"[A-Za-z]", v or "")) and not re.search(r"[一-鿿]", v or "")
    single = ("name", "name_en", "title", "department", "company", "company_en", "vat")
    fields = [dict(f) for f in keep["fields"]]
    have = {f["key"] for f in fields if f.get("status") != "deleted" and (f.get("value") or "").strip()}

    def nkey(f):
        v = f.get("value") or ""
        return (f["key"], normalize_phone(v) if f["key"] in ("mobile", "phone", "fax") else re.sub(r"\s+", "", v.lower()))
    seen = {nkey(f) for f in fields if f.get("status") != "deleted"}
    for f in other["fields"]:
        if f.get("status") == "deleted" or not (f.get("value") or "").strip():
            continue
        g = dict(f)
        if g["key"] in ("name", "company") and g["key"] in have and latin(g["value"]):
            g["key"] = g["key"] + "_en"
        if g["key"] in single and g["key"] in have:
            continue
        if nkey(g) in seen:
            continue
        seen.add(nkey(g))
        have.add(g["key"])
        fields.append(g)
    ids = list(dict.fromkeys([keep["card_id"]] + list(keep.get("card_ids") or []) + [other["card_id"]]
                             + list(other.get("card_ids") or [])))
    ids = [i for i in ids if i]
    kn, on = dict(keep["notes"] or {}), other["notes"] or {}
    kn["tags"] = sorted(set(kn.get("tags") or []) | set(on.get("tags") or []))
    for k in ("event", "source"):
        if not kn.get(k) and on.get(k):
            kn[k] = on[k]
    if on.get("memo") and on.get("memo") not in (kn.get("memo") or ""):
        kn["memo"] = ((kn.get("memo") or "") + "\n" + on["memo"]).strip()
    snap = _l(keep.get("merged") if isinstance(keep.get("merged"), str) else _j(keep.get("merged") or []), [])
    snap.append({"keep_before": {k: keep[k] for k in ("fields", "notes", "card_ids", "status")},
                 "other": {k: other[k] for k in ("id", "card_id", "card_ids", "idx", "status", "fields", "notes",
                                                 "created_at")}})
    c = conn()
    with db.tx(c):
        c.execute("UPDATE bc_contacts SET fields=?, notes=?, card_ids=?, merged=?, updated_at=? WHERE id=? AND batch_id=?",
                  (_j(fields), _j(clean_notes(kn)), _j(ids), _j(snap), time.time(), keep_id, bid))
        c.execute("DELETE FROM bc_contacts WHERE id=? AND batch_id=?", (other_id, bid))
    return get_contact(owner, bid, keep_id)


def unmerge_contact(owner: str, bid: str, ctid: str) -> dict:
    """解除最近一次合併：這一位回到合併前的樣子，被併進來的那一位原樣回來。"""
    ct = get_contact(owner, bid, ctid)
    snap = ct.get("merged") or []
    if isinstance(snap, str):
        snap = _l(snap, [])
    if not snap:
        raise ValueError("not merged")
    last = snap.pop()
    kb, ot = last["keep_before"], last["other"]
    c = conn()
    now = time.time()
    with db.tx(c):
        c.execute("UPDATE bc_contacts SET fields=?, notes=?, card_ids=?, status=?, merged=?, updated_at=? WHERE id=? AND batch_id=?",
                  (_j(kb["fields"]), _j(kb["notes"]), _j(kb["card_ids"]), kb["status"], _j(snap), now, ctid, bid))
        c.execute("INSERT OR REPLACE INTO bc_contacts(id, batch_id, card_id, card_ids, idx, status, fields, notes, created_at,"
                  " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (ot["id"], bid, ot["card_id"], _j(ot["card_ids"] or []), ot["idx"], ot["status"], _j(ot["fields"]),
                   _j(ot["notes"]), ot["created_at"], now))
    return get_contact(owner, bid, ctid)


def contacts(bid: str) -> list[dict]:
    return [_contact(r) for r in conn().execute(
        "SELECT * FROM bc_contacts WHERE batch_id=? ORDER BY idx, created_at", (bid,)).fetchall()]


def get_contact(owner: str, bid: str, ctid: str) -> dict:
    get_batch(owner, bid)
    _check_id(ctid)
    r = conn().execute("SELECT * FROM bc_contacts WHERE id=? AND batch_id=?", (ctid, bid)).fetchone()
    if not r:
        raise NotFound()
    return _contact(r)


def upsert_card_contact(bid: str, cid: str, fields: list[dict], idx: int, notes: Optional[dict] = None) -> str:
    """辨識完一張卡：這張卡的「自動」聯絡人換成新結果；**人工改過的欄位保留**（規格：重新辨識預設保留人工修改）。"""
    c = conn()
    now = time.time()
    row = c.execute("SELECT * FROM bc_contacts WHERE batch_id=? AND card_id=? ORDER BY created_at LIMIT 1",
                    (bid, cid)).fetchone()
    status = "check" if any(f.get("status") == "check" for f in fields) or not fields else "check"
    with db.tx(c):
        if row:
            old = _contact(row)
            manual = [f for f in old["fields"] if f.get("source", {}).get("method") == "manual"
                      or f.get("status") in ("confirmed", "deleted")]
            manual_keys = {(f["key"], f.get("source", {}).get("line")) for f in manual}
            merged = manual + [f for f in fields if (f["key"], f.get("source", {}).get("line")) not in manual_keys]
            c.execute("UPDATE bc_contacts SET fields=?, status=?, updated_at=? WHERE id=?",
                      (_j(merged), "check" if old["status"] != "excluded" else "excluded", now, old["id"]))
            return old["id"]
        ctid = new_id()
        c.execute("INSERT INTO bc_contacts(id, batch_id, card_id, card_ids, idx, status, fields, notes, created_at,"
                  " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (ctid, bid, cid, _j([cid]), idx, status, _j(fields), _j(clean_notes(notes)), now, now))
        return ctid


def add_contact(owner: str, bid: str, cid: str) -> str:
    """同一張卡上的第二位聯絡人（規格：一張名片列多位，共用來源，不把兩個姓名串在同一欄）。"""
    get_card(owner, bid, cid)
    c = conn()
    now = time.time()
    n = c.execute("SELECT COUNT(*) AS n FROM bc_contacts WHERE batch_id=?", (bid,)).fetchone()["n"]
    ctid = new_id()
    with db.tx(c):
        c.execute("INSERT INTO bc_contacts(id, batch_id, card_id, card_ids, idx, status, fields, notes, created_at,"
                  " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (ctid, bid, cid, _j([cid]), n, "check", "[]", "{}", now, now))
    return ctid


def save_contact(owner: str, bid: str, ctid: str, *, fields: Optional[list] = None, notes: Optional[dict] = None,
                 status: Optional[str] = None) -> dict:
    cur = get_contact(owner, bid, ctid)
    cols: dict[str, Any] = {}
    if fields is not None:
        cols["fields"] = _j(fields)
    if notes is not None:
        cols["notes"] = _j(clean_notes(notes))
    if status is not None:
        if status not in CONTACT_STATUSES:
            raise ValueError("status")
        cols["status"] = status
    if cols:
        cols["updated_at"] = time.time()
        c = conn()
        with db.tx(c):
            c.execute(f"UPDATE bc_contacts SET {', '.join(k + '=?' for k in cols)} WHERE id=? AND batch_id=?",
                      (*cols.values(), ctid, bid))
    return get_contact(owner, bid, ctid) if cols else cur


def delete_contact(owner: str, bid: str, ctid: str) -> None:
    get_contact(owner, bid, ctid)
    c = conn()
    with db.tx(c):
        c.execute("DELETE FROM bc_contacts WHERE id=? AND batch_id=?", (ctid, bid))


def card_index(bid: str) -> dict[str, dict]:
    return {x["id"]: x for x in cards(bid)}


def iter_owner_batches() -> Iterable[tuple[str, str]]:
    for r in conn().execute("SELECT id, owner FROM bc_batches").fetchall():
        yield r["id"], r["owner"]
