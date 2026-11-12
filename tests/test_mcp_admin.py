"""設定 → MCP 管理頁（v1.17.1）：存設定、逐張允許 Token、寫稽核、一般使用者不能改、客戶端設定範例。"""
from __future__ import annotations

import pytest


@pytest.fixture
def reset_mcp():
    from app.core import notify_settings
    from app.mcp import settings as ms
    yield
    ms.save({"enabled": False, "tools": None})
    notify_settings.save({"site_url": ""})


def test_page_shows_tools_tokens_and_snippets(client, auth_off, reset_mcp):
    from app.core import api_tokens as at
    from app.mcp import registry
    tok = at.api_tokens.create("ai-agent")
    try:
        html = client.get("/admin/mcp").text
        import re
        text = re.sub(r"<[^>]+>", "", html)       # 範例有語法顏色（span），比對去掉標記後的文字
        for t in registry.TOOLS:
            # AI 用印的工具不在逐支開放的卡片裡（看「允許 AI 用印」與 Token 的「用印」開關）
            assert (f'value="{t.name}"' in html) != t.stamp, t.name
        assert "ai-agent" in html and tok.token not in html, "只顯示遮罩，不顯示 Token 本身"
        for client_name in ("claude mcp add --transport http", "bearer_token_env_var", '"type": "remote"',
                            "mcp-remote", "OpenCode"):
            assert client_name in text, client_name
        assert "/admin/mcp" in client.get("/").text, "設定選單要有 MCP"
    finally:
        at.api_tokens.revoke(tok.token)


def test_save_and_token_toggle_are_audited(client, auth_off, reset_mcp, monkeypatch):
    from app.core import api_tokens as at, audit_db, notify_settings
    from app.mcp import settings as ms
    seen = []
    monkeypatch.setattr(audit_db, "log_event", lambda ev, **kw: seen.append((ev, kw)))
    r = client.post("/admin/mcp/save", json={"enabled": True, "site_url": "https://docs.example.test",
                                             "tools": ["pdf_merge", "nope"], "rate_per_minute": 9999,
                                             "max_concurrent": 3})
    assert r.status_code == 200, r.text
    cfg = ms.get()
    assert cfg["enabled"] and cfg["tools"] == ["pdf_merge"] and cfg["rate_per_minute"] == 600
    assert notify_settings.get()["site_url"] == "https://docs.example.test"
    assert seen[-1][1]["target"] == "mcp"
    assert client.post("/admin/mcp/save", json={"site_url": "javascript:alert(1)"}).status_code == 400
    tok = at.api_tokens.create("agent")
    try:
        preview = next(t["token_preview"] for t in at.api_tokens.list_tokens() if t["label"] == "agent")
        assert client.post("/admin/mcp/token", json={"preview": preview, "allowed": True}).json()["mcp"]
        assert at.api_tokens.lookup(tok.token)["mcp"] is True
        assert seen[-1][1]["details"] == {"token": "agent", "mcp": True}
        assert tok.token not in str(seen), "稽核不記 Token"
        assert client.post("/admin/mcp/token", json={"preview": "nope", "allowed": True}).status_code == 404
    finally:
        at.api_tokens.revoke(tok.token)


def test_regular_users_cannot_change_it(admin_session):
    from tests.test_authz_boundaries import _user_client
    _, c = _user_client("alice_mcp")
    r = c.post("/admin/mcp/save", json={"enabled": True}, follow_redirects=False)
    assert r.status_code in (401, 403, 302, 303), r.status_code
    from app.mcp import settings as ms
    assert not ms.get()["enabled"]


@pytest.mark.parametrize("loc", ["zh-TW", "en", "ja"])
def test_client_product_names_are_not_translated(client, auth_off, reset_mcp, loc):
    """Claude Desktop、Claude Code、Codex CLI、OpenCode 是產品名稱，三種介面語言都照原文。"""
    import re
    client.cookies.set("jtdt_locale", loc)
    try:
        text = re.sub(r"<[^>]+>", "", client.get("/admin/mcp").text)
    finally:
        client.cookies.delete("jtdt_locale")
    for name in ("Claude Desktop", "Claude Code", "Codex CLI", "OpenCode"):
        assert name in text, (loc, name)
    for wrong in ("桌面版", "デスクトップ版", "Opencode", "opencode CLI"):
        assert wrong not in text, (loc, wrong)


def test_translations_keep_the_product_names():
    """原文寫了 Claude Desktop / Claude Code / Codex / OpenCode 的句子，英文、日文譯文也要原樣保留（不可以改寫成別的叫法）。"""
    import json
    from pathlib import Path
    names = ("Claude Desktop", "Claude Code", "Codex", "OpenCode")
    bad = []
    for loc in ("en", "ja"):
        cat = json.loads((Path(__file__).resolve().parent.parent / f"app/i18n/{loc}.json").read_text(encoding="utf-8"))
        for zh, tr_ in cat.items():
            for n in names:
                if n in zh and n not in tr_:
                    bad.append(f"{loc}: {zh!r} → {tr_!r}")
    assert not bad, "\n".join(bad)


def test_heading_matches_the_other_settings_pages(client, auth_off, reset_mcp):
    """標題跟「LLM 設定」同一種寫法：圖示＋標題＋「附加功能」標籤，說明是標題下面獨立的一段。
    標籤樣式在全站的 platform.css（不寫在個別頁面裡，兩頁才不會一邊有改一邊沒改）。"""
    import re
    from pathlib import Path
    html = client.get("/admin/mcp").text
    h1 = re.search(r"<h1>(.*?)</h1>\s*<p class=\"muted\">", html, re.S)
    assert h1, "h1 後面直接接一段 muted 說明"
    assert 'class="addon-badge"' in h1.group(1) and "<svg" in h1.group(1)
    assert 'class="addon-badge"' in client.get("/admin/llm-settings").text
    root = Path(__file__).resolve().parent.parent
    assert ".addon-badge" in (root / "static/css/platform.css").read_text(encoding="utf-8")
    for tpl in ("app/admin/templates/admin_mcp.html", "app/admin/templates/llm_settings.html"):
        assert ".addon-badge {" not in (root / tpl).read_text(encoding="utf-8"), tpl


def test_settings_are_tiles_with_the_switch_next_to_its_title(client, auth_off, reset_mcp):
    """開關跟自己的標題放在同一張卡裡（原本一列橫跨整頁，開關離標題一千多像素）。"""
    import re
    html = client.get("/admin/mcp").text
    for sid in ("mcp-enabled", "mcp-bindip", "mcp-http"):
        assert re.search(r'<div class="mcp-tile(?: mcp-tile-wide)?">\s*<div class="mcp-tile-top">(?:(?!class="mcp-tile[ "]).)*id="' + sid + '"', html, re.S), sid


def test_site_url_card_says_it_is_automatic_and_lists_the_limits(client, auth_off, reset_mcp):
    """使用者 2026-10-21：「不填就是下面那兩個網址嗎？檔案上限多少？」「不填可以自動抓進來」。"""
    import re
    from app.core import workspace as ws
    html = client.get("/admin/mcp").text
    assert 'id="mcp-site-state">自動<' in html, "沒填不是警告，是自動"
    assert "自動用 AI 連進來的網址" in html and "<code>X-Forwarded-Proto</code>" in html and 'id="mcp-prev-note"' in html
    box = re.search(r'<div class="mcp-limits" id="mcp-limits">(.*?)</div>', html, re.S).group(1)
    s = ws.get_settings()
    assert f"單檔 {int(s['max_file_mb'])} MB" in box and "256 KB 以下" in box and "15 分鐘" in box
    assert f"共 {int(s['per_user_quota_mb'])} MB" in box
    ws.save_settings({"max_file_mb": 0})
    try:
        assert "單檔不限" in client.get("/admin/mcp").text, "上限跟著工作區設定走"
    finally:
        ws.save_settings({"max_file_mb": s["max_file_mb"]})


def test_page_has_no_private_ip_examples(client, auth_off, reset_mcp):
    """範例網址用主機名稱：頁面上出現私有 IP，ZAP 會報「Private IP Disclosure」（v1.17.4 踩過）。"""
    import re
    for loc in ("zh-TW", "en", "ja"):
        client.cookies.set("jtdt_locale", loc)
        html = client.get("/admin/mcp").text
        client.cookies.delete("jtdt_locale")
        hits = re.findall(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}\b", html)
        assert not [h for h in hits if not h.startswith("127.")], (loc, hits)
