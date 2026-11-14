"""名片辨識 API：流程、改框、輸入檢查、匯出範圍、立即清除、存取控制（別人的批次一律 404）。

OCR 換成假的（回固定文字行）：這裡驗的是流程與權限，辨識品質另外用真實樣本評估。資料全部杜撰。
"""
from __future__ import annotations

import io
import json
import time

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main as app_main
from app.tools.business_card_reader import card_ocr, pipeline, store
from tests import bc_synth as S

API = "/tools/business-card-reader/api"
LINES = [("範例科技股份有限公司", 32), ("王小明", 44), ("業務經理", 20), ("電話：(02)2345-6789 分機 302", 18),
         ("手機：0912-345-678", 18), ("Email: kevin@example.com", 18), ("統一編號：04595257", 18)]


@pytest.fixture(autouse=True)
def fake_ocr(monkeypatch):
    calls = []

    def fake(img, **kw):
        calls.append(img.size)
        lines = [card_ocr.Line(text=t, box=[20, 20 + i * 50, 400, 40], conf=95, height=h) for i, (t, h) in enumerate(LINES)]
        return {"lines": lines, "rotation": 0, "engine": "fake", "image": img, "quality": 99}
    monkeypatch.setattr(card_ocr, "ocr_card", fake)
    yield calls


def _files(*imgs, name="card.jpg"):
    return [("files", (f"{i}-{name}", S.jpeg(im), "image/jpeg")) for i, im in enumerate(imgs)]


def _wait(c, bid, cond=lambda b: b["counts"]["cards_pending"] == 0, t=60):
    end = time.time() + t
    while time.time() < end:
        pipeline.wait_idle(5)
        b = c.get(f"{API}/batches/{bid}").json()
        if cond(b):
            return b
        time.sleep(0.1)
    raise AssertionError("背景辨識沒有完成")


@pytest.fixture
def anon(auth_off, tmp_path, monkeypatch):
    with TestClient(app_main.app) as c:
        yield c


# --------------------------------------------------------------------------- 流程

def test_single_mode_end_to_end(anon):
    c = anon
    im1, _ = S.scene_single()
    im2, _ = S.scene_single(deg=-5, seed=9)
    r = c.post(f"{API}/batches", files=_files(im1, im2),
               data={"mode": "single", "event": "測試展覽", "tags": "展場, 客戶", "memo": "第一批"})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["counts"]["pages"] == 2 and b["counts"]["cards"] == 2
    b = _wait(c, b["id"])
    assert b["status"] == "review" and b["counts"]["contacts"] == 2 and b["counts"]["confirmed"] == 0
    ct = b["contacts"][0]
    assert ct["notes"]["event"] == "測試展覽" and ct["notes"]["tags"] == ["展場", "客戶"] and ct["notes"]["memo"] == "第一批"
    keys = {f["key"] for f in ct["fields"]}
    assert {"company", "name", "title", "phone", "mobile", "email", "vat"} <= keys
    # 卡面圖（拉正後）
    card = b["pages"][0]["cards"][0]
    # 每張卡記下耗時（畫面寫在「辨識引擎」後面）：整張 ≥ 文字辨識 ≥ 0
    assert isinstance(card["secs"], (int, float)) and isinstance(card["ocr_secs"], (int, float))
    assert card["secs"] >= card["ocr_secs"] >= 0
    r = c.get(f"{API}/batches/{b['id']}/cards/{card['id']}/image?max=400")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    # 匯出：還沒確認任何一位 → 409，不給空檔
    r = c.post(f"{API}/batches/{b['id']}/export", json={"format": "xlsx"})
    assert r.status_code == 409
    # 確認一位 → 預覽 1 位、另有 1 位待確認
    r = c.put(f"{API}/batches/{b['id']}/contacts/{ct['id']}", json={"fields": ct["fields"], "status": "confirmed"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert all(f["status"] in ("confirmed", "deleted") for f in r.json()["fields"])
    pv = c.post(f"{API}/batches/{b['id']}/export/preview", json={"scope": "confirmed"}).json()
    assert pv["count"] == 1 and pv["skipped_check"] == 1
    pv = c.post(f"{API}/batches/{b['id']}/export/preview", json={"scope": "all"}).json()
    assert pv["count"] == 2
    for fmt, magic in (("xlsx", b"PK"), ("ods", b"PK"), ("csv", "﻿".encode()), ("vcf", b"BEGIN:VCARD"), ("json", b"{")):
        r = c.post(f"{API}/batches/{b['id']}/export", json={"format": fmt, "scope": "all", "table": True})
        assert r.status_code == 200 and r.content.startswith(magic), fmt
        assert "no-store" in r.headers.get("cache-control", "")


def test_multi_mode_frames_then_recognize(anon, fake_ocr):
    c = anon
    im, truth = S.scene_multi(6)
    r = c.post(f"{API}/batches", files=_files(im), data={"mode": "multi"})
    b = r.json()
    assert b["status"] == "frames" and b["counts"]["cards"] == 6, "多卡模式先停在確認範圍，不自己開始辨識"
    assert not fake_ocr
    page = b["pages"][0]
    # 使用者刪一張、補一張、轉一張
    cards = [{"id": x["id"], "quad": x["quad"], "rotate": x["rotate"]} for x in page["cards"]][1:]
    cards[0]["rotate"] = 90
    cards.append({"quad": [[10, 10], [400, 10], [400, 250], [10, 250]], "memo": "手動補的"})
    r = c.put(f"{API}/batches/{b['id']}/pages/{page['id']}/cards", json={"cards": cards})
    assert r.status_code == 200, r.text
    assert len(r.json()["pages"][0]["cards"]) == 6
    r = c.post(f"{API}/batches/{b['id']}/recognize")
    assert r.status_code == 200 and r.json()["queued"] == 6
    b = _wait(c, b["id"])
    assert b["counts"]["cards_done"] == 6 and b["counts"]["contacts"] == 6
    manual = [x for x in b["pages"][0]["cards"] if x["memo"] == "手動補的"][0]
    assert manual["source"] == "manual"
    memo_ct = [x for x in b["contacts"] if x["card_id"] == manual["id"]][0]
    assert "手動補的" in memo_ct["notes"]["memo"], "這張卡的備註要帶到聯絡人"
    # 只改一張的框 → 只有那一張重新辨識
    n0 = len(fake_ocr)
    cards = [{"id": x["id"], "quad": x["quad"], "rotate": x["rotate"]} for x in b["pages"][0]["cards"]]
    cards[0]["quad"] = [[p[0] + 3, p[1] + 3] for p in cards[0]["quad"]]
    c.put(f"{API}/batches/{b['id']}/pages/{page['id']}/cards", json={"cards": cards, "recognize": True})
    _wait(c, b["id"])
    assert len(fake_ocr) - n0 == 1, "框位沒改的卡不重跑"


def test_manual_edits_survive_re_recognition(anon):
    c = anon
    im, _ = S.scene_single()
    b = _wait(c, c.post(f"{API}/batches", files=_files(im), data={"mode": "single"}).json()["id"])
    ct = b["contacts"][0]
    fields = ct["fields"]
    name = next(f for f in fields if f["key"] == "name")
    name["value"] = "王大明"
    name["status"] = "confirmed"
    c.put(f"{API}/batches/{b['id']}/contacts/{ct['id']}", json={"fields": fields})
    cid = b["pages"][0]["cards"][0]["id"]
    assert c.post(f"{API}/batches/{b['id']}/cards/{cid}/retry").status_code == 200
    b = _wait(c, b["id"])
    names = [f["value"] for f in b["contacts"][0]["fields"] if f["key"] == "name"]
    assert names == ["王大明"], "重新辨識預設保留人工修改"


def test_second_person_on_the_same_card(anon):
    c = anon
    im, _ = S.scene_single()
    b = _wait(c, c.post(f"{API}/batches", files=_files(im), data={"mode": "single"}).json()["id"])
    cid = b["pages"][0]["cards"][0]["id"]
    r = c.post(f"{API}/batches/{b['id']}/cards/{cid}/contacts")
    assert r.status_code == 200 and r.json()["card_id"] == cid and r.json()["fields"] == []
    b = c.get(f"{API}/batches/{b['id']}").json()
    assert b["counts"]["contacts"] == 2


def test_pdf_pages_and_limits(anon):
    c = anon
    doc = fitz.open()
    for _ in range(2):
        p = doc.new_page(width=842, height=595)
        p.insert_image(p.rect, stream=S.jpeg(S.scene_single()[0]))
    r = c.post(f"{API}/batches", files=[("files", ("scan.pdf", doc.tobytes(), "application/pdf"))], data={"mode": "single"})
    assert r.status_code == 201 and r.json()["counts"]["pages"] == 2
    _wait(c, r.json()["id"])
    # 有開啟密碼的 PDF
    enc = io.BytesIO()
    d2 = fitz.open()
    d2.new_page()
    d2.save(enc, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    r = c.post(f"{API}/batches", files=[("files", ("locked.pdf", enc.getvalue(), "application/pdf"))], data={"mode": "single"})
    assert r.status_code == 400 and "密碼" in r.json()["detail"]
    # 格式不支援、超過檔數
    r = c.post(f"{API}/batches", files=[("files", ("x.txt", b"hello", "text/plain"))], data={"mode": "single"})
    assert r.status_code == 400
    many = [("files", (f"{i}.png", S.jpeg(Image.new("RGB", (40, 40))), "image/png")) for i in range(store.MAX_FILES + 1)]
    r = c.post(f"{API}/batches", files=many, data={"mode": "single"})
    assert r.status_code == 400 and "分批" in r.json()["detail"]
    r = c.post(f"{API}/batches", files=_files(S.card()), data={"mode": "weird"})
    assert r.status_code == 400


def test_heic_refused_with_a_hint_when_decoder_missing(anon, monkeypatch):
    from app.core import image_utils
    monkeypatch.setattr(image_utils, "heif_available", lambda: False)
    r = anon.post(f"{API}/batches", files=[("files", ("IMG_0001.HEIC", b"\x00\x00\x00\x18ftypheic", "image/heic"))],
                  data={"mode": "single"})
    assert r.status_code == 400 and "HEIC" in r.json()["detail"]


def test_decompression_bomb_is_refused(anon):
    big = Image.new("1", (9000, 9000))           # 8100 萬畫素，壓縮後很小
    buf = io.BytesIO()
    big.save(buf, "PNG")
    r = anon.post(f"{API}/batches", files=[("files", ("bomb.png", buf.getvalue(), "image/png"))], data={"mode": "single"})
    assert r.status_code == 400 and "解析度太大" in r.json()["detail"]


@pytest.mark.parametrize("cards", [
    [{"quad": [[0, 0], [float("nan"), 0], [100, 100], [0, 100]]}],
    [{"quad": [[0, 0], [10, 0], [10, 10], [0, 10]]}],
    [{"quad": [[0, 0], [300, 0], [300, 200], [0, 200]], "rotate": 45}],
    [{"quad": [[0, 0], [300, 0], [300, 200], [0, 200]]}] * 61,
    "not-a-list",
])
def test_frame_edits_are_validated(anon, cards):
    c = anon
    b = c.post(f"{API}/batches", files=_files(S.scene_multi(2)[0]), data={"mode": "multi"}).json()
    r = c.put(f"{API}/batches/{b['id']}/pages/{b['pages'][0]['id']}/cards", data=json.dumps({"cards": cards}),
              headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_field_edits_are_validated(anon):
    c = anon
    b = _wait(c, c.post(f"{API}/batches", files=_files(S.scene_single()[0]), data={"mode": "single"}).json()["id"])
    ct = b["contacts"][0]
    for bad in ([{"key": "password", "value": "x"}], [{"key": "name", "value": "x", "status": "weird"}], "x",
                [{"key": "name", "value": "x"}] * 121):
        r = c.put(f"{API}/batches/{b['id']}/contacts/{ct['id']}", json={"fields": bad})
        assert r.status_code == 400
    r = c.put(f"{API}/batches/{b['id']}/contacts/{ct['id']}", json={"status": "weird"})
    assert r.status_code == 400


def test_delete_now_removes_files(anon):
    c = anon
    b = _wait(c, c.post(f"{API}/batches", files=_files(S.scene_single()[0]), data={"mode": "single"}).json()["id"])
    d = store.batch_dir(b["id"])
    assert any(d.rglob("*.jpg"))
    assert c.delete(f"{API}/batches/{b['id']}").status_code == 200
    assert not d.exists()
    assert c.get(f"{API}/batches/{b['id']}").status_code == 404


def test_retention_expiry_deletes_batches(anon):
    c = anon
    b = c.post(f"{API}/batches", files=_files(S.scene_multi(2)[0]), data={"mode": "multi"}).json()
    assert b["expires_at"] and b["expires_at"] > time.time()
    assert store.expire_due(time.time() + (store.DEFAULT_KEEP_DAYS + 1) * 86400) >= 1
    assert c.get(f"{API}/batches/{b['id']}").status_code == 404
    assert not store.batch_dir(b["id"]).exists()


def test_invalid_ids_are_404(anon):
    c = anon
    for path in ("/batches/../../etc", "/batches/" + "z" * 32, "/batches/" + "0" * 32,
                 "/batches/" + "0" * 32 + "/cards/" + "0" * 32 + "/image"):
        assert c.get(API + path).status_code == 404


# --------------------------------------------------------------------------- 存取控制

@pytest.fixture
def two_users(admin_session):
    from app.core import permissions, sessions, user_manager
    a = user_manager.create_local("bc-alice", "bc-alice", "UserPass1234")
    b = user_manager.create_local("bc-bob", "bc-bob", "UserPass1234")
    clients = []
    for uid in (a, b):
        permissions.grant_tool("user", str(uid), "business-card-reader")
        tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
        cl = TestClient(app_main.app)
        cl.cookies.set(sessions.COOKIE_NAME, tok)
        clients.append(cl)
    yield clients


def test_other_users_cannot_touch_my_batch(two_users):
    alice, bob = two_users
    b = alice.post(f"{API}/batches", files=_files(S.scene_single()[0]), data={"mode": "single"}).json()
    b = _wait(alice, b["id"])
    bid = b["id"]
    cid = b["pages"][0]["cards"][0]["id"]
    pid = b["pages"][0]["id"]
    ctid = b["contacts"][0]["id"]
    assert bob.get(f"{API}/batches").json()["items"] == [], "清單只列自己的"
    checks = [
        ("get", f"/batches/{bid}", None),
        ("get", f"/batches/{bid}/pages/{pid}/image", None),
        ("get", f"/batches/{bid}/cards/{cid}/image", None),
        ("put", f"/batches/{bid}/pages/{pid}/cards", {"cards": []}),
        ("post", f"/batches/{bid}/recognize", None),
        ("post", f"/batches/{bid}/cards/{cid}/retry", None),
        ("put", f"/batches/{bid}/contacts/{ctid}", {"status": "confirmed"}),
        ("post", f"/batches/{bid}/cards/{cid}/contacts", None),
        ("delete", f"/batches/{bid}/contacts/{ctid}", None),
        ("post", f"/batches/{bid}/export/preview", {"scope": "all"}),
        ("post", f"/batches/{bid}/export", {"format": "json", "scope": "all"}),
        ("patch", f"/batches/{bid}", {"notes": {"memo": "x"}}),
        ("post", f"/batches/{bid}/mode", {"mode": "multi"}),
        ("post", f"/batches/{bid}/contacts/save-all", {"items": [], "confirm_all": True}),
        ("delete", f"/batches/{bid}", None),
    ]
    for method, path, body in checks:
        kw = {"json": body} if body is not None else {}
        r = getattr(bob, method)(API + path, **kw)
        assert r.status_code == 404, (method, path, r.status_code)
    # 原主人的東西完好
    again = alice.get(f"{API}/batches/{bid}").json()
    assert again["counts"]["contacts"] == 1 and again["pages"][0]["cards"]
    assert alice.get(f"{API}/batches/{bid}/cards/{cid}/image").status_code == 200


def test_no_tool_permission_is_refused(admin_session):
    """文管角色沒有這支工具：頁面與 API 都進不來（權限中介層依 /tools/business-card-reader/ 判斷）。"""
    from app.core import permissions, sessions, user_manager
    uid = user_manager.create_local("bc-clerk", "bc-clerk", "UserPass1234", roles=["clerk"])
    permissions.invalidate_cache()
    tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    cl = TestClient(app_main.app)
    cl.cookies.set(sessions.COOKIE_NAME, tok)
    assert cl.get(f"{API}/batches", follow_redirects=False).status_code in (302, 401, 403)
    r = cl.post(f"{API}/batches", files=_files(S.card()), data={"mode": "single"}, follow_redirects=False)
    assert r.status_code in (302, 401, 403)


def test_audit_records_batches_and_exports_without_contact_data(two_users, monkeypatch):
    from app.core import audit_db
    seen = []
    real = audit_db.log_event
    monkeypatch.setattr(audit_db, "log_event", lambda ev, **kw: (seen.append((ev, kw)), real(ev, **kw))[1])
    alice, _ = two_users
    b = _wait(alice, alice.post(f"{API}/batches", files=_files(S.scene_single()[0]), data={"mode": "single"}).json()["id"])
    alice.post(f"{API}/batches/{b['id']}/export", json={"format": "csv", "scope": "all"})
    evs = [e for e, _ in seen]
    assert "business_card_batch" in evs and "business_card_export" in evs
    blob = json.dumps([kw for _, kw in seen], ensure_ascii=False)
    assert "0912" not in blob and "kevin@example.com" not in blob, "稽核不記聯絡人內容"


# --------------------------------------------------------------------------- 改收件模式（不用重新上傳）

def test_switch_one_page_to_multi_keeps_the_other_pages(anon, fake_ocr):
    """使用者 2026-11-13：一張圖片拍了好幾張名片、模式忘了選 → 回頭改模式，不必重新上傳。"""
    c = anon
    one, _ = S.scene_single()
    six, _ = S.scene_multi(6)
    b = c.post(f"{API}/batches", files=_files(one, six), data={"mode": "single"}).json()
    b = _wait(c, b["id"])
    assert b["counts"]["cards"] == 2 and b["status"] == "review"
    keep_page, multi_page = b["pages"][0], b["pages"][1]
    assert multi_page["multi_hint"], "單卡模式要看得出這張像是好幾張"
    keep_card = keep_page["cards"][0]["id"]
    keep_ct = [x for x in b["contacts"] if x["card_id"] == keep_card][0]
    c.put(f"{API}/batches/{b['id']}/contacts/{keep_ct['id']}", json={"status": "confirmed"})
    n0 = len(fake_ocr)
    r = c.post(f"{API}/batches/{b['id']}/mode", json={"mode": "multi", "pages": [multi_page["id"]]})
    assert r.status_code == 200, r.text
    nb = r.json()["batch"]
    assert r.json()["pages"] == [multi_page["id"]]
    assert nb["mode"] == "multi" and nb["status"] == "frames", "改成多張：停在確認範圍"
    assert len([p for p in nb["pages"] if p["id"] == multi_page["id"]][0]["cards"]) == 6
    assert len(fake_ocr) == n0, "確認範圍之前不辨識"
    assert [p for p in nb["pages"] if p["id"] == keep_page["id"]][0]["cards"][0]["id"] == keep_card, "沒改的那頁原封不動"
    still = [x for x in nb["contacts"] if x["id"] == keep_ct["id"]]
    assert still and still[0]["status"] == "confirmed", "沒改的那頁確認過的聯絡人還在"
    r = c.post(f"{API}/batches/{b['id']}/recognize")
    assert r.json()["queued"] == 6, "只辨識改過的那頁"
    b = _wait(c, b["id"])
    assert b["status"] == "review" and b["counts"]["cards"] == 7 and b["counts"]["contacts"] == 7


def test_switch_back_to_single_recognizes_directly(anon, fake_ocr):
    c = anon
    im, _ = S.scene_single()
    b = c.post(f"{API}/batches", files=_files(im), data={"mode": "multi"}).json()
    assert b["status"] == "frames"
    r = c.post(f"{API}/batches/{b['id']}/mode", json={"mode": "single"})
    assert r.status_code == 200, r.text
    b = _wait(c, b["id"])
    assert b["mode"] == "single" and b["status"] == "review" and b["counts"]["cards_done"] == 1


def test_switch_mode_stops_a_running_batch_first(anon, monkeypatch):
    """背景還在辨識時改成多張：辨識停下、不會把剛重找的卡偷偷辨識掉、也不會寫進已刪的卡。"""
    c = anon
    real = card_ocr.ocr_card

    def slow(img, **kw):
        time.sleep(0.4)
        return real(img, **kw)
    monkeypatch.setattr(card_ocr, "ocr_card", slow)
    ims = [S.scene_single(seed=i)[0] for i in range(4)]
    b = c.post(f"{API}/batches", files=_files(*ims), data={"mode": "single"}).json()
    r = c.post(f"{API}/batches/{b['id']}/mode", json={"mode": "multi"})
    assert r.status_code == 200, r.text
    pipeline.wait_idle(30)
    b = c.get(f"{API}/batches/{b['id']}").json()
    assert b["status"] == "frames", "改成多張之後要停在確認範圍，不可以被背景改回校對"
    assert b["counts"]["cards_done"] == 0 and b["counts"]["contacts"] == 0, "重找過的卡還沒確認範圍，不可以已經辨識"
    card_ids = {x["id"] for p in b["pages"] for x in p["cards"]}
    assert all(ct["card_id"] in card_ids for ct in b["contacts"]), "不可以留下指向已刪卡面的聯絡人"


def test_mode_switch_input_is_checked(anon):
    c = anon
    b = c.post(f"{API}/batches", files=_files(S.scene_single()[0]), data={"mode": "multi"}).json()
    assert c.post(f"{API}/batches/{b['id']}/mode", json={"mode": "triple"}).status_code == 400
    assert c.post(f"{API}/batches/{b['id']}/mode", json={"mode": "multi", "pages": ["f" * 32]}).status_code == 400
    assert c.post(f"{API}/batches/{b['id']}/mode", json={"mode": "multi", "pages": "x"}).status_code == 400
    assert c.post(f"{API}/batches/{'a' * 32}/mode", json={"mode": "multi"}).status_code == 404


# --------------------------------------------------------------------------- 全部儲存 / 全部確認

def _two_contacts(c):
    b = c.post(f"{API}/batches", files=_files(S.scene_single()[0], S.scene_single(seed=9)[0]),
               data={"mode": "single"}).json()
    return _wait(c, b["id"])


def test_save_all_saves_every_draft_without_confirming(anon):
    c = anon
    b = _two_contacts(c)
    items = []
    for i, ct in enumerate(b["contacts"]):
        fs = [dict(f) for f in ct["fields"]]
        next(f for f in fs if f["key"] == "title")["value"] = f"改過的職稱{i}"
        items.append({"id": ct["id"], "fields": fs, "notes": {"memo": f"備註{i}"}})
    r = c.post(f"{API}/batches/{b['id']}/contacts/save-all", json={"items": items})
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == 2 and r.json()["confirmed"] == 0
    got = {ct["id"]: ct for ct in r.json()["batch"]["contacts"]}
    for i, it in enumerate(items):
        ct = got[it["id"]]
        assert any(f["value"] == f"改過的職稱{i}" for f in ct["fields"]) and ct["notes"]["memo"] == f"備註{i}"
        assert ct["status"] == "check", "只儲存不確認"


def test_confirm_all_confirms_open_contacts_but_not_excluded(anon):
    c = anon
    b = _two_contacts(c)
    ex = b["contacts"][1]["id"]
    c.put(f"{API}/batches/{b['id']}/contacts/{ex}", json={"status": "excluded"})
    r = c.post(f"{API}/batches/{b['id']}/contacts/save-all", json={"items": [], "confirm_all": True})
    assert r.status_code == 200 and r.json()["confirmed"] == 1
    got = {ct["id"]: ct for ct in r.json()["batch"]["contacts"]}
    assert got[ex]["status"] == "excluded", "排除的不可以被全部確認拉回來"
    ok = got[b["contacts"][0]["id"]]
    assert ok["status"] == "confirmed" and all(f["status"] in ("confirmed", "deleted") for f in ok["fields"])


def test_save_all_is_all_or_nothing(anon):
    c = anon
    b = _two_contacts(c)
    good = {"id": b["contacts"][0]["id"], "notes": {"memo": "不該存進去"}}
    bad = {"id": b["contacts"][1]["id"], "fields": [{"key": "nope", "value": "x"}]}
    assert c.post(f"{API}/batches/{b['id']}/contacts/save-all", json={"items": [good, bad]}).status_code == 400
    stranger = {"id": "0" * 32, "notes": {"memo": "x"}}
    assert c.post(f"{API}/batches/{b['id']}/contacts/save-all", json={"items": [good, stranger]}).status_code == 404
    again = c.get(f"{API}/batches/{b['id']}").json()
    assert all((ct["notes"] or {}).get("memo") != "不該存進去" for ct in again["contacts"])


# --------------------------------------------------------------------------- CSV 編碼

def test_csv_encodings(anon):
    c = anon
    b = _two_contacts(c)
    body = {"format": "csv", "scope": "all"}
    u8 = c.post(f"{API}/batches/{b['id']}/export", json=body).content
    assert u8.startswith(b"\xef\xbb\xbf"), "預設 UTF-8 含 BOM（Excel 點兩下才不會亂碼）"
    nobom = c.post(f"{API}/batches/{b['id']}/export", json={**body, "encoding": "utf-8", "bom": False}).content
    assert not nobom.startswith(b"\xef\xbb\xbf") and nobom.decode("utf-8")
    r = c.post(f"{API}/batches/{b['id']}/export", json={**body, "encoding": "big5"})
    assert r.status_code == 200 and "big5" in r.headers["content-type"]
    assert "王小明" in r.content.decode("cp950") and not r.content.startswith(b"\xef\xbb\xbf")
    assert c.post(f"{API}/batches/{b['id']}/export", json={**body, "encoding": "latin-9"}).status_code == 400
    pv = c.post(f"{API}/batches/{b['id']}/export/preview", json={"scope": "all"}).json()
    assert pv["big5_missing_count"] == 0


def test_big5_refuses_characters_it_cannot_hold(anon):
    """Big5 沒有的字（杜撰的名字用「堃」以外也試簡體才有的「们」）不可以無聲變成問號。"""
    c = anon
    b = _two_contacts(c)
    ct = b["contacts"][0]
    fs = [dict(f) for f in ct["fields"]]
    next(f for f in fs if f["key"] == "name")["value"] = "王们"
    c.put(f"{API}/batches/{b['id']}/contacts/{ct['id']}", json={"fields": fs})
    pv = c.post(f"{API}/batches/{b['id']}/export/preview", json={"scope": "all"}).json()
    assert pv["big5_missing_count"] >= 1 and "们" in pv["big5_missing"]
    r = c.post(f"{API}/batches/{b['id']}/export", json={"format": "csv", "scope": "all", "encoding": "big5"})
    assert r.status_code == 422 and "们" in r.json()["detail"]
    assert c.post(f"{API}/batches/{b['id']}/export", json={"format": "csv", "scope": "all"}).status_code == 200
