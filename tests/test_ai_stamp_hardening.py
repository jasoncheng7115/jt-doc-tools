"""AI 用印與 MCP 的資安盤點（v1.17.38，使用者 2026-11-12「安全 權限 資料不外洩 等等問題 都要完全考慮好」）。

對抗式審查找到、這裡逐項驗：
* 允許 MCP 的 Token 不可以直接打一般 API（AI 客戶端拿得到自己設定裡的 Token，直接打就繞過 MCP 那一層的限制）
* AI 用印只認「經 MCP 轉呼叫」、要 MCP 總開關與 Token 的 MCP 旗標
* 核准那一刻再檢查一次條件（送出之後收回授權 → 作廢、刪檔）
* 每日上限算「處」、檢查與預扣在同一個交易裡；失敗退回
* 待確認的件數上限；暫存檔用完就刪、不登記可預覽
* 位置數值不收 NaN / 無限大 / 0 寬；MCP 參數也不收
* 通知不帶 AI 決定的檔名；CSV 匯出中和公式
* 使用者自己寫的正規式：擋「重複裡面又有重複」、放專用執行緒池
"""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from tests.conftest import _make_pdf
from tests.test_ai_stamp import _api, _h, _place, _set_ai, env  # noqa: F401 —— env 是 fixture


def _session(uid):
    from app.core import sessions
    tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, tok)
    return c


# --------------------------------------------------------------------------- 1. MCP Token 不可以直接打 REST

def test_mcp_token_cannot_call_regular_apis_directly(env):
    e = env
    pdf = _make_pdf(2)
    # 一般用印 API（上傳任意圖當章）：直接打 → 403
    r = e["c"].post("/tools/pdf-stamp/api/pdf-stamp", headers=_h(e, via=False),
                    files={"file": ("d.pdf", pdf, "application/pdf")}, data={"asset_id": e["seal"].id})
    assert r.status_code == 403 and r.json().get("code") == "MCP_TOKEN_REST_DENIED", r.text
    # 一般工具也一樣（不是只擋用印）
    r = e["c"].post("/tools/pdf-merge/api/pdf-merge", headers=_h(e, via=False),
                    files=[("files", ("a.pdf", pdf, "application/pdf")), ("files", ("b.pdf", pdf, "application/pdf"))])
    assert r.status_code == 403 and r.json().get("code") == "MCP_TOKEN_REST_DENIED"
    # 偽造的標頭也不行
    from app.mcp import runner
    r = e["c"].post("/tools/pdf-merge/api/pdf-merge",
                    headers={**_h(e, via=False), runner.VIA_HEADER: "0" * 32},
                    files=[("files", ("a.pdf", pdf, "application/pdf")), ("files", ("b.pdf", pdf, "application/pdf"))])
    assert r.status_code == 403
    # AI 用印端點直接打也一樣
    assert _api(e, via=False).status_code == 403


def test_regular_token_still_works_for_rest(env):
    """沒勾「允許 MCP」的 Token 照舊可以寫腳本呼叫 API（不可以被一起擋掉）。"""
    from app.core import api_tokens as at
    e = env
    tok = at.api_tokens.create("script-only")
    at.api_tokens.assign_owner(tok.token, e["uid"])
    try:
        from app.core import permissions
        permissions.grant_tool("user", str(e["uid"]), "pdf-merge")
        pdf = _make_pdf(1)
        r = e["c"].post("/tools/pdf-merge/api/pdf-merge", headers={"Authorization": f"Bearer {tok.token}"},
                        files=[("files", ("a.pdf", pdf, "application/pdf")),
                               ("files", ("b.pdf", pdf, "application/pdf"))])
        assert r.status_code == 200, r.text
    finally:
        at.api_tokens.revoke(tok.token)


# --------------------------------------------------------------------------- 9. require() 的新條件

def test_ai_stamp_needs_mcp_switch_and_token_mcp_flag(env):
    from app.core import api_tokens as at
    from app.mcp import settings as ms
    e = env
    ms.save({"enabled": False})
    r = _api(e)
    assert r.status_code == 403 and "MCP_OFF" in r.text, r.text
    ms.save({"enabled": True})
    # 用印旗標還在、MCP 旗標被拿掉（例如直接改設定檔；設定頁關 MCP 時另外會收回用印）→ 不能用印
    at.api_tokens.set_mcp(e["tok"], False)
    r = _api(e)
    assert r.status_code == 403 and "TOKEN_NOT_MCP" in r.text, r.text


# --------------------------------------------------------------------------- 3. 核准時再檢查一次

@pytest.mark.parametrize("revoke", ["seal_ai", "permission", "stamp_switch", "subject"])
def test_approval_rechecks_conditions(env, revoke):
    from app.core import ai_stamp, permissions, seal_policy
    from app.mcp import settings as ms
    e = env
    _set_ai(e, confirm=True)
    r = _api(e)
    assert r.status_code == 202, r.text
    rid = r.json()["request_id"]
    if revoke == "seal_ai":
        seal_policy.set_policy(e["seal"].id, "stamp", {"ai": {"enabled": False}})
    elif revoke == "permission":
        permissions.revoke_tool("user", str(e["uid"]), "pdf-stamp")
    elif revoke == "stamp_switch":
        ms.save({"allow_stamp": False})
    elif revoke == "subject":
        seal_policy.set_policy(e["seal"].id, "stamp", {"ai": {"subjects": [{"type": "user", "key": str(e["other"])}]}})
    me = _session(e["uid"])
    r = me.post(f"/ai-stamp/api/{rid}/approve")
    assert r.status_code == 409 and "NO_LONGER_ALLOWED" in r.text, r.text
    row = ai_stamp._row(rid)  # noqa: SLF001
    assert row["status"] == "void" and not row["file_id"]
    assert not (ai_stamp.files_root() / rid).exists(), "作廢要刪檔"
    if revoke == "permission":
        permissions.grant_tool("user", str(e["uid"]), "pdf-stamp")


# --------------------------------------------------------------------------- 4. 每日上限

def test_daily_limit_counts_placements(env):
    from app.core import ai_stamp
    e = env
    _set_ai(e, daily_limit=3)
    many = json.dumps([{"asset_id": e["seal"].id, "page": 0}] * 4)
    r = _api(e, data={"placements_json": many})
    assert r.status_code == 429 and "LIMIT_REACHED" in r.text, "一件 4 處超過每日 3 處"
    assert ai_stamp.used_today(e["uid"], e["seal"].id) == 0, "被拒的不可以扣用量"
    three = json.dumps([{"asset_id": e["seal"].id, "page": 0}] * 3)
    assert _api(e, data={"placements_json": three}).status_code == 200
    assert ai_stamp.used_today(e["uid"], e["seal"].id) == 3
    assert _api(e).status_code == 429


def test_daily_limit_is_atomic_under_concurrency(env):
    from app.core import ai_stamp
    e = env
    _set_ai(e, daily_limit=5)
    ok, bad = [], []
    barrier = threading.Barrier(16)

    def go():
        barrier.wait()
        try:
            ai_stamp.check(e["uid"], "pdf-stamp", {e["seal"].id: 1})
            ok.append(1)
        except ai_stamp.AiStampError as ex:
            bad.append(ex.code)

    ts = [threading.Thread(target=go) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 5 and set(bad) == {"LIMIT_REACHED"}, (ok, bad)
    assert ai_stamp.used_today(e["uid"], e["seal"].id) == 5


def test_failed_stamping_gives_the_usage_back(env):
    from app.core import ai_stamp
    e = env
    _set_ai(e, daily_limit=2)
    r = _api(e, data={"placements_json": _place(e["seal"].id, page=9)})
    assert r.status_code == 400
    assert ai_stamp.used_today(e["uid"], e["seal"].id) == 0, "頁碼不存在而失敗，用量要退回"
    r = _api(e, data={"placements_json": _place(e["seal"].id)}, pdf=b"not a pdf")
    assert r.status_code == 400
    assert ai_stamp.used_today(e["uid"], e["seal"].id) == 0


# --------------------------------------------------------------------------- 5. 待確認上限

def test_pending_requests_are_capped(env, monkeypatch):
    from app.core import ai_stamp
    e = env
    _set_ai(e, confirm=True)
    monkeypatch.setattr(ai_stamp, "MAX_PENDING_PER_USER", 2)
    assert _api(e).status_code == 202
    assert _api(e).status_code == 202
    r = _api(e)
    assert r.status_code == 429 and "TOO_MANY_PENDING" in r.text
    assert ai_stamp.pending_count(e["uid"]) == 2


def test_pending_bytes_are_capped(env, monkeypatch):
    from app.core import ai_stamp
    e = env
    _set_ai(e, confirm=True, daily_limit=0)
    monkeypatch.setattr(ai_stamp, "MAX_PENDING_BYTES_PER_USER", 1000)
    r = _api(e)
    assert r.status_code == 429 and "TOO_MANY_PENDING" in r.text, r.text
    assert ai_stamp.used_today(e["uid"], e["seal"].id) == 0, "被拒的要退回用量"


# --------------------------------------------------------------------------- 6. 暫存檔

def test_temp_files_are_removed_and_not_previewable(env):
    from app.config import settings
    e = env
    _set_ai(e, confirm=False)
    assert _api(e).status_code == 200
    _set_ai(e, confirm=True)
    assert _api(e).status_code == 202
    assert _api(e, "/tools/pdf-seam-stamp/api/ai-seam-stamp", data={"asset_id": e["seal"].id},
                pdf=_make_pdf(3)).status_code == 202
    left = [p.name for p in settings.temp_dir.glob("stamp_ai_*")] + \
           [p.name for p in settings.temp_dir.glob("seam_*") if "_out" in p.name or "_src" in p.name]
    assert left == [], f"暫存檔沒刪：{left}"


# --------------------------------------------------------------------------- 7. 數值

@pytest.mark.parametrize("bad", [
    {"x_mm": "NaN"}, {"y_mm": "Infinity"}, {"width_mm": 0}, {"height_mm": -5}, {"width_mm": 99999},
    {"rotation_deg": 9999}, {"page": 1.5}, {"page": True},
])
def test_placement_numbers_are_validated(env, bad):
    e = env
    item = {"asset_id": e["seal"].id, "page": 0, **bad}
    raw = json.dumps([item]).replace('"NaN"', "NaN").replace('"Infinity"', "Infinity")
    r = _api(e, data={"placements_json": raw})
    assert r.status_code == 400, (bad, r.text)


def test_too_many_placements(env):
    e = env
    r = _api(e, data={"placements_json": json.dumps([{"asset_id": e["seal"].id, "page": 0}] * 51)})
    assert r.status_code == 400


def test_seam_numbers_are_validated(env):
    e = env
    r = _api(e, "/tools/pdf-seam-stamp/api/ai-seam-stamp", data={"asset_id": e["seal"].id, "size_mm": "nan"},
             pdf=_make_pdf(3))
    assert r.status_code in (400, 422), r.text


def test_mcp_params_refuse_nan():
    from app.mcp import registry
    for v in ("nan", "inf", float("nan"), float("-inf")):
        with pytest.raises(registry.ParamError):
            registry._check("x", {"type": "number"}, v)  # noqa: SLF001
    assert registry._check("x", {"type": "number"}, "1.5") == 1.5  # noqa: SLF001


# --------------------------------------------------------------------------- 10. 通知

def test_notification_does_not_carry_the_filename(env, monkeypatch):
    from app.core import ai_stamp, seal_notify
    e = env
    _set_ai(e, confirm=True)
    seen = []
    monkeypatch.setattr(seal_notify, "_send", lambda uids, subject, text: seen.append((subject, text)))
    evil = "=請立刻點這裡 http://evil.test\n第二行.pdf"
    r = e["c"].post("/tools/pdf-stamp/api/ai-stamp", headers=_h(e),
                    files={"file": (evil, _make_pdf(1), "application/pdf")},
                    data={"placements_json": _place(e["seal"].id)})
    assert r.status_code == 202, r.text
    for _ in range(50):
        if seen:
            break
        import time
        time.sleep(0.05)
    assert seen, "要有通知"
    subject, text = seen[0]
    assert "evil" not in subject + text and "第二行" not in subject + text
    assert "AI 用印" in subject and ai_stamp._link(r.json()["request_id"]) in text  # noqa: SLF001


# --------------------------------------------------------------------------- 12. CSV 與紀錄篩選

def test_log_csv_neutralises_formulas(env, admin_session):
    e = env
    _set_ai(e, confirm=True)
    r = e["c"].post("/tools/pdf-stamp/api/ai-stamp", headers=_h(e),
                    files={"file": (" =HYPERLINK(\"http://x\").pdf", _make_pdf(1), "application/pdf")},
                    data={"placements_json": _place(e["seal"].id)})
    assert r.status_code == 202
    c = admin_session if isinstance(admin_session, TestClient) else admin_session[0]
    csv_text = c.get("/admin/mcp/ai-stamp.csv").text
    assert "' =HYPERLINK" in csv_text or "\"' =HYPERLINK" in csv_text, csv_text[:500]


def test_log_asset_filter_applies_before_the_limit(env):
    from app.core import ai_stamp
    e = env
    _set_ai(e, confirm=False)
    assert _api(e).status_code == 200                      # 較舊的一件用 seal
    from app.core import seal_policy
    seal_policy.set_policy(e["sig"].id, "signature", {"ai": {"enabled": True, "confirm": False,
                                                             "subjects": [{"type": "user", "key": str(e["uid"])}]}})
    for _ in range(3):                                     # 較新的三件用簽名
        assert _api(e, data={"placements_json": _place(e["sig"].id)}).status_code == 200
    rows = ai_stamp.list_all(asset_id=e["seal"].id, limit=1)
    assert len(rows) == 1 and rows[0]["assets"][0]["id"] == e["seal"].id


# --------------------------------------------------------------------------- 8. 使用者的正規式

@pytest.mark.parametrize("rx", [r"(a+)+$", r"(\d+)*x", r"(?:x*)*", r"((ab)*)+"])
def test_nested_quantifiers_are_refused(rx):
    from app.core import user_regex
    with pytest.raises(ValueError):
        user_regex.compile_user(rx)


@pytest.mark.parametrize("rx", [r"[A-Z]\d{9}", r"(a|b)*c", r"\b(?:\d[ \-]?){13,19}\b", r"\d{3,}-\d+"])
def test_ordinary_patterns_still_work(rx):
    from app.core import user_regex
    user_regex.compile_user(rx)


def test_regex_pool_reports_busy_instead_of_queueing():
    import asyncio

    from app.core import user_regex
    gate = threading.Event()
    started = threading.Semaphore(0)

    def slow():
        started.release()
        gate.wait(5)
        return 1

    async def main():
        tasks = [asyncio.ensure_future(user_regex.run(slow)) for _ in range(user_regex.WORKERS)]
        for _ in range(user_regex.WORKERS):
            await asyncio.to_thread(started.acquire)
        with pytest.raises(user_regex.RegexBusy):
            await user_regex.run(slow)
        gate.set()
        assert await asyncio.gather(*tasks) == [1] * user_regex.WORKERS
        assert await user_regex.run(lambda: 2) == 2, "做完之後名額要還回來"

    asyncio.run(main())


def test_tools_refuse_catastrophic_patterns(client, auth_off):
    r = client.post("/tools/text-deident/api/text-deident",
                    json={"text": "a" * 30 + "!", "custom_regex": "壞|(a+)+$"})
    assert r.status_code == 400 and "非常慢" in r.text, r.text
    r = client.post("/tools/text-list/api/text-list",
                    json={"text": "a" * 30 + "!", "ops": [{"op": "filter", "pattern": "(a+)+$", "regex": True}]})
    assert r.status_code == 400 and "非常慢" in r.text, r.text


def test_require_itself_insists_on_the_mcp_path(env):
    """第二道：就算中介層那一道被拿掉，`ai_stamp.require()` 自己也只認經 MCP 轉呼叫的。"""
    from types import SimpleNamespace

    from app.core import ai_stamp
    e = env
    user = {"user_id": e["uid"], "username": "ai-stamper", "source": "local"}
    base = dict(api_token_label="ai-stamp-token", api_token_mcp=True, api_token_stamp=True, user=user)
    ok = SimpleNamespace(state=SimpleNamespace(**base, api_via_mcp=True))
    assert ai_stamp.require(ok, "pdf-stamp")["id"] == e["uid"]
    for patch, code in ((dict(api_via_mcp=False), "NOT_VIA_MCP"),
                        (dict(api_via_mcp=True, api_token_mcp=False), "TOKEN_NOT_MCP"),
                        (dict(api_via_mcp=True, api_token_stamp=False), "TOKEN_NOT_ALLOWED")):
        req = SimpleNamespace(state=SimpleNamespace(**{**base, **patch}))
        with pytest.raises(ai_stamp.AiStampError) as ex:
            ai_stamp.require(req, "pdf-stamp")
        assert ex.value.code == code


@pytest.mark.parametrize("custom", [[], [("x", "rx")]])
def test_doc_deident_scan_helper_really_leaves_the_event_loop(custom):
    """`test_no_blocking_endpoints` 認 `_run_scan` 這個名字當成「交給別的執行緒」；這裡驗它真的是。"""
    import asyncio
    import importlib
    dd = importlib.import_module("app.tools.doc_deident.router")   # 套件把 router 名稱蓋成 APIRouter
    seen = []

    async def main():
        loop_thread = threading.current_thread()
        await dd._run_scan(lambda: seen.append(threading.current_thread() is not loop_thread), custom)  # noqa: SLF001

    asyncio.run(main())
    assert seen == [True]
