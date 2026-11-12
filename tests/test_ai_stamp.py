"""AI 用印（v1.17.38）：AI 經 MCP 蓋資產庫裡經 AI 授權的章。

設計 `docs-share/mcp/jtdt-ai-stamp-design-v1.md`。使用者 2026-11-12 決定：受管制的章 AI 一律不能用、
需要確認時由 Token 綁定的本人確認、騎縫章只收資產庫核准的章、印章與簽名預設都要確認。

驗的是：
* 條件**逐一拿掉**都被拒，而且 AI 的工具清單裡看不到（不是叫了才被拒）
* 只能用經 AI 授權的章；不收上傳圖片、不收 png_b64、混一顆不行的整件拒絕；每日上限
* 需要確認：核准前 AI 拿不到檔案、只能用網頁登入核准（帶 Token 的 403）、退回與逾時刪檔
* 原本的用印 / 騎縫章 API 不收經 MCP 來的呼叫；產出的 PDF 有回查用的註記；稽核有記
"""
from __future__ import annotations

import io
import json
import time

import fitz
import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from tests.conftest import _make_pdf
from tests.test_mcp_server import SITE, _call, _upload_inline


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (80, 80), (200, 20, 20, 255)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def env(admin_session):
    from app.core import api_tokens as at, notify_settings, permissions, seal_policy, user_manager, workspace as ws
    from app.core.asset_manager import asset_manager
    from app.mcp import runner, server, settings as ms, tickets
    ms.save({"enabled": True, "tools": None, "rate_per_minute": 600, "max_concurrent": 4,
             "ticket_bind_ip": True, "ticket_allow_http": False, "allow_stamp": True})
    notify_settings.save({"site_url": SITE})
    ws.save_settings({"enabled": True})
    runner._reset_for_tests()
    server._calls.clear()
    tickets._reset_for_tests()
    uid = user_manager.create_local("ai-stamper", "ai-stamper", "UserPass1234")
    other = user_manager.create_local("ai-other", "ai-other", "UserPass1234")
    for u in (uid, other):
        permissions.grant_tool("user", str(u), "pdf-stamp")
        permissions.grant_tool("user", str(u), "pdf-seam-stamp")
    tok = at.api_tokens.create("ai-stamp-token")
    at.api_tokens.set_mcp(tok.token, True)
    at.api_tokens.assign_owner(tok.token, uid)
    at.api_tokens.set_stamp(tok.token, True)
    seal = asset_manager.create_from_bytes("AI 測試公司章", "stamp", _png())
    sig = asset_manager.create_from_bytes("AI 測試簽名", "signature", _png())
    seal_policy.set_policy(seal.id, "stamp", {"ai": {"enabled": True, "subjects": [{"type": "user", "key": str(uid)}],
                                                     "confirm": False}})
    with TestClient(app_main.app) as c:
        yield {"c": c, "tok": tok.token, "uid": uid, "other": other, "seal": seal, "sig": sig}
    at.api_tokens.revoke(tok.token)
    for a in (seal, sig):
        seal_policy.forget_asset(a.id)
        asset_manager.delete(a.id)
    ms.save({"enabled": False, "tools": None, "allow_stamp": False})
    notify_settings.save({"site_url": ""})


def _place(aid, page=0):
    return json.dumps([{"asset_id": aid, "page": page}])


def _h(e, via=True) -> dict:
    """AI 用印只認 MCP 在行程內轉呼叫（帶程序內密值）；測試直接打端點時要帶同一個標頭。"""
    from app.mcp import runner
    h = {"Authorization": f"Bearer {e['tok']}"}
    if via:
        h[runner.VIA_HEADER] = runner.VIA_SECRET
    return h


def _api(e, path="/tools/pdf-stamp/api/ai-stamp", data=None, pdf=None, headers=None, via=True):
    return e["c"].post(path, files={"file": ("doc.pdf", pdf or _make_pdf(2), "application/pdf")},
                       data=data if data is not None else {"placements_json": _place(e["seal"].id)},
                       headers={**_h(e, via), **(headers or {})})


def _visible(e) -> set[str]:
    err, res = _call(e["c"], e["tok"], "jtdt_catalog", {})
    assert not err, res
    return {t["tool"] for t in res["tools"]}


def _set_ai(e, **ai):
    from app.core import seal_policy
    seal_policy.set_policy(e["seal"].id, "stamp", {"ai": ai})


# --------------------------------------------------------------------------- 正常流程

def test_no_confirmation_stamps_directly_and_marks_the_pdf(env):
    e = env
    names = _visible(e)
    assert {"stamp_list", "pdf_stamp", "pdf_seam_stamp", "stamp_request_status"} <= names
    err, res = _call(e["c"], e["tok"], "jtdt_run", {"tool": "stamp_list"})
    assert not err, res
    seals = res["result"]["seals"]
    assert [s["id"] for s in seals] == [e["seal"].id], "沒開 AI 授權的簽名不在清單裡"
    assert "thumb" not in json.dumps(seals) and "file" not in seals[0], "不回圖檔"
    doc = fitz.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    plain = doc.tobytes()
    doc.close()
    fid = _upload_inline(e["c"], e["tok"], plain, "contract.pdf")
    err, res = _call(e["c"], e["tok"], "jtdt_run", {"tool": "pdf_stamp", "files": [fid],
                                                     "params": {"placements_json": _place(e["seal"].id, 1)}})
    assert not err and res["status"] == "done", res
    from app.core import ai_stamp, workspace as ws
    from app.mcp import registry, runner
    got = runner.resolve_inputs(registry.BY_NAME["pdf_compress"], [res["files"][0]["file_id"]],
                                ws.key_for_user_id(e["uid"]))
    from pathlib import Path
    assert ai_stamp.read_mark(Path(got[0]["path"])).startswith(res["request_id"]), "PDF 有回查用的註記"
    with fitz.open(got[0]["path"]) as d:
        assert not d[0].get_images() and d[1].get_images(), "蓋在指定的第 2 頁"
    rows = ai_stamp.list_all(user_id=e["uid"])
    assert rows[0]["status"] == "done" and rows[0]["assets"][0]["id"] == e["seal"].id


def test_confirmation_flow(env):
    e = env
    _set_ai(e, confirm=True)
    r = _api(e)
    assert r.status_code == 202, r.text
    rid = r.json()["request_id"]
    h = _h(e)
    st = e["c"].get(f"/api/ai-stamp/requests/{rid}", headers=h).json()
    assert st["status"] == "pending" and "file" not in st, "核准前拿不到檔案"
    # AI 不可以自己核准：帶 Token 的一律 403
    assert e["c"].post(f"/ai-stamp/api/{rid}/approve", headers=h).status_code == 403
    # 別人（另一個帳號）看不到、也核准不了
    from app.core import sessions
    other_tok, _ = sessions.issue(e["other"], remember=False, ip="127.0.0.1", ua="pytest")
    oc = TestClient(app_main.app)
    oc.cookies.set(sessions.COOKIE_NAME, other_tok)
    assert oc.post(f"/ai-stamp/api/{rid}/approve").status_code == 404
    assert oc.get("/ai-stamp/api/pending").json()["items"] == []
    # 本人在網頁上核准
    me_tok, _ = sessions.issue(e["uid"], remember=False, ip="127.0.0.1", ua="pytest")
    me = TestClient(app_main.app)
    me.cookies.set(sessions.COOKIE_NAME, me_tok)
    pend = me.get("/ai-stamp/api/pending").json()["items"]
    assert [p["request_id"] for p in pend] == [rid]
    assert me.get(f"/ai-stamp/api/{rid}/preview/0").headers["content-type"] == "image/png"
    r = me.post(f"/ai-stamp/api/{rid}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved", r.text
    st = e["c"].get(f"/api/ai-stamp/requests/{rid}", headers=h).json()
    assert st["status"] == "approved" and st["file"]["file_id"], "核准後 AI 拿得到工作區的檔案"
    from app.core import ai_stamp
    assert not (ai_stamp.files_root() / rid).exists(), "待確認區的檔案核准後刪掉"
    assert me.post(f"/ai-stamp/api/{rid}/approve").status_code == 409, "不能核准兩次"
    # 退回
    rid2 = _api(e).json()["request_id"]
    assert me.post(f"/ai-stamp/api/{rid2}/reject").json()["status"] == "rejected"
    assert not (ai_stamp.files_root() / rid2).exists()
    st = e["c"].get(f"/api/ai-stamp/requests/{rid2}", headers=h).json()
    assert st["status"] == "rejected" and "file" not in st


def test_confirmation_through_mcp_reports_pending(env):
    e = env
    _set_ai(e, confirm=True)
    fid = _upload_inline(e["c"], e["tok"], _make_pdf(1), "a.pdf")
    err, res = _call(e["c"], e["tok"], "jtdt_run", {"tool": "pdf_stamp", "files": [fid],
                                                     "params": {"placements_json": _place(e["seal"].id)}})
    assert not err and res["status"] == "pending" and "files" not in res, res
    err, st = _call(e["c"], e["tok"], "jtdt_run", {"tool": "stamp_request_status",
                                                    "params": {"request_id": res["request_id"]}})
    assert not err and st["result"]["status"] == "pending"


def test_unconfirmed_requests_expire_and_files_are_deleted(env):
    e = env
    _set_ai(e, confirm=True)
    rid = _api(e).json()["request_id"]
    from app.core import ai_stamp
    assert (ai_stamp.files_root() / rid).exists()
    assert ai_stamp.expire_due(time.time() + ai_stamp.CONFIRM_HOURS * 3600 + 1) >= 1
    assert not (ai_stamp.files_root() / rid).exists()
    assert ai_stamp.get_for_user(rid, e["uid"])["status"] == "expired"


# --------------------------------------------------------------------------- 條件逐一拿掉

def _drop_mcp_switch(e):
    from app.mcp import settings as ms
    ms.save({"allow_stamp": False})


def _drop_token_flag(e):
    from app.core import api_tokens as at
    at.api_tokens.set_stamp(e["tok"], False)


def _drop_permission(e):
    from app.core import permissions
    permissions.revoke_tool("user", str(e["uid"]), "pdf-stamp")
    permissions.revoke_tool("user", str(e["uid"]), "pdf-seam-stamp")
    permissions.invalidate_cache() if hasattr(permissions, "invalidate_cache") else None


def _drop_seal_ai(e):
    _set_ai(e, enabled=False)


def _drop_subject(e):
    _set_ai(e, subjects=[{"type": "user", "key": str(e["other"])}])


def _make_controlled(e):
    from app.core import seal_policy
    seal_policy.set_enabled(True)
    seal_policy.set_policy(e["seal"].id, "stamp", {"mode": "approval", "ai": {"enabled": False}})


@pytest.mark.parametrize("drop", [_drop_mcp_switch, _drop_token_flag, _drop_permission, _drop_seal_ai,
                                  _drop_subject, _make_controlled],
                         ids=["mcp_switch", "token_flag", "permission", "seal_ai", "subject", "controlled"])
def test_each_condition_refuses(env, drop):
    e = env
    assert _api(e).status_code == 200, "先確認條件都齊時蓋得了"
    drop(e)
    r = _api(e)
    assert r.status_code in (403, 404), (drop.__name__, r.status_code, r.text)
    try:
        from app.core import seal_policy
        if drop is _make_controlled:
            seal_policy.set_policy(e["seal"].id, "stamp", {"mode": "direct"})
            seal_policy.set_enabled(False)
    except Exception:
        pass


@pytest.mark.parametrize("drop", [_drop_mcp_switch, _drop_token_flag, _drop_permission],
                         ids=["mcp_switch", "token_flag", "permission"])
def test_stamp_tools_disappear_from_the_ai_tool_list(env, drop):
    e = env
    drop(e)
    assert not ({"stamp_list", "pdf_stamp", "pdf_seam_stamp", "stamp_request_status"} & _visible(e))
    err, msg = _call(e["c"], e["tok"], "jtdt_run", {"tool": "stamp_list"})
    assert err


@pytest.mark.parametrize("drop", [_drop_seal_ai, _drop_subject, _make_controlled],
                         ids=["seal_ai", "subject", "controlled"])
def test_seal_disappears_from_the_list(env, drop):
    e = env
    drop(e)
    err, res = _call(e["c"], e["tok"], "jtdt_run", {"tool": "stamp_list"})
    assert not err and res["result"]["seals"] == [], res
    try:
        from app.core import seal_policy
        if drop is _make_controlled:
            seal_policy.set_policy(e["seal"].id, "stamp", {"mode": "direct"})
            seal_policy.set_enabled(False)
    except Exception:
        pass


def test_login_must_be_enabled(env):
    from app.core import ai_stamp, auth_settings

    class _Req:
        class state:  # noqa: N801
            api_token_label = "t"
            api_token_stamp = True
            user = {"user_id": env["uid"]}

    s = auth_settings.get()
    try:
        s["backend"] = "off"
        auth_settings.save(s)
        with pytest.raises(ai_stamp.AiStampError) as ex:
            ai_stamp.require(_Req(), "pdf-stamp")
        assert ex.value.code == "NEEDS_LOGIN"
    finally:
        s["backend"] = "local"
        auth_settings.save(s)


def test_session_without_token_cannot_use_ai_stamp(env):
    """網頁登入（沒有 Token）不能走 AI 用印這支 —— 那是給 AI 的入口，條件包含 Token。"""
    from app.core import sessions
    t, _ = sessions.issue(env["uid"], remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, t)
    r = c.post("/tools/pdf-stamp/api/ai-stamp", files={"file": ("d.pdf", _make_pdf(1), "application/pdf")},
               data={"placements_json": _place(env["seal"].id)})
    assert r.status_code == 403


# --------------------------------------------------------------------------- 只用核准的章

@pytest.mark.parametrize("items", [
    [{"page": 0}],
    [{"asset_id": "x" * 32, "page": 0}],
    [{"asset_id": "SEAL", "page": 0, "png_b64": "aGVsbG8="}],
    [{"asset_id": "SEAL", "page": 0, "kind": "date"}],
])
def test_only_listed_seals_no_uploads(env, items):
    e = env
    items = json.loads(json.dumps(items).replace("SEAL", e["seal"].id))
    r = _api(e, data={"placements_json": json.dumps(items)})
    assert r.status_code in (400, 403), r.text


def test_one_disallowed_seal_refuses_the_whole_request(env):
    e = env
    items = [{"asset_id": e["seal"].id, "page": 0}, {"asset_id": e["sig"].id, "page": 1}]
    r = _api(e, data={"placements_json": json.dumps(items)})
    assert r.status_code == 403 and "ASSET_NOT_ALLOWED" in r.text
    from app.core import ai_stamp
    assert ai_stamp.list_all(user_id=e["uid"]) == [], "整件拒絕，沒有蓋一半"


def test_daily_limit(env):
    e = env
    _set_ai(e, daily_limit=1)
    assert _api(e).status_code == 200
    r = _api(e)
    assert r.status_code == 429 and "LIMIT_REACHED" in r.text


def test_seam_stamp_only_with_an_approved_seal(env):
    e = env
    r = _api(e, "/tools/pdf-seam-stamp/api/ai-seam-stamp", data={"asset_id": e["seal"].id}, pdf=_make_pdf(3))
    assert r.status_code == 200 and r.headers.get("X-JTDT-AI-Stamp"), r.text
    r = _api(e, "/tools/pdf-seam-stamp/api/ai-seam-stamp", data={"asset_id": e["sig"].id}, pdf=_make_pdf(3))
    assert r.status_code == 403, "簽名不能當騎縫章、也沒開 AI 授權"


# --------------------------------------------------------------------------- 不可以從別的路繞過

def test_regular_stamp_apis_refuse_calls_from_mcp(env):
    from app.mcp import runner
    e = env
    h = {"Authorization": f"Bearer {e['tok']}", "X-JTDT-Via-MCP": runner.VIA_SECRET}
    r = e["c"].post("/tools/pdf-stamp/api/pdf-stamp", files={"file": ("d.pdf", _make_pdf(1), "application/pdf")},
                    data={"asset_id": e["seal"].id}, headers=h)
    assert r.status_code == 403
    r = e["c"].post("/tools/pdf-seam-stamp/api/pdf-seam-stamp",
                    files={"file": ("d.pdf", _make_pdf(2), "application/pdf")}, headers=h)
    assert r.status_code == 403


def test_reassigning_the_token_revokes_stamping(env):
    from app.core import api_tokens as at
    e = env
    at.api_tokens.assign_owner(e["tok"], e["other"])
    assert not at.api_tokens.find(e["tok"]).get("stamp"), "改綁給別人，用印權限不跟著轉過去"


def test_admin_cannot_turn_on_ai_for_a_controlled_seal(env):
    from app.core import seal_policy
    seal_policy.set_enabled(True)
    try:
        with pytest.raises(seal_policy.PolicyError) as ex:
            seal_policy.set_policy(env["seal"].id, "stamp", {"mode": "approval"})
        assert ex.value.code == "AI_ON_CONTROLLED"
        got = seal_policy.set_policy(env["seal"].id, "stamp", {"mode": "approval", "ai": {"enabled": False}})
        assert got["mode"] == "approval" and not got["ai"]["enabled"]
    finally:
        seal_policy.set_policy(env["seal"].id, "stamp", {"mode": "direct"})
        seal_policy.set_enabled(False)


def test_audit_records_each_stamp_without_images(env, monkeypatch):
    from app.core import audit_db
    seen = []
    real = audit_db.log_event
    monkeypatch.setattr(audit_db, "log_event", lambda ev, **kw: (seen.append((ev, kw)), real(ev, **kw))[1])
    assert _api(env).status_code == 200
    ev = [kw for name, kw in seen if name == "ai_stamp"]
    assert ev, "要有 ai_stamp 稽核"
    d = ev[-1]["details"]
    assert d["token"] == "ai-stamp-token" and d["assets"][0]["id"] == env["seal"].id and d["src_sha256"] \
        and d["out_sha256"] and d["pages"] == [0]
    assert "png" not in json.dumps(d).lower(), "稽核不含章的圖"


def test_controlled_seal_never_listed_even_if_ai_flag_is_left_on(env):
    """設定檔裡同時是「受管制」又「AI 開著」（手動改過、舊資料）：AI 清單也不可以出現、也蓋不了。"""
    from app.core import seal_policy
    e = env
    with seal_policy._LOCK:
        data = seal_policy._read()
        data["enabled"] = True
        data["assets"][e["seal"].id]["mode"] = "approval"
        seal_policy._write(data)
    try:
        err, res = _call(e["c"], e["tok"], "jtdt_run", {"tool": "stamp_list"})
        assert not err and res["result"]["seals"] == [], res
        assert _api(e).status_code == 403
    finally:
        with seal_policy._LOCK:
            data = seal_policy._read()
            data["enabled"] = False
            data["assets"][e["seal"].id]["mode"] = "direct"
            seal_policy._write(data)
