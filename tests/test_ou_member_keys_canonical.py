"""組織單位（OU）成員：用選的、而且「手打 `OU=…`」也要對得上（客戶回報 2026-11-23）。

* 共用名片簿、共用事務機、用印申請（保管人、可申請對象、各關審核者、AI 用印的對象）都可以指派給 OU。
  比對時拿的是登入算出來的鍵（`permissions.subjects_for_user` → `canon_subject_key`：屬性型別小寫，
  `OU=Sales,DC=…` 變 `ou=Sales,dc=…`），存的時候原本**原樣**存 —— 照畫面上的範例手打 `OU=…` 的那幾本 / 台 / 顆章，
  OU 裡的人**無聲地**看不到、用不到（不會多給權限，是該有的沒有）。現在寫入時正規化，舊資料遷移、讀設定時也正規化。
* `permissions.known_ous()`：從目錄帳號與群組的 DN 推出每一層 OU，畫面上用選的（SAML / 本機帳號沒有 DN，不會出現）。
* 共用名片簿的名稱不可以重複（不分大小寫、去掉頭尾空白）。
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from app.core import auth_db, permissions

DN = "CN=Amy Example,OU=Sales,OU=TW,DC=example,DC=com"
TYPED = "OU=Sales,OU=TW,DC=example,DC=com"            # 照畫面範例手打的寫法
CANON = "ou=Sales,ou=TW,dc=example,dc=com"


def _ad_user(name: str, dn: str) -> int:
    c = auth_db.conn()
    with c:
        cur = c.execute("INSERT INTO users(username, display_name, source, external_dn, enabled, is_admin_seed, created_at)"
                        " VALUES (?,?,?,?,1,0,?)", (name, name, "ad", dn, time.time()))
    return int(cur.lastrowid)


@pytest.fixture
def ad(auth_off):
    uid = _ad_user("amy", DN)
    _ad_user("bob", "CN=Bob,OU=TW,DC=example,DC=com")
    return uid


# --------------------------------------------------------------------------- 用選的

def test_known_ous_come_from_directory_dns(ad):
    ous = {o["key"]: o for o in permissions.known_ous()}
    assert set(ous) == {CANON, "ou=TW,dc=example,dc=com"}
    assert ous[CANON]["users"] == 1 and ous["ou=TW,dc=example,dc=com"]["users"] == 2, "上層 OU 算進下層的人"
    assert ous[CANON]["name"] == "Sales / TW（example.com）"
    # 登入算出來的 OU 鍵跟清單上的一樣（選了就對得上）
    mine = {k for t, k in permissions.subjects_for_user(ad) if t == "ou"}
    assert CANON in mine


def test_subjects_api_lists_ous(admin_session):
    _ad_user("amy", DN)
    c, _u, _p = admin_session
    r = c.get("/admin/seal-request/api/subjects")
    assert r.status_code == 200
    assert CANON in {o["key"] for o in r.json()["ous"]}


# --------------------------------------------------------------------------- 手打的 OU 也對得上

def test_shared_book_with_a_typed_ou_is_visible_to_people_in_it(ad):
    from app.tools.business_card_reader import books
    import uuid
    b = books.create_shared("業務部-" + uuid.uuid4().hex[:6], 0, "admin")
    out = books.set_members(b["id"], [{"type": "ou", "key": TYPED, "role": "edit"}])
    assert out == [{"type": "ou", "key": CANON, "role": "edit"}], "存的時候要正規化"
    v = books.Viewer(owner=f"u{ad}", user_id=ad, subjects=permissions.subjects_for_user(ad))
    assert books.role_of(v, books._shared(b["id"])) == "edit", "OU 裡的人要看得到（原本原樣存，永遠對不上）"


def test_shared_mfp_with_a_typed_ou(ad):
    from app.core import mfp_store
    import uuid
    host = f"mfp-{uuid.uuid4().hex[:8]}.example.com"
    cid = mfp_store.create(mfp_store.SHARED, None, host, 443, "https", "共用", None)["id"]
    out = mfp_store.set_members(cid, [("ou", TYPED)])
    assert [(m["type"], m["key"]) for m in out] == [("ou", CANON)]


def test_seal_subjects_with_a_typed_ou_match(ad):
    from app.core import seal_flow, seal_policy
    assert seal_flow.matches(ad, [{"type": "ou", "key": TYPED}]), "舊設定 / 舊案件裡原樣存的 OU 也要比得上"
    cleaned = seal_policy._check_subjects([{"type": "ou", "key": TYPED}])
    assert cleaned == [{"type": "ou", "key": CANON}]


# --------------------------------------------------------------------------- 名稱不可重複

def test_shared_book_names_are_unique(ad):
    from app.tools.business_card_reader import books
    import uuid
    n = "Test-" + uuid.uuid4().hex[:6]
    a = books.create_shared(n, 0, "admin")
    with pytest.raises(books.BookError):
        books.create_shared(" " + n.lower() + " ", 0, "admin")
    b = books.create_shared(n + " 2", 0, "admin")
    with pytest.raises(books.BookError):
        books.update_shared(b["id"], name=n.upper())
    assert books.update_shared(a["id"], name=n)["name"] == n, "改成自己原本的名字可以"


def test_duplicate_name_is_a_400_with_a_message(admin_session):
    import uuid
    c, _u, _p = admin_session
    n = "dup-" + uuid.uuid4().hex[:6]
    assert c.post("/admin/business-cards/api/books", json={"name": n}).status_code == 200
    r = c.post("/admin/business-cards/api/books", json={"name": n.upper()})
    assert r.status_code == 400 and "同名" in r.json()["detail"]


# --------------------------------------------------------------------------- 舊資料升級（§1.98）

def test_book_migration_canonicalises_existing_ou_members(tmp_path, monkeypatch):
    from app.config import settings
    from app.core import db
    from app.tools.business_card_reader import store
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    p = store.db_path()
    db.migrate(p, store._MIGRATIONS[:3])
    c = db.get_conn(p)
    now = time.time()
    with c:
        for bid in ("a" * 32, "b" * 32):
            c.execute("INSERT INTO bc_books(id, kind, owner, name, keep_days, created_by, created_at, updated_at)"
                      " VALUES (?,?,?,?,?,?,?,?)", (bid, "shared", "", bid[:3], 0, "x", now, now))
        c.execute("INSERT INTO bc_book_members VALUES (?,?,?,?)", ("a" * 32, "ou", TYPED, "view"))
        # 同一本裡正規化之後撞在一起的兩列：留權限高的那個
        c.execute("INSERT INTO bc_book_members VALUES (?,?,?,?)", ("b" * 32, "ou", CANON, "view"))
        c.execute("INSERT INTO bc_book_members VALUES (?,?,?,?)", ("b" * 32, "ou", TYPED, "edit"))
        c.execute("INSERT INTO bc_book_members VALUES (?,?,?,?)", ("b" * 32, "user", "7", "view"))
    store._INITED.discard(str(p))
    rows = sorted(tuple(r) for r in store.conn().execute(
        "SELECT book_id, subject_type, subject_key, role FROM bc_book_members").fetchall())
    assert rows == [("a" * 32, "ou", CANON, "view"), ("b" * 32, "ou", CANON, "edit"), ("b" * 32, "user", "7", "view")]
    assert store.conn().execute("PRAGMA user_version").fetchone()[0] == len(store._MIGRATIONS)


def test_mfp_migration_canonicalises_existing_ou_members(tmp_path, monkeypatch):
    from app.config import settings
    from app.core import db, mfp_store
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    p = mfp_store.db_path()
    db.migrate(p, mfp_store._MIGRATIONS[:4])
    c = db.get_conn(p)
    cols = [r[1] for r in c.execute("PRAGMA table_info(mfp_connections)").fetchall()]
    with c:
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("INSERT INTO mfp_members VALUES (?,?,?,?)", (1, "ou", TYPED, time.time()))
        c.execute("INSERT INTO mfp_members VALUES (?,?,?,?)", (1, "group", "5", time.time()))
    assert cols, "共用事務機的表要在"
    mfp_store._INITED.discard(str(p))
    rows = sorted((r[0], r[1]) for r in mfp_store._conn().execute(
        "SELECT subject_type, subject_key FROM mfp_members").fetchall())
    assert rows == [("group", "5"), ("ou", CANON)]
