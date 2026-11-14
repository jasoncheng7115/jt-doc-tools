"""名片簿：存進 jtdt 長期保留、可以查詢的聯絡人（第 2 版）。

* **個人名片簿**：每個人一本（第一次用到時建立），只有本人看得到；未啟用登入時是這台電腦上共用的那一本。
* **共用名片簿**：管理員建立，成員用使用者 / 群組 / 組織單位指定（跟權限系統同一套，`permissions.subjects_for_user`），
  分「可查詢」（view：查、看、匯出）與「可編修」（edit：另外可以存進來、修改、合併、刪除、記互動）。
  管理員只管名片簿與成員，**不會因為是管理員就看得到內容**（要看就把自己加進成員）。
* 一個人（`bc_people`）底下保留每一張存進來的名片（`bc_person_cards`：當時的公司、職稱、名片圖），換工作、升職都看得到；
  互動紀錄（`bc_interactions`）；最後更新時間。
* **保留期限每本各自設**（N 天或永久），依「最後更新」起算；個人名片簿由管理員定上限，使用者在上限內自己選。

存取控制在這一層：每個函式都先拿到「這位使用者在這本的角色」，沒有權限的一律當作不存在（`NotFound`），
有查詢權限但要編修時丟 `Forbidden`。編號一律 32 位十六進位，檔案路徑只由編號組成。
"""
from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ...core import db
from . import store
from .store import NotFound, _check_id, _j, _l, conn, new_id

ROLES = ("view", "edit")
MEMBER_TYPES = ("user", "group", "ou")
MAX_MEMBERS = 500
MAX_PEOPLE = 20000
MAX_KEEP_DAYS = 36500
INTERACTION_KINDS = ("meeting", "call", "email", "visit", "other")
SINGLE_KEYS = ("name", "name_en", "title", "department", "company", "company_en", "vat")
PERSONAL_NAME = "我的名片簿"


class Forbidden(Exception):
    """看得到這本、但只能查詢不能編修。"""


class BookError(ValueError):
    pass


@dataclass
class Viewer:
    owner: str                                   # u<id> / local
    user_id: Optional[int] = None
    label: str = ""
    auth: bool = True
    subjects: list = field(default_factory=list)


# --------------------------------------------------------------------------- 設定

def get_setting(key: str, default: str = "") -> str:
    r = conn().execute("SELECT value FROM bc_settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    c = conn()
    with db.tx(c):
        c.execute("INSERT INTO bc_settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))


def personal_max_days() -> int:
    """個人名片簿的保留上限（天，0 ＝不限）。"""
    try:
        return max(0, int(get_setting("personal_max_days", "0")))
    except ValueError:
        return 0


def set_personal_max_days(days: int) -> None:
    days = _days(days)
    set_setting("personal_max_days", str(days))


def _days(v) -> int:
    try:
        d = int(v)
    except (TypeError, ValueError):
        raise BookError("保留天數要是 0（永久）到 36500 之間的整數")
    if d < 0 or d > MAX_KEEP_DAYS:
        raise BookError("保留天數要是 0（永久）到 36500 之間的整數")
    return d


def effective_keep_days(book: dict) -> int:
    """實際的保留天數：個人名片簿受管理員的上限限制（上限 > 0 時「永久」也算上限）。"""
    keep = int(book.get("keep_days") or 0)
    if book.get("kind") == "personal":
        cap = personal_max_days()
        if cap > 0:
            return cap if keep <= 0 else min(keep, cap)
    return keep


# --------------------------------------------------------------------------- 名片簿與角色

def books_dir(book_id: str) -> Path:
    _check_id(book_id)
    return store.files_root() / "books" / book_id


def _book(r) -> dict:
    return dict(r)


def ensure_personal(v: Viewer) -> dict:
    c = conn()
    r = c.execute("SELECT * FROM bc_books WHERE kind='personal' AND owner=?", (v.owner,)).fetchone()
    if r:
        return _book(r)
    now = time.time()
    bid = new_id()
    with db.tx(c):
        c.execute("INSERT OR IGNORE INTO bc_books(id, kind, owner, name, keep_days, created_by, created_at, updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?)", (bid, "personal", v.owner, PERSONAL_NAME, 0, v.label[:120], now, now))
    return _book(c.execute("SELECT * FROM bc_books WHERE kind='personal' AND owner=?", (v.owner,)).fetchone())


def role_of(v: Viewer, book: dict) -> Optional[str]:
    """這位使用者在這本的角色：owner / edit / view / None（看不到）。"""
    if book["kind"] == "personal":
        return "owner" if book["owner"] == v.owner else None
    if not v.auth:
        return "edit"                                  # 沒有帳號的單機環境：大家都是同一個人
    if not v.subjects:
        return None
    cond = " OR ".join(["(subject_type=? AND subject_key=?)"] * len(v.subjects))
    args = [book["id"]]
    for st, sk in v.subjects:
        args += [str(st), str(sk)]
    roles = {r["role"] for r in conn().execute(
        f"SELECT role FROM bc_book_members WHERE book_id=? AND ({cond})", args).fetchall()}
    return "edit" if "edit" in roles else ("view" if "view" in roles else None)


def get_book(v: Viewer, book_id: str, need: str = "view") -> tuple[dict, str]:
    _check_id(book_id)
    r = conn().execute("SELECT * FROM bc_books WHERE id=?", (book_id,)).fetchone()
    if not r:
        raise NotFound()
    book = _book(r)
    role = role_of(v, book)
    if role is None:
        raise NotFound()                               # 不分「不存在」與「沒權限」
    if need == "edit" and role not in ("owner", "edit"):
        raise Forbidden()
    return book, role


def list_books(v: Viewer) -> list[dict]:
    own = ensure_personal(v)
    c = conn()
    shared = [_book(r) for r in c.execute("SELECT * FROM bc_books WHERE kind='shared' ORDER BY name").fetchall()]
    out = []
    for b in [own] + shared:
        role = role_of(v, b)
        if role is None:
            continue
        n = c.execute("SELECT COUNT(*) AS n FROM bc_people WHERE book_id=?", (b["id"],)).fetchone()["n"]
        out.append({**b, "role": role, "people": n, "effective_keep_days": effective_keep_days(b),
                    "personal_max_days": personal_max_days() if b["kind"] == "personal" else None})
    return out


def set_personal_keep(v: Viewer, book_id: str, days) -> dict:
    book, role = get_book(v, book_id, "edit")
    if book["kind"] != "personal":
        raise Forbidden()                              # 共用名片簿的保留期限由管理員設定
    days = _days(days)
    cap = personal_max_days()
    if cap > 0 and (days == 0 or days > cap):
        raise BookError(f"管理員設定個人名片簿最多保留 {cap} 天")
    c = conn()
    with db.tx(c):
        c.execute("UPDATE bc_books SET keep_days=?, updated_at=? WHERE id=?", (days, time.time(), book_id))
    return get_book(v, book_id)[0]


# ------------------------------------------------------------------ 管理員：共用名片簿

def admin_list() -> list[dict]:
    """管理頁：每本的名稱、種類、保留期限、人數與成員（不含名片內容）。個人名片簿只列數量。"""
    c = conn()
    out = []
    for r in c.execute("SELECT * FROM bc_books ORDER BY kind DESC, name").fetchall():
        b = _book(r)
        b["people"] = c.execute("SELECT COUNT(*) AS n FROM bc_people WHERE book_id=?", (b["id"],)).fetchone()["n"]
        b["members"] = get_members(b["id"]) if b["kind"] == "shared" else []
        out.append(b)
    return out


def create_shared(name: str, keep_days, by: str) -> dict:
    name = _name(name)
    days = _days(keep_days)
    bid, now = new_id(), time.time()
    c = conn()
    with db.tx(c):
        c.execute("INSERT INTO bc_books(id, kind, owner, name, keep_days, created_by, created_at, updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?)", (bid, "shared", "", name, days, by[:120], now, now))
    return _book(c.execute("SELECT * FROM bc_books WHERE id=?", (bid,)).fetchone())


def _name(name) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()[:80]
    if not name:
        raise BookError("名片簿要有名稱")
    return name


def _shared(book_id: str) -> dict:
    _check_id(book_id)
    r = conn().execute("SELECT * FROM bc_books WHERE id=? AND kind='shared'", (book_id,)).fetchone()
    if not r:
        raise NotFound()
    return _book(r)


def update_shared(book_id: str, name=None, keep_days=None) -> dict:
    _shared(book_id)
    cols = {}
    if name is not None:
        cols["name"] = _name(name)
    if keep_days is not None:
        cols["keep_days"] = _days(keep_days)
    if cols:
        cols["updated_at"] = time.time()
        c = conn()
        with db.tx(c):
            c.execute(f"UPDATE bc_books SET {', '.join(k + '=?' for k in cols)} WHERE id=?", (*cols.values(), book_id))
    return _shared(book_id)


def delete_book(book_id: str) -> int:
    """刪掉一本（連同人、名片、互動、名片圖）。回刪掉幾個人。"""
    _check_id(book_id)
    c = conn()
    n = c.execute("SELECT COUNT(*) AS n FROM bc_people WHERE book_id=?", (book_id,)).fetchone()["n"]
    with db.tx(c):
        c.execute("DELETE FROM bc_interactions WHERE book_id=?", (book_id,))
        c.execute("DELETE FROM bc_person_cards WHERE book_id=?", (book_id,))
        c.execute("DELETE FROM bc_people WHERE book_id=?", (book_id,))
        c.execute("DELETE FROM bc_book_members WHERE book_id=?", (book_id,))
        c.execute("DELETE FROM bc_books WHERE id=?", (book_id,))
    shutil.rmtree(books_dir(book_id), ignore_errors=True)
    return n


def get_members(book_id: str) -> list[dict]:
    return [{"type": r["subject_type"], "key": r["subject_key"], "role": r["role"]} for r in conn().execute(
        "SELECT * FROM bc_book_members WHERE book_id=? ORDER BY subject_type, subject_key", (book_id,)).fetchall()]


def set_members(book_id: str, members: Iterable) -> list[dict]:
    _shared(book_id)
    clean: dict[tuple[str, str], str] = {}
    for m in members:
        if not isinstance(m, dict):
            raise BookError("成員格式不正確")
        st, sk, role = str(m.get("type") or ""), str(m.get("key") or "").strip(), str(m.get("role") or "view")
        if st not in MEMBER_TYPES or not sk or len(sk) > 500 or role not in ROLES:
            raise BookError("成員格式不正確")
        if st in ("user", "group") and not sk.isdigit():
            raise BookError("成員格式不正確")
        if clean.get((st, sk)) != "edit":
            clean[(st, sk)] = role
    if len(clean) > MAX_MEMBERS:
        raise BookError(f"成員最多 {MAX_MEMBERS} 個")
    c = conn()
    with db.tx(c):
        c.execute("DELETE FROM bc_book_members WHERE book_id=?", (book_id,))
        c.executemany("INSERT INTO bc_book_members(book_id, subject_type, subject_key, role) VALUES (?,?,?,?)",
                      [(book_id, st, sk, role) for (st, sk), role in clean.items()])
    return get_members(book_id)


# --------------------------------------------------------------------------- 人

def _val(fields: list, key: str) -> str:
    return next((f.get("value") or "" for f in fields if f.get("key") == key and f.get("status") != "deleted"), "")


def _vals(fields: list, key: str) -> list[str]:
    return [f.get("value") or "" for f in fields if f.get("key") == key and f.get("status") != "deleted"]


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def _keys(fields: list) -> str:
    from .extract import normalize_phone
    ks = []
    for e in _vals(fields, "email"):
        if e:
            ks.append("e:" + e.strip().lower())
    for m in _vals(fields, "mobile"):
        d = normalize_phone(m).lstrip("+")
        if len(d) >= 8:
            ks.append("m:" + d[-9:])                   # 0912… 與 +886912… 視為同一支
    comp = _norm_text(_val(fields, "company"))
    for k in ("name", "name_en"):
        n = _norm_text(_val(fields, k))
        if n and comp:
            ks.append("n:" + n + "|" + comp)
    return " " + " ".join(ks) + " " if ks else ""


def _search(fields: list, notes: dict) -> str:
    vals = [f.get("value") or "" for f in fields if f.get("status") != "deleted"]
    vals += [notes.get("event") or "", notes.get("source") or "", notes.get("memo") or "", " ".join(notes.get("tags") or [])]
    return " ".join(_norm_text(x) for x in vals if x)


def _person(r, *, full: bool = False) -> dict:
    d = dict(r)
    d["fields"] = _l(d["fields"], [])
    d["notes"] = _l(d["notes"], {})
    d.pop("search", None)
    d.pop("match_keys", None)
    return d


def _summary(p: dict) -> dict:
    f = p["fields"]
    return {"id": p["id"], "name": _val(f, "name"), "name_en": _val(f, "name_en"), "company": _val(f, "company"),
            "title": _val(f, "title"), "department": _val(f, "department"), "mobile": _val(f, "mobile"),
            "email": _val(f, "email"), "phone": _val(f, "phone"), "tags": (p["notes"] or {}).get("tags") or [],
            "updated_at": p["updated_at"], "created_at": p["created_at"], "cards": p.get("cards", 0)}


def list_people(book: dict, q: str = "", tag: str = "", limit: int = 100, offset: int = 0) -> dict:
    """查詢：關鍵字（空白分開，每個都要出現在某個欄位或備註裡）、標籤。依最後更新排序。"""
    where, args = ["book_id=?"], [book["id"]]
    for tok in [_norm_text(t) for t in (q or "").split() if t.strip()][:8]:
        where.append("search LIKE ? ESCAPE '\\'")
        args.append("%" + tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if tag:
        where.append("notes LIKE ?")
        args.append("%" + _j(str(tag)).replace("%", "").replace("_", "") + "%")   # 含引號：整個標籤相同才算
    c = conn()
    total = c.execute(f"SELECT COUNT(*) AS n FROM bc_people WHERE {' AND '.join(where)}", args).fetchone()["n"]
    limit = max(1, min(int(limit or 100), 500))
    rows = c.execute(f"SELECT p.*, (SELECT COUNT(*) FROM bc_person_cards pc WHERE pc.person_id=p.id) AS cards "
                     f"FROM bc_people p WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                     [*args, limit, max(0, int(offset or 0))]).fetchall()
    items = [_summary(_person(r)) for r in rows]
    if tag:
        items = [i for i in items if tag in i["tags"]]
    return {"total": total, "items": items}


def all_people(book: dict) -> list[dict]:
    return [_person(r) for r in conn().execute(
        "SELECT * FROM bc_people WHERE book_id=? ORDER BY updated_at DESC", (book["id"],)).fetchall()]


def get_person(book: dict, pid: str) -> dict:
    _check_id(pid)
    c = conn()
    r = c.execute("SELECT * FROM bc_people WHERE id=? AND book_id=?", (pid, book["id"])).fetchone()
    if not r:
        raise NotFound()
    p = _person(r)
    p["cards"] = [{**dict(x), "fields": _l(x["fields"], []), "notes": _l(x["notes"], {}), "image": bool(x["image"])}
                  for x in c.execute("SELECT * FROM bc_person_cards WHERE person_id=? ORDER BY captured_at DESC, saved_at DESC",
                                     (pid,)).fetchall()]
    p["interactions"] = [dict(x) for x in c.execute(
        "SELECT * FROM bc_interactions WHERE person_id=? ORDER BY at DESC, created_at DESC", (pid,)).fetchall()]
    return p


def card_image_path(book: dict, card_id: str) -> Path:
    _check_id(card_id)
    return books_dir(book["id"]) / f"{card_id}.jpg"


def _merge_fields(current: list, incoming: list) -> list:
    """新的名片進來：單值欄位（姓名、公司、職稱…）用新的（舊的留在那張名片的紀錄裡），
    多值欄位（電話、Email、地址…）合併、同一個值不重複，新的排前面。"""
    from .extract import normalize_phone
    def norm(f):
        v = f.get("value") or ""
        if f.get("key") in ("mobile", "phone", "fax"):
            return normalize_phone(v)
        return _norm_text(v)
    inc = [dict(f) for f in incoming if f.get("status") != "deleted" and (f.get("value") or "").strip()]
    inc_single = {f["key"] for f in inc if f["key"] in SINGLE_KEYS}
    out = list(inc)
    seen = {(f["key"], norm(f)) for f in inc}
    for f in current:
        if f.get("status") == "deleted" or not (f.get("value") or "").strip():
            continue
        if f["key"] in SINGLE_KEYS and f["key"] in inc_single:
            continue
        k = (f["key"], norm(f))
        if k not in seen:
            seen.add(k)
            out.append(dict(f))
    return out


def _clean_card_fields(fields: list) -> list:
    out = []
    for f in fields or []:
        if f.get("status") == "deleted" or not str(f.get("value") or "").strip():
            continue
        g = {k: f[k] for k in ("id", "key", "value", "ext", "label", "zip", "norm") if f.get(k) not in (None, "")}
        g["status"] = "confirmed"
        out.append(g)
    return out


def _write_image(book: dict, card_id: str, image: Optional[bytes]) -> bool:
    if not image:
        return False
    p = card_image_path(book, card_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(image)
    return True


def add_person(book: dict, fields: list, notes: dict, by: str, *, image: Optional[bytes] = None,
               source: str = "card", captured_at: Optional[float] = None) -> str:
    c = conn()
    if c.execute("SELECT COUNT(*) AS n FROM bc_people WHERE book_id=?", (book["id"],)).fetchone()["n"] >= MAX_PEOPLE:
        raise BookError(f"一本名片簿最多 {MAX_PEOPLE} 人")
    fields = _clean_card_fields(fields)
    notes = store.clean_notes(notes)
    pid, cid, now = new_id(), new_id(), time.time()
    has_img = _write_image(book, cid, image)
    with db.tx(c):
        c.execute("INSERT INTO bc_people(id, book_id, fields, notes, search, match_keys, created_by, updated_by,"
                  " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (pid, book["id"], _j(fields), _j(notes), _search(fields, notes), _keys(fields), by[:120], by[:120], now, now))
        c.execute("INSERT INTO bc_person_cards(id, person_id, book_id, fields, notes, image, source, captured_at,"
                  " saved_at, saved_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (cid, pid, book["id"], _j(fields), _j(notes), int(has_img), source, captured_at or now, now, by[:120]))
    return pid


def add_card(book: dict, pid: str, fields: list, notes: dict, by: str, *, image: Optional[bytes] = None,
             source: str = "card", captured_at: Optional[float] = None) -> None:
    """同一個人又拿到一張名片（換工作、升職、補一張）：保留這張的紀錄，目前資料換成新的。"""
    p = get_person(book, pid)
    fields = _clean_card_fields(fields)
    notes = store.clean_notes(notes)
    merged = _merge_fields(p["fields"], fields)
    pn = dict(p["notes"] or {})
    pn["tags"] = sorted(set(pn.get("tags") or []) | set(notes.get("tags") or []))
    for k in ("event", "source", "memo"):
        if notes.get(k) and not pn.get(k):
            pn[k] = notes[k]
    pn = store.clean_notes(pn)
    cid, now = new_id(), time.time()
    has_img = _write_image(book, cid, image)
    c = conn()
    with db.tx(c):
        c.execute("INSERT INTO bc_person_cards(id, person_id, book_id, fields, notes, image, source, captured_at,"
                  " saved_at, saved_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (cid, pid, book["id"], _j(fields), _j(notes), int(has_img), source, captured_at or now, now, by[:120]))
        c.execute("UPDATE bc_people SET fields=?, notes=?, search=?, match_keys=?, updated_by=?, updated_at=? WHERE id=?",
                  (_j(merged), _j(pn), _search(merged, pn), _keys(merged), by[:120], now, pid))


def update_person(book: dict, pid: str, by: str, fields: Optional[list] = None, notes: Optional[dict] = None) -> dict:
    p = get_person(book, pid)
    f = _clean_card_fields(fields) if fields is not None else p["fields"]
    n = store.clean_notes(notes) if notes is not None else p["notes"]
    c = conn()
    with db.tx(c):
        c.execute("UPDATE bc_people SET fields=?, notes=?, search=?, match_keys=?, updated_by=?, updated_at=? WHERE id=?",
                  (_j(f), _j(n), _search(f, n), _keys(f), by[:120], time.time(), pid))
    return get_person(book, pid)


def delete_person(book: dict, pid: str) -> None:
    p = get_person(book, pid)
    c = conn()
    with db.tx(c):
        c.execute("DELETE FROM bc_interactions WHERE person_id=?", (pid,))
        c.execute("DELETE FROM bc_person_cards WHERE person_id=?", (pid,))
        c.execute("DELETE FROM bc_people WHERE id=?", (pid,))
    for card in p["cards"]:
        card_image_path(book, card["id"]).unlink(missing_ok=True)


def merge_people(book: dict, keep: str, others: list[str], by: str) -> dict:
    """把幾個人合併成一個（同一人被存成兩筆）：名片紀錄與互動都搬過去；目前資料以保留的那位為主、補上另一位多的值。"""
    kp = get_person(book, keep)
    others = [o for o in dict.fromkeys(others) if o != keep]
    if not others:
        raise BookError("要選另一位才能合併")
    ops = [get_person(book, o) for o in others]
    fields = list(kp["fields"])
    notes = dict(kp["notes"] or {})
    for o in ops:
        fields = _merge_fields(o["fields"], fields)    # 保留的那位單值優先
        notes["tags"] = sorted(set(notes.get("tags") or []) | set((o["notes"] or {}).get("tags") or []))
        for k in ("event", "source", "memo"):
            if not notes.get(k) and (o["notes"] or {}).get(k):
                notes[k] = o["notes"][k]
    notes = store.clean_notes(notes)
    c = conn()
    with db.tx(c):
        for o in ops:
            c.execute("UPDATE bc_person_cards SET person_id=? WHERE person_id=?", (keep, o["id"]))
            c.execute("UPDATE bc_interactions SET person_id=? WHERE person_id=?", (keep, o["id"]))
            c.execute("DELETE FROM bc_people WHERE id=?", (o["id"],))
        c.execute("UPDATE bc_people SET fields=?, notes=?, search=?, match_keys=?, updated_by=?, updated_at=? WHERE id=?",
                  (_j(fields), _j(notes), _search(fields, notes), _keys(fields), by[:120], time.time(), keep))
    return get_person(book, keep)


def find_matches(book: dict, fields: list, limit: int = 3) -> list[dict]:
    """名片簿裡可能是同一人的：Email 相同 → 手機相同 → 姓名＋公司相同（照這個優先順序）。"""
    keys = _keys(_clean_card_fields(fields)).split()
    if not keys:
        return []
    order = {"e": 0, "m": 1, "n": 2}
    reason = {"e": "Email 相同", "m": "手機相同", "n": "姓名與公司相同"}
    c = conn()
    hits: dict[str, tuple[int, str]] = {}
    for k in keys:
        for r in c.execute("SELECT id FROM bc_people WHERE book_id=? AND match_keys LIKE ? LIMIT 20",
                           (book["id"], "% " + k.replace("%", "") + " %")).fetchall():
            rank = order[k[0]]
            if r["id"] not in hits or rank < hits[r["id"]][0]:
                hits[r["id"]] = (rank, reason[k[0]])
    out = []
    for pid, (rank, why) in sorted(hits.items(), key=lambda x: x[1][0])[:limit]:
        p = get_person(book, pid)
        out.append({"person": _summary({**p, "cards": len(p["cards"])}), "reason": why})
    return out


# --------------------------------------------------------------------------- 互動紀錄

def _interaction(kind, note, at) -> tuple[str, str, float]:
    kind = str(kind or "other")
    if kind not in INTERACTION_KINDS:
        raise BookError("互動種類不正確")
    note = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(note or "")).strip()[:2000]
    try:
        at = float(at) if at not in (None, "") else time.time()
    except (TypeError, ValueError):
        raise BookError("互動時間不正確")
    if not (0 < at < 4102444800):
        raise BookError("互動時間不正確")
    return kind, note, at


def add_interaction(book: dict, pid: str, kind, note, at, by: str) -> dict:
    get_person(book, pid)
    kind, note, at = _interaction(kind, note, at)
    iid, now = new_id(), time.time()
    c = conn()
    with db.tx(c):
        c.execute("INSERT INTO bc_interactions(id, person_id, book_id, at, kind, note, by_label, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?)", (iid, pid, book["id"], at, kind, note, by[:120], now))
        c.execute("UPDATE bc_people SET updated_at=?, updated_by=? WHERE id=?", (now, by[:120], pid))
    return dict(c.execute("SELECT * FROM bc_interactions WHERE id=?", (iid,)).fetchone())


def update_interaction(book: dict, pid: str, iid: str, kind, note, at) -> dict:
    _check_id(iid)
    get_person(book, pid)
    kind, note, at = _interaction(kind, note, at)
    c = conn()
    with db.tx(c):
        cur = c.execute("UPDATE bc_interactions SET kind=?, note=?, at=? WHERE id=? AND person_id=?",
                        (kind, note, at, iid, pid))
    if not cur.rowcount:
        raise NotFound()
    return dict(c.execute("SELECT * FROM bc_interactions WHERE id=?", (iid,)).fetchone())


def delete_interaction(book: dict, pid: str, iid: str) -> None:
    _check_id(iid)
    get_person(book, pid)
    c = conn()
    with db.tx(c):
        cur = c.execute("DELETE FROM bc_interactions WHERE id=? AND person_id=?", (iid, pid))
    if not cur.rowcount:
        raise NotFound()


# --------------------------------------------------------------------------- 保留期限、刪除帳號

def expire_due(now: Optional[float] = None) -> int:
    """每本依自己的保留期限（個人名片簿另受上限限制），刪掉最後更新超過期限的人（含名片圖）。回刪了幾個人。"""
    now = now or time.time()
    try:
        books = [_book(r) for r in conn().execute("SELECT * FROM bc_books").fetchall()]
    except Exception:                                  # noqa: BLE001 —— 表還沒建（舊版資料庫）
        return 0
    n = 0
    for b in books:
        days = effective_keep_days(b)
        if days <= 0:
            continue
        for r in conn().execute("SELECT id FROM bc_people WHERE book_id=? AND updated_at<?",
                                (b["id"], now - days * 86400)).fetchall():
            try:
                delete_person(b, r["id"])
                n += 1
            except NotFound:
                pass
    return n


def purge_owner(owner: str, user_id: Optional[int] = None) -> int:
    """刪除帳號：他的個人名片簿整本刪掉；共用名片簿裡指名給他的成員資格拿掉（共用的資料留著）。"""
    c = conn()
    n = 0
    for r in c.execute("SELECT id FROM bc_books WHERE kind='personal' AND owner=?", (owner,)).fetchall():
        n += delete_book(r["id"])
    if user_id is not None:
        with db.tx(c):
            c.execute("DELETE FROM bc_book_members WHERE subject_type='user' AND subject_key=?", (str(int(user_id)),))
    return n


def stats() -> dict:
    c = conn()
    r = c.execute("SELECT COUNT(*) AS n FROM bc_books").fetchone()
    p = c.execute("SELECT COUNT(*) AS n, MIN(updated_at) AS m FROM bc_people").fetchone()
    return {"books": int(r["n"] or 0), "people": int(p["n"] or 0), "oldest_updated_at": p["m"]}


def as_contacts(people: list[dict]) -> list[dict]:
    """給匯出用：名片簿的人 → 跟辨識批次的聯絡人同一種形狀（全部算已確認）。"""
    return [{"id": p["id"], "fields": p["fields"], "notes": p["notes"] or {}, "status": "confirmed", "card_id": ""}
            for p in people]
