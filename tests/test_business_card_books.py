"""名片簿：個人 / 共用、查詢與編修權限、存進名片簿與找同一人、合併、互動紀錄、保留期限、刪除帳號、升級。

OCR 換成假的（固定文字行），資料全部杜撰（王小明、Kevin Sample、example.com）。
"""
from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.tools.business_card_reader import books, card_ocr, pipeline, store
from tests import bc_synth as S

API = "/tools/business-card-reader/api"
ADMIN = "/admin/business-cards/api"


def _lines(title="業務經理", email="kevin@example.com", company="範例科技股份有限公司"):
    return [(company, 32), ("王小明", 44), (title, 20), ("手機：0912-345-678", 18), (f"Email: {email}", 18)]


@pytest.fixture
def fake_ocr(monkeypatch):
    state = {"lines": _lines()}

    def fake(img, **kw):
        ls = [card_ocr.Line(text=t, box=[20, 20 + i * 50, 400, 40], conf=95, height=h) for i, (t, h) in enumerate(state["lines"])]
        return {"lines": ls, "rotation": 0, "engine": "fake", "image": img, "quality": 99}
    monkeypatch.setattr(card_ocr, "ocr_card", fake)
    return state


def _batch(c, n=1):
    files = [("files", (f"{i}.jpg", S.jpeg(S.scene_single(seed=i + 3)[0]), "image/jpeg")) for i in range(n)]
    b = c.post(f"{API}/batches", files=files, data={"mode": "single", "tags": "展場"}).json()
    end = time.time() + 60
    while time.time() < end:
        pipeline.wait_idle(5)
        b = c.get(f"{API}/batches/{b['id']}").json()
        if b["counts"]["cards_pending"] == 0 and b["status"] == "review":
            return b
        time.sleep(0.1)
    raise AssertionError("辨識沒有完成")


@pytest.fixture
def people(admin_session, fake_ocr):
    """管理員＋三個使用者：alice（共用簿可編修）、bob（經由群組可查詢）、carol（不是成員）。"""
    from app.core import group_manager, permissions, sessions, user_manager
    admin, *_ = admin_session
    ids = {n: user_manager.create_local(f"bc-{n}", n, "UserPass1234", roles=[]) for n in ("alice", "bob", "carol")}
    gid = group_manager.create_local("bc-viewers")
    group_manager.set_members(gid, [ids["bob"]])
    clients = {"admin": admin}
    for n, uid in ids.items():
        permissions.grant_tool("user", str(uid), "business-card-reader")
        tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
        cl = TestClient(app_main.app)
        cl.cookies.set(sessions.COOKIE_NAME, tok)
        clients[n] = cl
    r = admin.post(f"{ADMIN}/books", json={"name": "業務部共用", "keep_days": 0})
    assert r.status_code == 200, r.text
    bid = r.json()["created"]
    r = admin.put(f"{ADMIN}/books/{bid}/members", json={"members": [
        {"type": "user", "key": str(ids["alice"]), "role": "edit"}, {"type": "group", "key": str(gid), "role": "view"}]})
    assert r.status_code == 200, r.text
    yield clients, bid, ids, fake_ocr
    for cl in clients.values():
        if cl is not admin:
            cl.close()


def _personal(c):
    return next(b for b in c.get(f"{API}/books").json()["items"] if b["kind"] == "personal")


def _save_all_new(c, batch, book_id):
    pv = c.post(f"{API}/batches/{batch['id']}/save-to-book/preview", json={"book": book_id, "scope": "all"}).json()
    items = [{"contact_id": it["contact_id"], "action": "merge" if it["matches"] else "new",
              **({"person_id": it["matches"][0]["person"]["id"]} if it["matches"] else {})} for it in pv["items"]]
    r = c.post(f"{API}/batches/{batch['id']}/save-to-book", json={"book": book_id, "items": items})
    assert r.status_code == 200, r.text
    return pv, r.json()


# --------------------------------------------------------------------------- 個人名片簿

def test_personal_book_is_private(people):
    clients, _bid, _ids, _ = people
    alice, bob = clients["alice"], clients["bob"]
    pa = _personal(alice)
    assert pa["role"] == "owner" and pa["name"] == "我的名片簿"
    b = _batch(alice)
    _save_all_new(alice, b, pa["id"])
    lst = alice.get(f"{API}/books/{pa['id']}/people").json()
    assert lst["total"] == 1 and lst["items"][0]["name"] == "王小明"
    pid = lst["items"][0]["id"]
    person = alice.get(f"{API}/books/{pa['id']}/people/{pid}").json()
    cid = person["cards"][0]["id"]
    assert person["cards"][0]["image"] is True
    assert alice.get(f"{API}/books/{pa['id']}/people/{pid}/cards/{cid}/image").status_code == 200
    assert pa["id"] not in {x["id"] for x in bob.get(f"{API}/books").json()["items"]}
    for method, path, body in [
        ("get", f"/books/{pa['id']}/people", None), ("get", f"/books/{pa['id']}/people/{pid}", None),
        ("get", f"/books/{pa['id']}/people/{pid}/cards/{cid}/image", None),
        ("put", f"/books/{pa['id']}/people/{pid}", {"notes": {"memo": "x"}}),
        ("delete", f"/books/{pa['id']}/people/{pid}", None),
        ("post", f"/books/{pa['id']}/people/merge", {"keep": pid, "others": [pid]}),
        ("post", f"/books/{pa['id']}/people/{pid}/interactions", {"kind": "call", "note": "x"}),
        ("post", f"/books/{pa['id']}/export", {"format": "json"}),
        ("patch", f"/books/{pa['id']}", {"keep_days": 30}),
    ]:
        r = getattr(bob, method)(API + path, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, path, r.status_code)
    # 別人的批次也不能拿來存進自己的名片簿
    pb = _personal(bob)
    assert bob.post(f"{API}/batches/{b['id']}/save-to-book/preview", json={"book": pb["id"]}).status_code == 404


# --------------------------------------------------------------------------- 共用名片簿

def test_shared_book_roles(people):
    clients, bid, _ids, _ = people
    alice, bob, carol, admin = clients["alice"], clients["bob"], clients["carol"], clients["admin"]
    roles = {x["id"]: x["role"] for x in alice.get(f"{API}/books").json()["items"]}
    assert roles[bid] == "edit"
    assert {x["id"]: x["role"] for x in bob.get(f"{API}/books").json()["items"]}[bid] == "view", "群組成員可以查詢"
    assert bid not in {x["id"] for x in carol.get(f"{API}/books").json()["items"]}
    b = _batch(alice)
    _save_all_new(alice, b, bid)
    pid = alice.get(f"{API}/books/{bid}/people").json()["items"][0]["id"]
    # 可查詢：查、看、匯出
    assert bob.get(f"{API}/books/{bid}/people?q=王小明").json()["total"] == 1
    assert bob.get(f"{API}/books/{bid}/people/{pid}").status_code == 200
    assert bob.post(f"{API}/books/{bid}/export", json={"format": "csv"}).status_code == 200
    # 不能改
    for method, path, body in [
        ("put", f"/books/{bid}/people/{pid}", {"notes": {"memo": "x"}}), ("delete", f"/books/{bid}/people/{pid}", None),
        ("post", f"/books/{bid}/people/merge", {"keep": pid, "others": [pid]}),
        ("post", f"/books/{bid}/people/{pid}/interactions", {"kind": "call"}),
    ]:
        r = getattr(bob, method)(API + path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, path, r.status_code)
    bb = _batch(bob)
    assert bob.post(f"{API}/batches/{bb['id']}/save-to-book/preview", json={"book": bid}).status_code == 403
    # 不是成員、管理員（沒把自己加進成員）都看不到內容
    for cl in (carol, admin):
        assert cl.get(f"{API}/books/{bid}/people").status_code == 404
        assert cl.get(f"{API}/books/{bid}/people/{pid}").status_code == 404


def test_admin_page_and_member_validation(people):
    clients, bid, _ids, _ = people
    admin, alice = clients["admin"], clients["alice"]
    assert admin.get("/admin/business-cards").status_code == 200
    st = admin.get(f"{ADMIN}/state").json()
    assert st["shared"][0]["name"] == "業務部共用" and len(st["shared"][0]["members"]) == 2
    assert "fields" not in str(st), "管理頁不可以帶出名片內容"
    assert admin.put(f"{ADMIN}/books/{bid}/members", json={"members": [{"type": "user", "key": "x"}]}).status_code == 400
    assert admin.put(f"{ADMIN}/books/{bid}/members", json={"members": [{"type": "role", "key": "1"}]}).status_code == 400
    assert admin.post(f"{ADMIN}/books", json={"name": "", "keep_days": 0}).status_code == 400
    assert admin.post(f"{ADMIN}/books", json={"name": "x", "keep_days": -1}).status_code == 400
    assert alice.get(f"{ADMIN}/state").status_code in (302, 303, 401, 403), "一般使用者進不了管理頁"
    assert admin.request("DELETE", f"{ADMIN}/books/{bid}", json={"confirm": "不對"}).status_code == 400
    assert admin.request("DELETE", f"{ADMIN}/books/{bid}", json={"confirm": "業務部共用"}).status_code == 200
    assert alice.get(f"{API}/books/{bid}/people").status_code == 404


# --------------------------------------------------------------------------- 存進名片簿：同一人換工作

def test_same_person_new_card_is_merged_with_history(people):
    clients, _bid, _ids, ocr = people
    alice = clients["alice"]
    pa = _personal(alice)
    _save_all_new(alice, _batch(alice), pa["id"])
    ocr["lines"] = _lines(title="資深業務經理", company="另一家範例股份有限公司")    # 同一個 Email：升職、換公司
    pv, out = _save_all_new(alice, _batch(alice), pa["id"])
    assert pv["items"][0]["matches"][0]["reason"] == "Email 相同"
    assert out == {"added": 0, "merged": 1}
    lst = alice.get(f"{API}/books/{pa['id']}/people").json()
    assert lst["total"] == 1, "同一人不可以變兩筆"
    p = alice.get(f"{API}/books/{pa['id']}/people/{lst['items'][0]['id']}").json()
    assert len(p["cards"]) == 2
    vals = {f["key"]: f["value"] for f in p["fields"] if f["key"] in ("title", "company")}
    assert vals == {"title": "資深業務經理", "company": "另一家範例股份有限公司"}, "目前資料換成新的名片"
    old = [{f["key"]: f["value"] for f in c["fields"]} for c in p["cards"]]
    assert any(c.get("title") == "業務經理" for c in old), "舊名片的職稱留在紀錄裡"


def test_match_priority_and_new_person(people):
    clients, _bid, _ids, ocr = people
    alice = clients["alice"]
    pa = _personal(alice)
    _save_all_new(alice, _batch(alice), pa["id"])
    ocr["lines"] = _lines(email="other@example.com")                    # Email 不同、手機相同
    pv = alice.post(f"{API}/batches/{_batch(alice)['id']}/save-to-book/preview", json={"book": pa["id"], "scope": "all"}).json()
    assert pv["items"][0]["matches"][0]["reason"] == "手機相同"
    ocr["lines"] = [("Kevin Sample", 40), ("Email: kevin.sample@example.org", 18)]
    b = _batch(alice)
    pv, out = _save_all_new(alice, b, pa["id"])
    assert pv["items"][0]["matches"] == [] and out["added"] == 1


def test_save_to_book_is_all_or_nothing(people):
    clients, _bid, _ids, _ = people
    alice = clients["alice"]
    pa = _personal(alice)
    b = _batch(alice, 2)
    ids = [c["id"] for c in b["contacts"]]
    bad = [{"contact_id": ids[0], "action": "new"}, {"contact_id": ids[1], "action": "merge", "person_id": "f" * 32}]
    assert alice.post(f"{API}/batches/{b['id']}/save-to-book", json={"book": pa["id"], "items": bad}).status_code == 404
    assert alice.get(f"{API}/books/{pa['id']}/people").json()["total"] == 0, "一筆不對就整批不存"
    assert alice.post(f"{API}/batches/{b['id']}/save-to-book", json={"book": pa["id"], "items": [{"contact_id": ids[0], "action": "x"}]}).status_code == 400


# --------------------------------------------------------------------------- 查詢、修改、合併、互動、匯出

def test_query_edit_merge_interactions_export(people):
    clients, _bid, _ids, ocr = people
    alice = clients["alice"]
    pa = _personal(alice)
    _save_all_new(alice, _batch(alice), pa["id"])
    ocr["lines"] = [("王大明", 40), ("Email: daming@example.com", 18), ("範例顧問有限公司", 30)]
    _save_all_new(alice, _batch(alice), pa["id"])
    base = f"{API}/books/{pa['id']}/people"
    assert alice.get(base).json()["total"] == 2
    assert alice.get(base + "?q=顧問").json()["total"] == 1
    assert alice.get(base + "?q=王 範例科技").json()["total"] == 1, "多個關鍵字要全部符合"
    assert alice.get(base + "?q=100%").json()["total"] == 0, "% 不可以當萬用字元"
    assert alice.get(base + "?tag=展場").json()["total"] == 2
    a, d = sorted(alice.get(base).json()["items"], key=lambda i: i["name"])
    r = alice.put(f"{base}/{a['id']}", json={"notes": {"memo": "常客", "tags": ["展場", "VIP"]}})
    assert r.status_code == 200 and r.json()["notes"]["memo"] == "常客"
    assert alice.get(base + "?tag=VIP").json()["total"] == 1
    r = alice.post(f"{base}/{a['id']}/interactions", json={"kind": "meeting", "note": "談合約", "at": 1_700_000_000})
    assert r.status_code == 200
    iid = r.json()["id"]
    assert alice.put(f"{base}/{a['id']}/interactions/{iid}", json={"kind": "call", "note": "改成電話"}).json()["kind"] == "call"
    assert alice.post(f"{base}/{a['id']}/interactions", json={"kind": "hack"}).status_code == 400
    # 合併：名片紀錄與互動都搬過去
    m = alice.post(f"{base}/merge", json={"keep": a["id"], "others": [d["id"]]}).json()
    assert len(m["cards"]) == 2 and len(m["interactions"]) == 1
    assert alice.get(base).json()["total"] == 1
    assert alice.request("DELETE", f"{base}/{a['id']}/interactions/{iid}").status_code == 200
    r = alice.post(f"{API}/books/{pa['id']}/export", json={"format": "xlsx", "table": True})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert alice.request("DELETE", f"{base}/{a['id']}").status_code == 200
    assert alice.get(base).json()["total"] == 0
    assert alice.post(f"{API}/books/{pa['id']}/export", json={"format": "csv"}).status_code == 409


def test_audit_never_records_names_or_keywords(people):
    from app.core import audit_db
    clients, _bid, _ids, _ = people
    alice = clients["alice"]
    pa = _personal(alice)
    _save_all_new(alice, _batch(alice), pa["id"])
    alice.get(f"{API}/books/{pa['id']}/people?q=王小明")
    time.sleep(0.5)
    rows = audit_db.conn().execute("SELECT event_type, details_json FROM audit_events WHERE event_type LIKE 'business_card_book%'").fetchall()
    events = {r[0] for r in rows}
    assert {"business_card_book_save", "business_card_book_query"} <= events
    assert all("王小明" not in (r[1] or "") and "example.com" not in (r[1] or "") for r in rows)


# --------------------------------------------------------------------------- 保留期限、刪除帳號

def test_personal_keep_days_and_admin_cap(people):
    clients, _bid, _ids, _ = people
    alice, admin = clients["alice"], clients["admin"]
    pa = _personal(alice)
    assert alice.patch(f"{API}/books/{pa['id']}", json={"keep_days": 365}).json()["keep_days"] == 365
    assert admin.put(f"{ADMIN}/settings", json={"personal_max_days": 180}).status_code == 200
    assert alice.patch(f"{API}/books/{pa['id']}", json={"keep_days": 0}).status_code == 400, "有上限時不可以選永久"
    assert alice.patch(f"{API}/books/{pa['id']}", json={"keep_days": 200}).status_code == 400
    assert alice.patch(f"{API}/books/{pa['id']}", json={"keep_days": 90}).status_code == 200
    assert _personal(alice)["effective_keep_days"] == 90


def test_expiry_deletes_people_and_images(people):
    clients, bid, _ids, _ = people
    alice, admin = clients["alice"], clients["admin"]
    _save_all_new(alice, _batch(alice), bid)
    pid = alice.get(f"{API}/books/{bid}/people").json()["items"][0]["id"]
    book = store.conn().execute("SELECT * FROM bc_books WHERE id=?", (bid,)).fetchone()
    person = books.get_person(dict(book), pid)
    img = books.card_image_path(dict(book), person["cards"][0]["id"])
    assert img.is_file()
    assert books.expire_due() == 0, "永久保留的不刪"
    admin.put(f"{ADMIN}/books/{bid}", json={"keep_days": 30})
    c = store.conn()
    with c:
        c.execute("UPDATE bc_people SET updated_at=? WHERE id=?", (time.time() - 31 * 86400, pid))
    assert books.expire_due() == 1
    assert alice.get(f"{API}/books/{bid}/people").json()["total"] == 0 and not img.exists()


def test_deleting_an_account_removes_personal_book_and_memberships(people):
    from app.core import user_manager
    clients, bid, ids, _ = people
    alice = clients["alice"]
    pa = _personal(alice)
    _save_all_new(alice, _batch(alice), pa["id"])
    _save_all_new(alice, _batch(alice), bid)
    user_manager.delete(ids["alice"])
    assert store.conn().execute("SELECT 1 FROM bc_books WHERE id=?", (pa["id"],)).fetchone() is None
    assert not books.books_dir(pa["id"]).exists()
    assert store.conn().execute("SELECT 1 FROM bc_book_members WHERE subject_type='user' AND subject_key=?",
                                (str(ids["alice"]),)).fetchone() is None
    assert store.conn().execute("SELECT COUNT(*) FROM bc_people WHERE book_id=?", (bid,)).fetchone()[0] == 1, "共用的資料留著"


def test_single_user_mode_has_one_shared_personal_book(auth_off, fake_ocr):
    with TestClient(app_main.app) as c:
        items = c.get(f"{API}/books").json()["items"]
        personal = [i for i in items if i["kind"] == "personal"]
        assert len(personal) == 1 and personal[0]["owner"] == "local", "沒有帳號：只有一本大家共用的個人名片簿"
        assert all(i["role"] in ("owner", "edit") for i in items), "沒有帳號時不分成員"


# --------------------------------------------------------------------------- 升級

def test_upgrade_from_v1_keeps_batches(tmp_path, monkeypatch):
    """第 1 版的資料庫（只有辨識批次）升上來：批次照舊讀得到，名片簿的表補上（§1.98）。"""
    from app.core import db
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    p = tmp_path / "business_cards.sqlite"
    raw = sqlite3.connect(p)
    raw.close()
    db.migrate(p, [store._m1_initial])
    c = db.get_conn(p)
    now = time.time()
    with c:
        c.execute("INSERT INTO bc_batches(id, owner, owner_label, created_at, updated_at, mode, status, notes, langs, expires_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?)", ("a" * 32, "local", "", now, now, "single", "review", "{}", "chi_tra+eng", 0))
    assert c.execute("PRAGMA user_version").fetchone()[0] == 1
    store._INITED.discard(str(p))
    assert store.get_batch("local", "a" * 32)["status"] == "review"
    assert store.conn().execute("PRAGMA user_version").fetchone()[0] == 2
    v = books.Viewer(owner="local", auth=False)
    assert books.list_books(v)[0]["kind"] == "personal"


# --------------------------------------------------------------------------- 匯入檔案、自動去重、檢查重複

def _vcf(*cards):
    out = []
    for name, email, mobile in cards:
        out += ["BEGIN:VCARD", "VERSION:3.0", f"FN:{name}", f"N:{name};;;;", "ORG:範例科技股份有限公司"]
        if email:
            out.append(f"EMAIL;TYPE=INTERNET:{email}")
        if mobile:
            out.append(f"TEL;TYPE=CELL:{mobile}")
        out.append("END:VCARD")
    return ("\r\n".join(out) + "\r\n").encode()


def test_import_into_book_checks_duplicates_automatically(people):
    """匯入檔案進名片簿：先比對（名片簿已有的預設合併、檔案裡重複的預設不加入），按 apply 才寫進去。資料杜撰。"""
    clients, _bid, _ids, _ = people
    alice = clients["alice"]
    pa = _personal(alice)
    _save_all_new(alice, _batch(alice), pa["id"])            # 名片簿已有王小明（kevin@example.com）
    data = _vcf(("王小明", "kevin@example.com", ""),          # 名片簿已有 → 合併
                ("林小華", "hua@example.net", "0922-000-111"),  # 新的人
                ("林小華", "hua@example.net", ""),              # 檔案裡重複 → 不加入
                ("陳大文", "", "0933-000-222"))                 # 新的人
    r = alice.post(f"{API}/books/{pa['id']}/import", files={"file": ("之前匯出.vcf", data, "text/vcard")})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["kind"] == "vcf" and up["rows"] == 4
    kinds = [it["kind"] for it in up["items"]]
    assert kinds == ["match", "new", "dup", "new"], kinds
    assert [it["default"] for it in up["items"]] == ["merge", "add", "skip", "add"]
    assert up["items"][0]["match"]["reason"] == "Email 相同" and up["items"][2]["dup_of"] == 1
    assert alice.get(f"{API}/books/{pa['id']}/people").json()["total"] == 1, "比對時不可以先寫進去"
    r = alice.post(f"{API}/books/{pa['id']}/import/{up['token']}/apply", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"added": 2, "merged": 1, "skipped": 1}
    lst = alice.get(f"{API}/books/{pa['id']}/people").json()
    assert lst["total"] == 3, "同一個人不會變兩筆"
    kevin = next(p for p in lst["items"] if p["name"] == "王小明")
    person = alice.get(f"{API}/books/{pa['id']}/people/{kevin['id']}").json()
    assert [c["source"] for c in person["cards"]].count("import") == 1, "合併的那一位多一筆匯入紀錄"
    # 做完暫存檔就刪掉
    assert alice.post(f"{API}/books/{pa['id']}/import/{up['token']}/apply", json={}).status_code == 410


def test_import_decisions_and_access(people):
    clients, bid, _ids, _ = people
    alice, bob, carol = clients["alice"], clients["bob"], clients["carol"]
    data = _vcf(("林小華", "hua@example.net", ""), ("林小華", "hua@example.net", "0922-000-111"))
    up = alice.post(f"{API}/books/{bid}/import", files={"file": ("a.vcf", data, "text/vcard")}).json()
    # 檔案裡重複的那一列選「合併」→ 併進前一列匯入後的那位
    r = alice.post(f"{API}/books/{bid}/import/{up['token']}/apply", json={"decisions": {"1": "merge"}})
    assert r.json() == {"added": 1, "merged": 1, "skipped": 0}
    lst = alice.get(f"{API}/books/{bid}/people").json()
    assert lst["total"] == 1 and lst["items"][0]["mobile"] == "0922-000-111", "第二列多的手機補上"
    # 只能查詢的成員不能匯入；不是成員看不到；別人的暫存檔拿不到；處理方式要合法
    assert bob.post(f"{API}/books/{bid}/import", files={"file": ("a.vcf", data, "text/vcard")}).status_code == 403
    assert carol.post(f"{API}/books/{bid}/import", files={"file": ("a.vcf", data, "text/vcard")}).status_code == 404
    up2 = alice.post(f"{API}/books/{bid}/import", files={"file": ("a.vcf", data, "text/vcard")}).json()
    pc = _personal(carol)
    assert carol.post(f"{API}/books/{pc['id']}/import/{up2['token']}/apply", json={}).status_code == 404
    assert alice.post(f"{API}/books/{bid}/import/{up2['token']}/apply",
                      json={"decisions": {"0": "delete"}}).status_code == 400
    assert alice.post(f"{API}/books/{bid}/import/not-a-token/apply", json={}).status_code == 404
    assert alice.request("DELETE", f"{API}/books/{bid}/import/{up2['token']}").status_code == 200
    assert alice.post(f"{API}/books/{bid}/import", files={"file": ("a.pdf", b"%PDF", "application/pdf")}).status_code == 400


def test_duplicates_in_a_book_are_found_and_can_be_merged(people):
    """名片簿裡已經有的重複（Email / 手機 / 姓名＋公司相同）：「檢查重複」列出來，合併成一位。"""
    clients, _bid, _ids, _ = people
    alice = clients["alice"]
    pa = _personal(alice)
    data = _vcf(("林小華", "hua@example.net", ""), ("陳大文", "", "0933-000-222"),
                ("林小華", "hua@example.net", "0922-000-111"), ("陳大文", "", "+886 933 000 222"),
                ("張書豪", "shu@example.com", ""))
    up = alice.post(f"{API}/books/{pa['id']}/import", files={"file": ("a.vcf", data, "text/vcard")}).json()
    # 硬是全部加成新的（模擬以前重複存進來的）
    alice.post(f"{API}/books/{pa['id']}/import/{up['token']}/apply",
               json={"decisions": {str(i): "add" for i in range(5)}})
    g = alice.get(f"{API}/books/{pa['id']}/duplicates").json()
    assert g["can_edit"] is True and len(g["groups"]) == 2
    by_name = {grp["people"][0]["name"]: set(grp["reasons"]) for grp in g["groups"]}
    assert by_name == {"林小華": {"Email 相同", "姓名與公司相同"}, "陳大文": {"手機相同", "姓名與公司相同"}}, by_name
    assert all(len(grp["people"]) == 2 for grp in g["groups"]), "張書豪只有一筆，不算重複"
    grp = g["groups"][0]
    keep, others = grp["people"][0]["id"], [p["id"] for p in grp["people"][1:]]
    assert alice.post(f"{API}/books/{pa['id']}/people/merge", json={"keep": keep, "others": others}).status_code == 200
    assert len(alice.get(f"{API}/books/{pa['id']}/duplicates").json()["groups"]) == 1
    assert clients["bob"].get(f"{API}/books/{pa['id']}/duplicates").status_code == 404
