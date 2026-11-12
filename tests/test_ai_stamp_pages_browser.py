"""AI 用印的三個畫面（真瀏覽器）：設定 → 用印申請的「AI 使用」欄、設定 → MCP 的兩個開關、本人的「AI 用印確認」頁。

API 與條件在 `test_ai_stamp.py`；這裡驗畫面接線：設定值有畫出來、確認頁真的載出待確認的那一件與預覽、
按核准（經過確認視窗）之後真的核准、手機寬度不會橫向捲動、側欄有「AI 用印確認」與件數。
設 `JTDT_SHOT_DIR` 時另存截圖（發版前目視用）。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_doc_straighten_quad_overlay_e2e import _browser, _free_port  # noqa: E402
from tools.browser_probe import profile_arg as _profile_arg  # noqa: E402

CHROME = "/usr/bin/chromium-browser"
pytestmark = pytest.mark.skipif(not Path(CHROME).exists(), reason="沒有 chromium，跳過真瀏覽器測試")

_SETUP = r"""
import io, json
from PIL import Image
from app.core import auth_settings, auth_db, audit_db, sessions, permissions, user_manager, seal_policy, workspace
from app.core import api_tokens as at
from app.mcp import settings as ms
auth_db.init(); audit_db.init()
pw = "TestAdmin1234"
auth_settings.enable_local_with_admin(admin_username="jtdt-admin", admin_display_name="admin",
    admin_password=pw, admin_password_confirm=pw, actor_ip="127.0.0.1")
admin = auth_db.conn().execute("SELECT id FROM users WHERE username='jtdt-admin'").fetchone()["id"]
uid = user_manager.create_local("stamper", "stamper", "UserPass1234")
permissions.grant_tool("user", str(uid), "pdf-stamp")
ms.save({"enabled": True, "allow_stamp": True})
workspace.save_settings({"enabled": True})
tok = at.api_tokens.create("claude-code")
at.api_tokens.set_mcp(tok.token, True); at.api_tokens.assign_owner(tok.token, uid); at.api_tokens.set_stamp(tok.token, True)
from app.core.asset_manager import asset_manager
buf = io.BytesIO(); Image.new("RGBA", (100, 100), (200, 20, 20, 255)).save(buf, "PNG")
a = asset_manager.create_from_bytes("公司大小章", "stamp", buf.getvalue())
seal_policy.set_policy(a.id, "stamp", {"ai": {"enabled": True, "subjects": [{"type": "user", "key": str(uid)}],
                                              "confirm": True, "daily_limit": 5}})
at_, _ = sessions.issue(admin, remember=False, ip="127.0.0.1", ua="pytest")
ut_, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
# AI 送一件需要確認的用印：AI 用印只認 MCP 在行程內轉呼叫（帶程序內密值），所以在這個行程裡經同一條路送
import fitz
from fastapi.testclient import TestClient
from app import main as app_main
from app.mcp import runner
d = fitz.open()
for i in range(2):
    d.new_page().insert_text((72, 72), f"Contract page {i + 1}")
with TestClient(app_main.app) as c:
    r = c.post("/tools/pdf-stamp/api/ai-stamp",
               headers={"Authorization": f"Bearer {tok.token}", runner.VIA_HEADER: runner.VIA_SECRET},
               files={"file": ("合約.pdf", d.tobytes(), "application/pdf")},
               data={"placements_json": json.dumps([{"asset_id": a.id, "page": 1}])})
    assert r.status_code == 202, r.text
print(json.dumps({"admin": at_, "user": ut_, "cookie": sessions.COOKIE_NAME, "asset": a.id, "token": tok.token,
                  "request_id": r.json()["request_id"]}))
"""

_STATUS = r"""
import json, sys
from app.core import ai_stamp
r = ai_stamp._row(sys.argv[1])
print(json.dumps({"status": r["status"], "file_id": r["file_id"]}))
"""



@pytest.fixture(scope="module")
def site():
    br_path = _browser()
    if not br_path:
        pytest.skip("沒有 chromium")
    data = tempfile.mkdtemp(prefix="aistamp-")
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    out = subprocess.run([sys.executable, "-c", _SETUP], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    info = json.loads(out.stdout.strip().splitlines()[-1])
    port, cdp = _free_port(), _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                            "--port", str(port), "--log-level", "warning"],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen([br_path, "--headless=new", "--no-sandbox", "--disable-gpu", _profile_arg(),
                           f"--remote-debugging-port={cdp}", "--window-size=1280,900", "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        for _ in range(160):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
                urllib.request.urlopen(f"http://127.0.0.1:{cdp}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.skip("實例或瀏覽器起不來")
        import websockets.sync.client as wsc
        req = urllib.request.Request(f"http://127.0.0.1:{cdp}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=10) as resp:
            tab = json.loads(resp.read())
        ws = wsc.connect(tab["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
        n = [0]

        def send(method, params=None):
            n[0] += 1
            i = n[0]
            ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
            while True:
                m = json.loads(ws.recv(timeout=120))
                if m.get("id") == i:
                    return m

        send("Network.enable"); send("Page.enable"); send("Runtime.enable")
        info["env"] = env
        yield port, send, info
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        br.terminate(); srv.terminate()
        for p in (br, srv):
            try:
                p.wait(timeout=6)
            except Exception:
                p.kill()
        shutil.rmtree(data, ignore_errors=True)


def _tools(send):
    def ev(expr):
        r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        res = r.get("result") or {}
        assert not res.get("exceptionDetails"), res.get("exceptionDetails")
        return (res.get("result") or {}).get("value")

    def wait(expr, t=30):
        for _ in range(int(t / 0.25)):
            if ev(expr):
                return True
            time.sleep(0.25)
        return False

    def shot(name, width=1280):
        d = os.environ.get("JTDT_SHOT_DIR")
        if not d:
            return
        send("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1,
                                                    "mobile": width < 600})
        time.sleep(0.4)
        r = send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        Path(d, f"{name}-{width}.png").write_bytes(base64.b64decode(r["result"]["data"]))
        send("Emulation.clearDeviceMetricsOverride")

    return ev, wait, shot


def _login(send, info, who):
    send("Network.setCookie", {"name": info["cookie"], "value": info[who], "domain": "127.0.0.1", "path": "/"})


def test_admin_pages_show_the_ai_settings(site):
    port, send, info = site
    ev, wait, shot = _tools(send)
    _login(send, info, "admin")
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/admin/seal-request"})
    assert wait("document.querySelectorAll('#srRows tr .sr-ai-cell').length > 0"), "沒有 AI 使用欄"
    assert ev("document.querySelector('.sr-ai-cell input[type=checkbox]').checked") is True
    assert ev("document.querySelector('.sr-ai-cell').textContent.includes('stamper')")
    assert ev("document.querySelector('.sr-ai-limit').value") == "5"
    shot("admin-seal-ai")
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/admin/mcp"})
    assert wait("!!document.getElementById('mcp-stamp')")
    assert ev("document.getElementById('mcp-stamp').checked") is True
    row = "[...document.querySelectorAll('.mcp-token')].find(r => r.textContent.includes('claude-code'))"
    assert ev(row + ".querySelector('.mcp-token-stamp').checked") is True
    # 站台內建的那張 Token 沒綁使用者：「用印」開關停用
    assert ev("[...document.querySelectorAll('.mcp-token')].find(r => r.textContent.includes('built-in'))"
              ".querySelector('.mcp-token-stamp').disabled") is True
    assert ev("!!document.querySelector('a[href=\"/admin/mcp/ai-stamp\"]')")
    shot("admin-mcp")
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/admin/mcp/ai-stamp"})
    assert wait("document.querySelectorAll('#ailRows tr').length === 1"), "紀錄頁沒有那一件"
    assert ev("document.querySelector('#ailRows .ail-st').classList.contains('s-pending')")
    shot("admin-ai-log")


def test_owner_confirms_on_the_web(site):
    port, send, info = site
    ev, wait, shot = _tools(send)
    _login(send, info, "user")
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/ai-stamp"})
    assert wait("document.querySelectorAll('.as-card').length === 1"), "確認頁沒有那一件"
    assert ev("document.querySelector('.sb-ai-stamp-link .sb-count').textContent.trim()") == "1", "側欄要有件數"
    assert wait("[...document.querySelectorAll('.as-pages img')].every(i => i.complete && i.naturalWidth > 0)"), \
        "預覽圖沒有載出來"
    assert ev("document.querySelector('.as-card').textContent.includes('公司大小章')")
    shot("ai-stamp")
    shot("ai-stamp", 390)
    send("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 800, "deviceScaleFactor": 1, "mobile": True})
    time.sleep(0.4)
    assert ev("document.documentElement.scrollWidth") <= 390, "手機寬度不可以橫向捲動"
    send("Emulation.clearDeviceMetricsOverride")
    ev("document.querySelector('.as-actions .btn-primary').click()")
    assert wait("!!document.querySelector('.modal-ok')"), "要先問一聲"
    ev("document.querySelector('.modal-ok').click()")
    assert wait("!document.getElementById('asEmpty').hidden"), "核准後清單要清空"
    out = subprocess.run([sys.executable, "-c", _STATUS, info["request_id"]], cwd=ROOT, env=info["env"],
                         capture_output=True, text=True, timeout=60)
    st = json.loads(out.stdout.strip().splitlines()[-1])
    assert st["status"] == "approved" and st["file_id"]
