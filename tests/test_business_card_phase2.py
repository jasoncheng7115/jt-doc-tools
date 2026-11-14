"""名片辨識第 2 版：同一批合併（正反面 / 同一人）與解除、辨識走「我的作業」、跟手上的名片清單整合、AI 協助（選用）。

OCR 與 LLM 都換成假的；資料全部杜撰（王小明、Kevin Sample、example.com）。
"""
from __future__ import annotations

import json

import io
import time

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.tools.business_card_reader import card_ocr, pipeline
from tests import bc_synth as S

API = "/tools/business-card-reader/api"


@pytest.fixture
def ocr(monkeypatch):
    """依卡面的次序回不同的內容：第 1 張中文面、第 2 張英文面（同一支手機與 Email）、第 3 張另一個人。"""
    seq = [
        [("範例科技股份有限公司", 32), ("王小明", 44), ("業務經理", 20), ("手機：0912-345-678", 18), ("Email: kevin@example.com", 18)],
        [("Example Tech Co., Ltd.", 30), ("Kevin Sample", 40), ("Sales Manager", 20), ("Mobile: 0912-345-678", 18),
         ("kevin@example.com", 18), ("www.example.com", 18)],
        [("範例顧問有限公司", 32), ("陳大文", 44), ("Email: daming@example.org", 18)],
    ]
    state = {"i": 0, "seq": seq}

    def fake(img, **kw):
        lines = state["seq"][state["i"] % len(state["seq"])]
        state["i"] += 1
        ls = [card_ocr.Line(text=t, box=[20, 20 + i * 50, 500, 40], conf=95, height=h) for i, (t, h) in enumerate(lines)]
        return {"lines": ls, "rotation": 0, "engine": "fake", "image": img, "quality": 99}
    monkeypatch.setattr(card_ocr, "ocr_card", fake)
    return state


def _wait(c, bid, t=60):
    end = time.time() + t
    while time.time() < end:
        pipeline.wait_idle(5)
        b = c.get(f"{API}/batches/{bid}").json()
        if b["counts"]["cards_pending"] == 0 and b["status"] == "review":
            return b
        time.sleep(0.1)
    raise AssertionError("辨識沒有完成")


def _batch(c, n):
    files = [("files", (f"{i}.jpg", S.jpeg(S.scene_single(seed=i + 11)[0]), "image/jpeg")) for i in range(n)]
    r = c.post(f"{API}/batches", files=files, data={"mode": "single"})
    assert r.status_code == 201, r.text
    return _wait(c, r.json()["id"])


@pytest.fixture
def anon(auth_off):
    with TestClient(app_main.app) as c:
        yield c


# --------------------------------------------------------------------------- 正反面 / 同一人

def test_front_and_back_are_suggested_merged_and_can_be_undone(anon, ocr):
    c = anon
    b = _batch(c, 3)
    hints = b["merge_hints"]
    assert len(hints) == 1 and hints[0]["reason"] == "front_back", hints
    by_card = {ct["card_id"]: ct for ct in b["contacts"]}
    cards = [cd["id"] for p in b["pages"] for cd in p["cards"]]
    front, back = by_card[cards[0]], by_card[cards[1]]
    r = c.post(f"{API}/batches/{b['id']}/contacts/merge", json={"keep": front["id"], "other": back["id"]})
    assert r.status_code == 200, r.text
    m = r.json()
    vals = {}
    for f in m["fields"]:
        vals.setdefault(f["key"], []).append(f["value"])
    assert vals["name"] == ["王小明"] and vals["name_en"] == ["Kevin Sample"], "英文面的姓名放進外文姓名"
    assert vals["company_en"] == ["Example Tech Co., Ltd."]
    assert len(vals["mobile"]) == 1 and len(vals["email"]) == 1, "同一支手機、同一個 Email 不重複"
    assert set(m["card_ids"]) == {cards[0], cards[1]}
    b2 = c.get(f"{API}/batches/{b['id']}").json()
    assert b2["counts"]["contacts"] == 2 and b2["merge_hints"] == []
    assert next(x for x in b2["contacts"] if x["id"] == front["id"])["merged"] == 1
    r = c.post(f"{API}/batches/{b['id']}/contacts/{front['id']}/unmerge")
    assert r.status_code == 200
    b3 = c.get(f"{API}/batches/{b['id']}").json()
    assert b3["counts"]["contacts"] == 3, "解除合併：兩位都回來"
    back2 = next(x for x in b3["contacts"] if x["id"] == back["id"])
    assert any(f["value"] == "Kevin Sample" for f in back2["fields"])
    assert c.post(f"{API}/batches/{b['id']}/contacts/{front['id']}/unmerge").status_code == 409
    assert c.post(f"{API}/batches/{b['id']}/contacts/merge", json={"keep": front["id"], "other": front["id"]}).status_code == 400


# --------------------------------------------------------------------------- 我的作業

def test_recognition_shows_up_in_my_jobs_with_an_open_link(anon, ocr):
    c = anon
    b = _batch(c, 2)
    jobs = c.get("/api/jobs").json()
    items = jobs["jobs"]
    mine = [j for j in items if j.get("tool_id") == "business-card-reader"]
    assert mine, "辨識要出現在「我的作業」"
    j = mine[0]
    assert j["view_url"] == f"/tools/business-card-reader/#batch={b['id']}" and j["view_ok"]
    assert "名片辨識" in (j.get("name") or j.get("display_name") or j.get("filename") or "")


def test_cancelling_the_job_stops_recognition_but_keeps_the_batch(anon, monkeypatch):
    from app.core import job_manager as jm
    started = []
    real = card_ocr.ocr_card

    def slow(img, **kw):
        started.append(1)
        time.sleep(0.5)
        return {"lines": [card_ocr.Line(text="王小明", box=[1, 1, 100, 40], conf=95, height=40)],
                "rotation": 0, "engine": "fake", "image": img, "quality": 99}
    monkeypatch.setattr(card_ocr, "ocr_card", slow)
    files = [("files", (f"{i}.jpg", S.jpeg(S.scene_single(seed=i + 20)[0]), "image/jpeg")) for i in range(5)]
    bid = anon.post(f"{API}/batches", files=files, data={"mode": "single"}).json()["id"]
    end = time.time() + 20
    job = None
    while time.time() < end and job is None:
        job = next((x for x in jm.job_manager.list() if (x.meta or {}).get("batch") == bid), None) if hasattr(jm.job_manager, "list") else None
        if job is None:
            for jid in list(getattr(jm.job_manager, "_jobs", {})):
                x = jm.job_manager._jobs.get(jid)
                if x and (x.meta or {}).get("batch") == bid:
                    job = x
        time.sleep(0.05)
    assert job is not None
    while not started and time.time() < end:
        time.sleep(0.05)
    job.cancelled = True
    pipeline.wait_idle(30)
    b = anon.get(f"{API}/batches/{bid}").json()
    assert b["status"] == "review" and b["counts"]["cards_pending"] >= 1, "取消後還沒辨識的留著，可以再按辨識"
    monkeypatch.setattr(card_ocr, "ocr_card", real)


# --------------------------------------------------------------------------- 跟手上的名片清單整合

def _xlsx(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_list_merge_round_trip(anon, ocr):
    c = anon
    b = _batch(c, 3)
    for ct in b["contacts"]:
        c.put(f"{API}/batches/{b['id']}/contacts/{ct['id']}", json={"status": "confirmed"})
    data = _xlsx([["姓名", "公司", "Email", "手機", "備註"],
                  ["王小明", "範例科技股份有限公司", "kevin@example.com", "", "老客戶"],
                  ["林小華", "範例貿易有限公司", "hua@example.net", "0922-000-111", ""]])
    r = c.post(f"{API}/batches/{b['id']}/list-merge", files={"file": ("清單.xlsx", data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["rows"] == 2 and set(up["mapping"].values()) >= {"name", "company", "email", "mobile"}
    tok = up["token"]
    plan = c.post(f"{API}/batches/{b['id']}/list-merge/{tok}/plan", json={"mapping": up["mapping"], "scope": "all"}).json()
    kinds = {it["kind"] for it in plan["items"]}
    assert "update" in kinds and "new" in kinds and "untouched" in kinds, plan["counts"]
    r = c.post(f"{API}/batches/{b['id']}/list-merge/{tok}/download",
               json={"mapping": up["mapping"], "scope": "all", "format": "csv", "encoding": "utf-8", "bom": True})
    assert r.status_code == 200, r.text
    text = r.content.decode("utf-8-sig")
    assert "林小華" in text, "清單原本的列都要留著"
    assert "0912-345-678" in text, "清單缺的手機補上"
    assert "陳大文" in text, "新的人接在最後"
    assert text.index("王小明") < text.index("林小華") < text.index("陳大文"), "原本的順序不變"
    # 也可以存回 VCF：原本的人、補上的手機、新的人都在
    r = c.post(f"{API}/batches/{b['id']}/list-merge/{tok}/download",
               json={"mapping": up["mapping"], "scope": "all", "format": "vcf"})
    assert r.status_code == 200 and r.content.startswith(b"BEGIN:VCARD"), r.text[:200]
    v = r.content.decode("utf-8")
    assert v.count("BEGIN:VCARD") == 3 and "林小華" in v and "0912-345-678" in v and "陳大文" in v
    assert c.post(f"{API}/batches/{b['id']}/list-merge/{tok}/download", json={"format": "pdf"}).status_code == 400
    assert c.request("DELETE", f"{API}/batches/{b['id']}/list-merge/{tok}").status_code == 200
    assert c.post(f"{API}/batches/{b['id']}/list-merge/{tok}/plan", json={}).status_code == 410, "刪掉之後不能再用"


@pytest.mark.parametrize("fmt", ["vcf", "json", "xlsx"])
def test_previous_export_merges_without_duplicates(anon, ocr, fmt):
    """上傳之前從這裡匯出的檔（VCF / JSON / 試算表），跟同一批再合併：同一個人不會多一份。"""
    c = anon
    b = _batch(c, 3)
    for ct in b["contacts"]:
        c.put(f"{API}/batches/{b['id']}/contacts/{ct['id']}", json={"status": "confirmed"})
    old = c.post(f"{API}/batches/{b['id']}/export", json={"format": fmt, "scope": "all"})
    assert old.status_code == 200
    up = c.post(f"{API}/batches/{b['id']}/list-merge", files={"file": (f"之前匯出.{fmt}", old.content, "application/octet-stream")})
    assert up.status_code == 200, up.text
    up = up.json()
    assert up["kind"] == fmt and up["rows"] == 3
    tok = up["token"]
    plan = c.post(f"{API}/batches/{b['id']}/list-merge/{tok}/plan", json={"scope": "all"}).json()
    assert plan["counts"] == {"same": 3}, json.dumps([{k: it.get(k) for k in ("kind", "row", "contact_id", "match", "diffs", "card", "list")} for it in plan["items"]], ensure_ascii=False)
    r = c.post(f"{API}/batches/{b['id']}/list-merge/{tok}/download", json={"scope": "all", "format": "json"})
    assert r.status_code == 200
    assert len(json.loads(r.content)["contacts"]) == 3, "同一個人不可以變兩份"


def test_list_merge_rejects_bad_files_and_strangers(two_users_bc, ocr):
    alice, bob = two_users_bc
    b = _batch(alice, 1)
    r = alice.post(f"{API}/batches/{b['id']}/list-merge", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 400
    data = _xlsx([["姓名", "Email"], ["王小明", "kevin@example.com"]])
    tok = alice.post(f"{API}/batches/{b['id']}/list-merge", files={"file": ("a.xlsx", data, "application/octet-stream")}).json()["token"]
    assert bob.post(f"{API}/batches/{b['id']}/list-merge/{tok}/plan", json={}).status_code == 404
    bb = _batch(bob, 1)
    assert bob.post(f"{API}/batches/{bb['id']}/list-merge/{tok}/plan", json={}).status_code == 404, "別人的清單拿不到"
    assert alice.post(f"{API}/batches/{b['id']}/list-merge/{'x' * 32}/plan", json={}).status_code == 404


@pytest.fixture
def two_users_bc(admin_session):
    from app.core import permissions, sessions, user_manager
    out = []
    for n in ("bcp-alice", "bcp-bob"):
        uid = user_manager.create_local(n, n, "UserPass1234", roles=[])
        permissions.grant_tool("user", str(uid), "business-card-reader")
        tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
        cl = TestClient(app_main.app)
        cl.cookies.set(sessions.COOKIE_NAME, tok)
        out.append(cl)
    yield out
    for cl in out:
        cl.close()


# --------------------------------------------------------------------------- AI 協助（選用）

class _FakeLLM:
    calls: list = []

    def text_query(self, prompt, model, **kw):
        _FakeLLM.calls.append(prompt)
        if "same person" in prompt:
            return '{"same": true, "confidence": 0.9, "reason": "同一支手機"}'
        return '{"fields": [{"key": "name", "value": "王小明"}, {"key": "title", "value": "業務總監"}]}'


@pytest.fixture
def ai_on(monkeypatch):
    from app.core.llm_settings import llm_settings
    from app.tools.business_card_reader import ai
    monkeypatch.setattr(llm_settings, "is_enabled", lambda: True)
    monkeypatch.setattr(llm_settings, "make_client", lambda *a, **k: _FakeLLM())
    monkeypatch.setattr(llm_settings, "get_model_for", lambda tid: "fake-model")
    monkeypatch.setattr(llm_settings, "base_url_for", lambda tid: "http://llm.example.com:11434")
    _FakeLLM.calls = []
    ai.set_settings(True, "text")
    yield
    ai.set_settings(False, "text")


def test_ai_is_off_by_default_and_needs_both_switches(anon, ocr, monkeypatch):
    from app.core.llm_settings import llm_settings
    from app.tools.business_card_reader import ai
    ai.set_settings(False, "text")
    b = _batch(anon, 1)
    ct = b["contacts"][0]["id"]
    assert anon.get(f"{API}/ai").json()["available"] is False
    assert anon.post(f"{API}/batches/{b['id']}/contacts/{ct}/ai-suggest").status_code == 409
    monkeypatch.setattr(llm_settings, "is_enabled", lambda: False)
    ai.set_settings(True, "text")
    assert anon.get(f"{API}/ai").json()["available"] is False, "全站 LLM 沒開也不行"
    ai.set_settings(False, "text")


def test_ai_suggestions_are_marked_for_checking_and_flag_changed_text(anon, ocr, ai_on):
    b = _batch(anon, 1)
    ct = b["contacts"][0]["id"]
    r = anon.post(f"{API}/batches/{b['id']}/contacts/{ct}/ai-suggest")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["model"] == "fake-model" and out["info"]["host"] == "llm.example.com"
    by = {f["key"]: f for f in out["fields"]}
    assert all(f["status"] == "check" for f in out["fields"]), "AI 的建議一律要人確認"
    assert "不一樣" in by["title"]["reason"], "AI 改了字（OCR 原文是業務經理）要寫出來"
    assert "不一樣" not in by["name"]["reason"]
    prompt = _FakeLLM.calls[-1]
    assert "王小明" in prompt and "<card_text>" in prompt, "只送這一張的 OCR 文字"
    again = anon.get(f"{API}/batches/{b['id']}").json()
    assert all(f["source"].get("method") != "ai" for f in again["contacts"][0]["fields"]), "建議不會自己寫進聯絡人"


def test_ai_same_person_check_only_sends_the_pair(anon, ocr, ai_on):
    b = _batch(anon, 3)
    data = _xlsx([["姓名", "公司"], ["王小明", "範例科技"]])
    tok = anon.post(f"{API}/batches/{b['id']}/list-merge", files={"file": ("a.xlsx", data, "application/octet-stream")}).json()["token"]
    ct = b["contacts"][0]["id"]
    r = anon.post(f"{API}/batches/{b['id']}/list-merge/{tok}/ai-check", json={"scope": "all", "pairs": [{"contact_id": ct, "row": 0}]})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["same"] is True
    prompt = _FakeLLM.calls[-1]
    assert "<record_a>" in prompt and "陳大文" not in prompt, "只送那一組，不送其他人"
    assert anon.post(f"{API}/batches/{b['id']}/list-merge/{tok}/ai-check", json={"pairs": []}).status_code == 400


def test_admin_ai_switch(admin_session):
    c, *_ = admin_session
    r = c.put("/admin/business-cards/api/ai", json={"enabled": True, "mode": "vision"})
    assert r.status_code == 200 and r.json()["ai"]["enabled"] is True and r.json()["ai"]["mode"] == "vision"
    assert c.put("/admin/business-cards/api/ai", json={"enabled": "yes"}).status_code == 400
    assert c.put("/admin/business-cards/api/ai", json={"enabled": False, "mode": "magic"}).status_code == 400
    c.put("/admin/business-cards/api/ai", json={"enabled": False, "mode": "text"})
