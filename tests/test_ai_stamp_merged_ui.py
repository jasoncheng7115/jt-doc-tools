"""AI 用印確認併進「用印申請」、MCP 設定教學、通知主旨不重複（v1.17.56，使用者 2026-11-24 要求）。

驗的是：

* 舊網址 `/ai-stamp` 轉到 `/seal-request`（通知裡的 `#req=…` 由瀏覽器帶過去）；通知連結與提示指向新位置
* 用印申請頁三種情況：都沒開（只說尚未啟用）、只開 AI 用印（只有「AI 用印」分頁）、都開（多一個「AI 用印」分頁）
* 側欄只有「用印申請」一項（不再有獨立的「AI 用印確認」），件數是「待我審核＋待我用印＋AI 待確認」
* MCP 設定頁的「設定教學」逐項標完成 / 未完成，數字跟實際設定一致（開放 AI 的章、有設定對象的章、允許用印的 Token）
* 聊天類通知（Telegram、LINE、Slack…）內文第一行就是主旨時不再重複
* 確認頁的預覽有放大用的大圖（`size=large`）

資料全部杜撰。
"""
from __future__ import annotations

import io
import json


from tests.test_ai_stamp import _api, _set_ai, env  # noqa: F401  (fixture)


# --------------------------------------------------------------------------- 通知

def test_chat_channels_do_not_repeat_the_subject(monkeypatch):
    from app.core import notify_channels as nc
    assert nc._msg("AI 用印待你確認", "AI 用印待你確認\n章：發票章") == "AI 用印待你確認\n章：發票章"
    assert nc._msg("主旨", "內文第一行\n第二行") == "主旨\n內文第一行\n第二行", "第一行不同時照舊"
    assert nc._msg("主旨", "") == "主旨"
    sent = []
    monkeypatch.setattr(nc, "_post", lambda url, **kw: sent.append(kw))
    nc.send_slack({"slack_webhook": "https://hooks.example.com/x"}, "通知", "通知\n內容")
    nc.send_teams({"teams_webhook": "https://hooks.example.com/y"}, "通知", "通知\n內容")
    for kw in sent:
        assert json.dumps(kw, ensure_ascii=False).count("通知") == 1, kw


def test_ai_stamp_notification_points_to_the_seal_request_page(monkeypatch):
    from app.core import ai_stamp, notify_settings
    monkeypatch.setattr(notify_settings, "get", lambda: {"site_url": "https://doc.example.com/"})
    assert ai_stamp._link("a" * 32) == "https://doc.example.com/seal-request#req=" + "a" * 32
    monkeypatch.setattr(notify_settings, "get", lambda: {"site_url": ""})
    sent = {}
    import app.core.seal_notify as sn

    class _T:
        def __init__(self, target, args, **kw):
            sent["args"] = args

        def start(self):
            pass
    monkeypatch.setattr(ai_stamp.threading, "Thread", _T)
    monkeypatch.setattr(sn, "_send", lambda *a: None)
    ai_stamp._notify(1, {"id": "b" * 32, "assets": json.dumps([{"name": "發票章"}]), "token_label": "mcp",
                         "created_at": 1795000000})
    text = sent["args"][2]
    assert "「用印申請」→「AI 用印」" in text and "AI 用印確認」頁" not in text


# --------------------------------------------------------------------------- 頁面

def _user_client(e):
    from fastapi.testclient import TestClient

    from app import main as app_main
    from app.core import sessions
    tok, _ = sessions.issue(e["uid"], remember=False, ip="127.0.0.1", ua="pytest")
    c = TestClient(app_main.app)
    c.cookies.set(sessions.COOKIE_NAME, tok)
    return c


def test_old_address_redirects(env):  # noqa: F811
    c = _user_client(env)
    r = c.get("/ai-stamp", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/seal-request"


def test_ai_only_mode_shows_just_the_ai_tab_and_one_sidebar_item(env):  # noqa: F811
    from app.core import seal_policy
    e = env
    _set_ai(e, confirm=True, enabled=True, subjects=[{"type": "user", "key": str(e["uid"])}])
    assert _api(e).status_code == 202                        # 一件待確認
    assert not seal_policy.is_enabled()
    c = _user_client(e)
    html = c.get("/seal-request").text
    assert 'id="srAi"' in html and 'data-only="1"' in html, "只開 AI 用印：只有 AI 用印分頁"
    assert 'data-box="mine"' not in html and "用印申請尚未啟用" not in html
    assert 'href="/ai-stamp"' not in html, "側欄不再有獨立的 AI 用印確認"
    assert 'class="sb-item sb-seal-link" href="/seal-request#ai"' in html
    assert '<span class="sb-count" title="待我處理">1</span>' in html, "側欄件數含 AI 待確認"


def test_both_on_adds_an_ai_tab(env):  # noqa: F811
    from app.core import seal_policy
    e = env
    seal_policy.set_enabled(True)
    try:
        html = _user_client(e).get("/seal-request").text
        assert 'data-box="ai"' in html and 'id="srAi"' in html and 'data-only="1"' not in html
        assert 'data-box="mine"' in html, "原本的分頁都在"
        assert 'class="sb-item sb-seal-link" href="/seal-request"' in html
    finally:
        seal_policy.set_enabled(False)


def test_nothing_on_says_not_enabled(env):  # noqa: F811
    from app.mcp import settings as ms
    e = env
    cfg = ms.get()
    ms.save({**cfg, "allow_stamp": False})
    try:
        html = _user_client(e).get("/seal-request").text
        assert "用印申請尚未啟用" in html and 'id="srAi"' not in html
        assert "sb-seal-link" not in html, "兩個都沒開：側欄不出現"
    finally:
        ms.save({**cfg, "allow_stamp": True})


def test_large_preview_for_zooming(env):  # noqa: F811
    e = env
    _set_ai(e, confirm=True, enabled=True, subjects=[{"type": "user", "key": str(e["uid"])}])
    rid = _api(e).json()["request_id"]
    c = _user_client(e)
    from PIL import Image
    small = Image.open(io.BytesIO(c.get(f"/ai-stamp/api/{rid}/preview/0").content))
    large = Image.open(io.BytesIO(c.get(f"/ai-stamp/api/{rid}/preview/0?size=large").content))
    assert large.width > small.width * 1.5


# --------------------------------------------------------------------------- MCP 設定教學

def test_mcp_setup_guide_reflects_the_real_state(env, admin_session):  # noqa: F811
    from app.admin import mcp_routes
    from app.core import api_tokens as at
    e = env
    ss = mcp_routes._stamp_setup(at.api_tokens.list_tokens())
    assert ss["seals_ai"] == 1 and ss["seals_ai_with_subjects"] == 1 and ss["tokens_stamp"] == 1
    _set_ai(e, subjects=[])
    ss = mcp_routes._stamp_setup(at.api_tokens.list_tokens())
    assert ss["seals_ai"] == 1 and ss["seals_ai_with_subjects"] == 0, "開了 AI 但沒設對象不算完成"
    admin, _u, _p = admin_session
    html = admin.get("/admin/mcp").text
    assert 'id="mcp-stamp-guide-btn"' in html and 'id="mcp-stamp-guide"' in html
    guide = html[html.index('id="mcp-stamp-guide"'):html.index("</ol>", html.index('id="mcp-stamp-guide"'))]
    assert guide.count('class="todo"') == 1 and "開放 AI 1 顆，其中有設定對象的 0 顆" in guide


def test_seal_request_admin_page_labels_the_ai_subjects():
    from pathlib import Path
    t = (Path(__file__).resolve().parents[1] / "app/admin/templates/admin_seal_request.html").read_text(encoding="utf-8")
    assert "tr('AI 可以用的對象')" in t and "sr-ai-warn" in t and "目前沒有人能經 AI 使用這顆章" in t
    assert t.count("el('div', 'sr-who')") >= 2, "標籤與「設定」按鈕放同一列"
    assert ".sr-who { display:flex; align-items:center;" in t


def test_mcp_switch_columns_are_top_aligned():
    from pathlib import Path
    t = (Path(__file__).resolve().parents[1] / "app/admin/templates/admin_mcp.html").read_text(encoding="utf-8")
    assert ".mcp-token-sws { display: flex; gap: 12px; align-items: flex-start;" in t
    assert "line-height: 16px; height: 16px;" in t
