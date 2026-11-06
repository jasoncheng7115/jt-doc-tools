"""設定 → 用印申請（真瀏覽器）：啟用、把章改成管制、用印工具看不到、還有管制的章時不能停用。

API 層在 `test_seal_policy.py`；這裡驗的是畫面接線（按下去真的打到 API、畫面跟著變）。
"""
from __future__ import annotations

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
import io, json, sys
from PIL import Image
from app.core import auth_settings, auth_db, audit_db, sessions
auth_db.init(); audit_db.init()
pw = "TestAdmin1234"
auth_settings.enable_local_with_admin(admin_username="jtdt-admin", admin_display_name="admin",
    admin_password=pw, admin_password_confirm=pw, actor_ip="127.0.0.1")
uid = auth_db.conn().execute("SELECT id FROM users WHERE username='jtdt-admin'").fetchone()["id"]
tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
from app.core.asset_manager import asset_manager
buf = io.BytesIO(); Image.new("RGBA", (100, 100), (200, 20, 20, 255)).save(buf, "PNG")
a = asset_manager.create_from_bytes("browser-seal", "stamp", buf.getvalue())
print(json.dumps({"token": tok, "cookie": sessions.COOKIE_NAME, "asset": a.id}))
"""


@pytest.fixture(scope="module")
def site():
    br_path = _browser()
    if not br_path:
        pytest.skip("沒有 chromium")
    data = tempfile.mkdtemp(prefix="sealadm-")
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    out = subprocess.run([sys.executable, "-c", _SETUP], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    info = json.loads(out.stdout.strip().splitlines()[-1])
    port, cdp = _free_port(), _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                            "--port", str(port), "--log-level", "warning"],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen([br_path, "--headless=new", "--no-sandbox", "--disable-gpu", _profile_arg(),
                           f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", "about:blank"],
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
        with urllib.request.urlopen(req, timeout=10) as r:
            tab = json.loads(r.read())
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

        send("Network.enable")
        send("Network.setCookie", {"name": info["cookie"], "value": info["token"], "domain": "127.0.0.1",
                                   "path": "/"})
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


def test_enable_restrict_and_cannot_disable(site):
    port, send, info = site
    aid = info["asset"]

    def ev(expr):
        r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        res = r.get("result") or {}
        assert not res.get("exceptionDetails"), res.get("exceptionDetails")
        return (res.get("result") or {}).get("value")

    def wait(expr, t=20):
        for _ in range(int(t / 0.25)):
            if ev(expr):
                return True
            time.sleep(0.25)
        return False

    send("Page.enable"); send("Runtime.enable")
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/admin/seal-request"})
    assert wait("document.querySelectorAll('#srRows tr').length > 0"), "清單沒有載出來"
    assert ev("document.getElementById('srEnabled').checked") is False, "預設不啟用"
    assert ev("document.querySelector('#srRows select').disabled") is True, "未啟用時不能改"

    ev("document.getElementById('srEnabled').click()")
    assert wait("document.getElementById('srEnabled').checked && !document.querySelector('#srRows select').disabled"), \
        "啟用後清單沒有變成可以改"

    ev("""(() => { const s = document.querySelector('#srRows select'); s.value = 'approval';
                   s.dispatchEvent(new Event('change', {bubbles: true})); })()""")
    assert wait("document.querySelector('#srRows select').value === 'approval' && !document.querySelector('#srRows select').disabled")

    # 用印工具頁看不到這顆章
    with urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{port}/tools/pdf-stamp/",
            headers={"Cookie": f"{info['cookie']}={info['token']}"}), timeout=30) as r:
        assert aid not in r.read().decode("utf-8"), "用印工具還列出受管制的章"

    # 停用被擋：開關回到勾選、訊息講出要先改回直接使用
    ev("document.getElementById('srEnabled').click()")
    assert wait("document.getElementById('srMsg').textContent.includes('browser-seal')"), "停用被擋的訊息沒出來"
    assert ev("document.getElementById('srEnabled').checked") is True, "停用被擋之後開關要回到啟用"
    assert "browser-seal" in ev("document.getElementById('srMsg').textContent"), "訊息要列出是哪一顆章"

    # 保管人設定視窗打得開
    ev("document.querySelectorAll('#srRows td button.btn')[0].click()")   # 第一顆 button 是本站下拉的按鈕
    assert wait("!document.getElementById('srPicker').hidden && document.querySelectorAll('#srUsers input').length > 0")


def test_site_dropdown_type_to_search(site):
    """本站下拉的「隨打即找」（`<select data-search>`）：打字只留下符合的、Enter 選唯一的一個、
    沒帶 data-search 的下拉不受影響（v1.17.32；模擬申請的申請者用這個）。"""
    port, send, _info = site

    def ev(expr):
        r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        res = r.get("result") or {}
        assert not res.get("exceptionDetails"), res.get("exceptionDetails")
        return (res.get("result") or {}).get("value")

    def wait(expr, t=20):
        for _ in range(int(t / 0.25)):
            if ev(expr):
                return True
            time.sleep(0.25)
        return False

    send("Page.enable"); send("Runtime.enable")
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/admin/seal-request"})
    assert wait("!!window.JtSelect && document.querySelectorAll('#srRows tr').length > 0")
    ev("""(() => {
      const mk = (search) => { const s = document.createElement('select'); s.className = 'jt-select';
        ['alice@local', 'bob@ldap', 'carol@ldap', 'sylvia@ldap'].forEach((t, i) => {
          const o = document.createElement('option'); o.value = String(i + 1); o.textContent = t; s.append(o); });
        if (search) s.setAttribute('data-search', '搜尋帳號');
        document.body.append(s); new window.JtSelect(s); return s; };
      window.__a = mk(true); window.__b = mk(false); return true; })()""")
    assert ev("!!window.__a._jtSelect.searchInput") and ev("!window.__b._jtSelect.searchInput"), \
        "只有帶 data-search 的下拉才有搜尋框"
    ev("window.__a._jtSelect.trigger.click()")
    assert wait("document.activeElement === window.__a._jtSelect.searchInput"), "打開時游標要在搜尋框"
    ev("""(() => { const i = window.__a._jtSelect.searchInput; i.value = 'SYL';
      i.dispatchEvent(new Event('input')); return true; })()""")
    vis = ev("window.__a._jtSelect._items.filter(e => !e.hidden).map(e => e.textContent)")
    assert vis == ["sylvia@ldap"], vis
    ev("""window.__a._jtSelect.searchInput.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}))""")
    assert ev("window.__a.value") == "4", "Enter 要選唯一符合的那一個"
    ev("window.__a._jtSelect.trigger.click()")
    assert ev("window.__a._jtSelect._items.every(e => !e.hidden)"), "重新打開要清掉上次的搜尋"
    ev("""(() => { const i = window.__a._jtSelect.searchInput; i.value = 'zzz'; i.dispatchEvent(new Event('input'));
      return true; })()""")
    assert ev("!window.__a._jtSelect._emptyEl.hidden"), "沒有符合的要顯示提示"
