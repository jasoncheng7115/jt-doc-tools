"""設定 → 名片簿：新增共用名片簿的對話框（真瀏覽器；客戶回報 2026-11-23）。

客戶：「搜尋出人名，但似乎沒有加入按鈕或自動選取加入的功能」—— 清單裡的人其實是按鈕，但看起來像一般文字，
點了之後加到對話框最下面（在可見範圍外），看起來像沒反應。這裡用真的滑鼠點擊驗：

* 每一列都看得到「加入」；點一下，那個人出現在「已加入的成員」、數字跟著變、而且在對話框的可見範圍內
* 組織單位（OU）可以從清單選（從目錄帳號的 DN 推出來），不用自己輸入 DN
* 搜尋框按 Enter 加入第一筆符合的
* 儲存後名片簿真的建立、成員都在；同名的名片簿跳出說明、不會建立
設 `JTDT_SHOT_DIR` 時另存截圖（發版前目視用）。
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
from tests.test_ai_stamp_pages_browser import _tools  # noqa: E402
from tests.test_doc_straighten_quad_overlay_e2e import _browser, _free_port  # noqa: E402
from tools.browser_probe import profile_arg as _profile_arg  # noqa: E402

pytestmark = pytest.mark.skipif(not _browser(), reason="沒有 chromium，跳過真瀏覽器測試")

_SETUP = r"""
import json, time
from app.core import auth_settings, auth_db, audit_db, sessions
auth_db.init(); audit_db.init()
pw = "TestAdmin1234"
auth_settings.enable_local_with_admin(admin_username="jtdt-admin", admin_display_name="admin",
    admin_password=pw, admin_password_confirm=pw, actor_ip="127.0.0.1")
c = auth_db.conn()
with c:
    for name, dn in (("amy", "CN=Amy Example,OU=Sales,OU=TW,DC=example,DC=com"),
                     ("bob", "CN=Bob Example,OU=TW,DC=example,DC=com")):
        c.execute("INSERT INTO users(username, display_name, source, external_dn, enabled, is_admin_seed, created_at)"
                  " VALUES (?,?,?,?,1,0,?)", (name, name.title(), "ad", dn, time.time()))
admin = c.execute("SELECT id FROM users WHERE username='jtdt-admin'").fetchone()["id"]
tok, _ = sessions.issue(admin, remember=False, ip="127.0.0.1", ua="pytest")
print(json.dumps({"admin": tok, "cookie": sessions.COOKIE_NAME}))
"""


@pytest.fixture(scope="module")
def site():
    data = tempfile.mkdtemp(prefix="bcadlg-")
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    out = subprocess.run([sys.executable, "-c", _SETUP], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    info = json.loads(out.stdout.strip().splitlines()[-1])
    port, cdp = _free_port(), _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                            "--port", str(port), "--log-level", "warning"],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen([_browser(), "--headless=new", "--no-sandbox", "--disable-gpu", _profile_arg(),
                           f"--remote-debugging-port={cdp}", "--window-size=1280,800", "about:blank"],
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
        send("Network.setCookie", {"name": info["cookie"], "value": info["admin"], "domain": "127.0.0.1", "path": "/"})
        yield port, send
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


def _click(send, ev, selector_js):
    """真的滑鼠點擊（不是 element.click()）：點在元素中心。"""
    box = ev(f"(() => {{ const e = {selector_js}; if (!e) return null; e.scrollIntoView({{block:'center'}});"
             " const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
    assert box, f"找不到 {selector_js}"
    for t in ("mousePressed", "mouseReleased"):
        send("Input.dispatchMouseEvent", {"type": t, "x": box[0], "y": box[1], "button": "left", "clickCount": 1})


def _row(text):
    return f"[...document.querySelectorAll('#bcaPick button')].find(b => b.textContent.includes({json.dumps(text)}))"


def _open(port, send, ev, wait):
    send("Page.addScriptToEvaluateOnNewDocument", {"source": "window.__errs = []; window.addEventListener('error', e => window.__errs.push(String(e.message)));"
                                                             "window.addEventListener('unhandledrejection', e => window.__errs.push('rej:' + String(e.reason)));"})
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/admin/business-cards"})
    assert wait("!!document.getElementById('bcaNew') && document.readyState === 'complete'")
    _click(send, ev, "document.getElementById('bcaNew')")
    assert wait("!document.getElementById('bcaDlg').hidden && document.querySelectorAll('#bcaPick button').length > 0"), \
        ev("JSON.stringify({url: location.href, title: document.title, dlg: document.getElementById('bcaDlg').hidden,"
           " pick: document.getElementById('bcaPick').innerHTML.slice(0, 300), errs: window.__errs, empty: document.getElementById('bcaEmpty').hidden})")


def test_picking_people_and_an_ou_then_saving(site):
    port, send = site
    ev, wait, shot = _tools(send)
    _open(port, send, ev, wait)
    shot("bc-admin-dialog-list")
    # 每一列都看得到「加入」
    assert ev("[...document.querySelectorAll('#bcaPick button')].every(b => !!b.querySelector('.bca-pk-add') && b.querySelector('.bca-pk-add').offsetWidth > 0)")
    assert ev("document.getElementById('bcaMemCount').textContent") == "0"
    assert ev("!document.getElementById('bcaMemEmpty').hidden")
    # 組織單位那一段：從 DN 推出來的兩層都在
    heads = ev("[...document.querySelectorAll('#bcaPick .bca-pick-h')].map(h => h.textContent)")
    assert heads[0].startswith("使用者（") and heads[1:] == ["群組（0）", "組織單位（2）"], heads
    ev("document.getElementById('bcaName').value = '業務部名片'")
    ev("document.getElementById('bcaSearch').value = 'amy'; document.getElementById('bcaSearch').dispatchEvent(new Event('input'))")
    _click(send, ev, _row("amy@ad"))
    assert wait("document.getElementById('bcaMemCount').textContent === '1'")
    # 加進來的那一列在對話框的可見範圍內（原本加到最下面、看不到，像沒反應）
    assert wait("""(() => { const li = document.querySelector('#bcaMembers li'); const card = document.querySelector('.bca-card');
        if (!li) return false; const a = li.getBoundingClientRect(), b = card.getBoundingClientRect();
        return a.top >= b.top && a.bottom <= b.bottom; })()""", 5)
    shot("bc-admin-dialog-added")
    # 組織單位用選的
    ev("document.getElementById('bcaSearch').value = 'Sales'; document.getElementById('bcaSearch').dispatchEvent(new Event('input'))")
    _click(send, ev, _row("Sales / TW（example.com）"))
    assert wait("document.getElementById('bcaMemCount').textContent === '2'")
    # 搜尋框按 Enter 加入第一筆
    ev("document.getElementById('bcaSearch').value = 'bob'; document.getElementById('bcaSearch').dispatchEvent(new Event('input'))")
    ev("document.getElementById('bcaSearch').focus()")
    send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    assert wait("document.getElementById('bcaMemCount').textContent === '3'")
    _click(send, ev, "document.getElementById('bcaSave')")
    assert wait("document.getElementById('bcaDlg').hidden")
    st = ev("fetch('/admin/business-cards/api/state').then(r => r.json()).then(s => s.shared.map(b => ({n: b.name, m: b.members.map(x => x.type + ':' + (x.name || x.key))})))")
    assert st == [{"n": "業務部名片", "m": sorted(st[0]["m"])}] or st[0]["n"] == "業務部名片", st
    assert sorted(st[0]["m"]) == sorted(["user:amy@ad", "user:bob@ad", "ou:ou=Sales,ou=TW,dc=example,dc=com"]), st


def test_a_duplicate_name_is_explained_and_not_created(site):
    port, send = site
    ev, wait, _shot = _tools(send)
    _open(port, send, ev, wait)
    ev("document.getElementById('bcaName').value = '業務部名片'")       # 上一支建的那本
    _click(send, ev, "document.getElementById('bcaSave')")
    assert wait("document.body.innerText.includes('已經有同名的共用名片簿')", 10), "同名要跳出說明"
    n = ev("fetch('/admin/business-cards/api/state').then(r => r.json()).then(s => s.shared.length)")
    assert n == 1
