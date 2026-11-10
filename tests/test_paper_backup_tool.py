"""加密紙本備份：伺服器端只有頁面與不含秘密的稽核（建立 / 還原都在瀏覽器裡）。

* 頁面載得到模組、importmap 指到內建檔案、文字都走 tr()（給模組的 JSON）。
* 稽核端點只收白名單欄位；多送任何欄位（例如內容、標題、分片）一律 400。
* 相機只對這個工具開（Permissions-Policy），其他頁維持關閉。
* 預設不在任何一般角色裡（只有管理員能用）。
* 伺服器端刻意沒有收明文或分片的端點。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOOD = {"action": "create", "backup_id": "0123456789abcdef", "t": 2, "n": 3, "mode": "paper", "density": "medium", "blocks": 3}


def test_page_renders_with_module_and_importmap(client, auth_off):
    r = client.get("/tools/paper-backup/")
    assert r.status_code == 200
    html = r.text
    assert "加密紙本備份" in html
    assert '/static/js/paper_backup/pb_page.mjs' in html and 'type="module"' in html
    from tools.source_text import block_re
    scripts = {m.group(0)[:80]: m for m in block_re("script").finditer(html)}
    imap = next(v for k, v in scripts.items() if 'type="importmap"' in k)
    body = lambda m: m.group(1)
    assert json.loads(body(imap))["imports"]["shamir-secret-sharing/csprng"] == \
        "/static/vendor/shamir-secret-sharing-0.0.3/csprng.js"
    data = next(v for k, v in scripts.items() if 'id="pbText18n"' in k)
    texts = json.loads(body(data))
    assert {"L", "M"} <= set(texts) and texts["L"]["blockLabel"] and isinstance(texts["L"]["mainHelp"], list)


def test_static_modules_are_served(client, auth_off):
    for p in ("/static/js/paper_backup/pb_page.mjs", "/static/js/paper_backup/pb_core.mjs",
              "/static/vendor/shamir-secret-sharing-0.0.3/index.js", "/static/vendor/qrcode-generator-2.0.4/qrcode.mjs"):
        r = client.get(p)
        assert r.status_code == 200 and "javascript" in r.headers.get("content-type", ""), p


def test_camera_only_on_this_tool(client, auth_off):
    assert "camera=(self)" in client.get("/tools/paper-backup/").headers["permissions-policy"]
    assert "camera=()" in client.get("/tools/text-diff/").headers["permissions-policy"]
    assert "camera=()" in client.get("/").headers["permissions-policy"]


def test_audit_accepts_only_non_secret_fields(client, auth_off, monkeypatch):
    from app.core import audit_db
    seen = []
    monkeypatch.setattr(audit_db, "log_event", lambda ev, **kw: seen.append((ev, kw)))
    assert client.post("/tools/paper-backup/audit", json=GOOD).status_code == 200
    ev, kw = seen[-1]
    assert ev == "paper_backup_create" and kw["target"] == GOOD["backup_id"]
    assert kw["details"] == {"backup_id": GOOD["backup_id"], "t": 2, "n": 3, "mode": "paper", "density": "medium", "blocks": 3}
    r = client.post("/tools/paper-backup/audit", json={"action": "restore", "backup_id": GOOD["backup_id"], "t": 2, "n": 3, "ok": False})
    assert r.status_code == 200 and seen[-1][0] == "paper_backup_restore" and seen[-1][1]["details"]["ok"] is False
    n = len(seen)
    for extra in ({"content": "x"}, {"label": "x"}, {"share": "x"}, {"name": "a.txt"}):
        assert client.post("/tools/paper-backup/audit", json={**GOOD, **extra}).status_code == 400, extra
    for bad in ({"backup_id": "../../etc"}, {"backup_id": "0123456789ABCDEF"}, {"t": 1}, {"t": 4, "n": 3}, {"n": 17},
                {"action": "delete"}, {"mode": "cloud"}, {"mode": "file"}, {"density": "max"}, {"blocks": 0}, {"blocks": 5000}, {"t": "2"}):
        assert client.post("/tools/paper-backup/audit", json={**GOOD, **bad}).status_code == 400, bad
    assert client.post("/tools/paper-backup/audit", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/tools/paper-backup/audit", json=[1, 2]).status_code == 400
    assert len(seen) == n                                           # 拒絕的一筆都沒記


def test_not_in_any_default_role():
    from app.core.roles import SEED_ROLES, _NON_ADMIN_TOOL_IDS
    assert "paper-backup" not in _NON_ADMIN_TOOL_IDS
    for role in SEED_ROLES:
        assert "paper-backup" not in role["tools"], role["id"]


def test_server_has_no_endpoint_that_takes_secrets():
    from app.tools.paper_backup.router import router
    paths = sorted((r.path, tuple(sorted(r.methods))) for r in router.routes)
    assert paths == [("/", ("GET",)), ("/audit", ("POST",))]
    src = (ROOT / "app" / "tools" / "paper_backup" / "router.py").read_text(encoding="utf-8")
    assert "UploadFile" not in src and "File(" not in src


def test_page_module_has_no_hardcoded_ui_text():
    # 介面文字都從樣板（tr）來；模組裡出現中文代表漏翻
    src = (ROOT / "static" / "js" / "paper_backup" / "pb_page.mjs").read_text(encoding="utf-8")
    code = "\n".join(line.split("//", 1)[0] for line in src.splitlines())
    assert not re.search(r"['\"`][^'\"`\n]*[\u4e00-\u9fff][^'\"`\n]*['\"`]", code)


def test_never_exposed_to_ai_via_mcp():
    """S17：秘密不經過 AI 通道 —— MCP 歸類為不開放，也不在開放的工具清單裡。"""
    from app.mcp import registry
    assert registry.CLASSIFIED["paper-backup"] == "excluded"
    assert all(t.tool_id != "paper-backup" for t in registry.TOOLS)
