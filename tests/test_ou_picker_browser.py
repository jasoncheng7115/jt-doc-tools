"""共用事務機與用印申請的選人清單：組織單位（OU）用選的，而且已經設定的 OU 儲存時不會被拿掉（真瀏覽器；客戶回報 2026-11-23）。

這兩頁的選人清單原本只有「使用者」「群組」兩欄，儲存時照勾選的重組整份清單 —— 經 API 設定的 OU
（或清單裡沒有的 OU）開視窗再按儲存就**無聲地被拿掉**。現在多一欄「組織單位」：

* 從目錄帳號的 DN 推出來的 OU 可以勾選
* 已經設定、但清單裡沒有的 OU 也列出來、維持勾選，儲存後還在
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

LEGACY = "ou=Legacy,dc=example,dc=com"
SALES = "ou=Sales,ou=TW,dc=example,dc=com"

_SETUP = r"""
import io, json, time
from PIL import Image
from app.core import auth_settings, auth_db, audit_db, sessions, mfp_store, seal_policy
from app.core.asset_manager import asset_manager
auth_db.init(); audit_db.init()
pw = "TestAdmin1234"
auth_settings.enable_local_with_admin(admin_username="jtdt-admin", admin_display_name="admin",
    admin_password=pw, admin_password_confirm=pw, actor_ip="127.0.0.1")
c = auth_db.conn()
with c:
    c.execute("INSERT INTO users(username, display_name, source, external_dn, enabled, is_admin_seed, created_at)"
              " VALUES ('amy','Amy','ad','CN=Amy Example,OU=Sales,OU=TW,DC=example,DC=com',1,0,?)", (time.time(),))
admin = c.execute("SELECT id FROM users WHERE username='jtdt-admin'").fetchone()["id"]
tok, _ = sessions.issue(admin, remember=False, ip="127.0.0.1", ua="pytest")
# 共用事務機：成員裡有一個清單上沒有的 OU（經 API 設定的）
mid = mfp_store.create(mfp_store.SHARED, None, "mfp-ou.example.com", 443, "https", "共用 OU 測試", None)["id"]
mfp_store.set_members(mid, [("ou", "OU=Legacy,DC=example,DC=com")])
# 用印：一顆章的保管人是同一個 OU
buf = io.BytesIO(); Image.new("RGBA", (100, 100), (200, 20, 20, 255)).save(buf, "PNG")
a = asset_manager.create_from_bytes("ou-seal", "stamp", buf.getvalue())
seal_policy.set_policy(a.id, "stamp", {"custodians": [{"type": "ou", "key": "OU=Legacy,DC=example,DC=com"}]})
print(json.dumps({"admin": tok, "cookie": sessions.COOKIE_NAME, "mfp": mid, "asset": a.id}))
"""


@pytest.fixture(scope="module")
def site():
    data = tempfile.mkdtemp(prefix="oupick-")
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



def _labels(ev, box):
    return ev(f"[...document.querySelectorAll('#{box} label')].map(l => [l.querySelector('input').dataset.key, l.querySelector('input').checked, l.textContent])")


def test_shared_mfp_members_pick_an_ou_and_keep_the_existing_one(site):
    port, send, info = site
    ev, wait, shot = _tools(send)
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/admin/mfp"})
    btn = "[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '成員')"
    assert wait(f"!!({btn})"), "共用事務機清單沒有載出來"
    ev(f"({btn}).click()")
    assert wait("!document.getElementById('mfMembersPanel').hidden && document.querySelectorAll('#mfMOus label').length >= 2")
    rows = {k: (on, t) for k, on, t in _labels(ev, "mfMOus")}
    assert rows[LEGACY][0] is True, "已經設定的 OU 要列出來、維持勾選"
    assert rows[SALES][0] is False and "Sales / TW" in rows[SALES][1], rows
    assert "1" in ev("document.getElementById('mfMOuCount').textContent")
    ev(f"document.querySelector('#mfMOus input[data-key=\"{SALES}\"]').click()")
    shot("mfp-members-ou")
    ev("document.getElementById('mfMembersSave').click()")
    assert wait("document.getElementById('mfMembersMsg').textContent.includes('2')"), ev("document.getElementById('mfMembersMsg').textContent")
    m = ev(f"fetch('/tools/mfp-fetch/api/shared/{info['mfp']}/members').then(r => r.json()).then(j => j.members.map(x => x.type + ':' + x.key))")
    assert sorted(m) == sorted([f"ou:{LEGACY}", f"ou:{SALES}"]), m


def test_seal_custodians_pick_an_ou_and_keep_the_existing_one(site):
    port, send, info = site
    ev, wait, shot = _tools(send)
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/admin/seal-request"})
    assert wait("document.querySelectorAll('#srRows tr').length > 0"), "清單沒有載出來"
    # 保管人那一格的「設定」
    ev("[...document.querySelectorAll('#srRows button')].find(b => b.textContent.trim() === '設定').click()")
    assert wait("!document.getElementById('srPicker').hidden && document.querySelectorAll('#srOus label').length >= 2")
    rows = {k: (on, t) for k, on, t in _labels(ev, "srOus")}
    assert rows[LEGACY][0] is True, "已經設定的 OU 要列出來、維持勾選"
    assert rows[SALES][0] is False and "Sales / TW" in rows[SALES][1], rows
    ev(f"document.querySelector('#srOus input[data-key=\"{SALES}\"]').click()")
    assert wait("document.getElementById('srOuCount').textContent === '(2)'")
    shot("seal-picker-ou")
    ev("document.getElementById('srPickerSave').click()")
    assert wait("document.getElementById('srPickerMsg').textContent.length > 0")
    st = ev("fetch('/admin/seal-request/api/state').then(r => r.json()).then(s => s.assets[0].custodians.map(x => x.type + ':' + x.key))")
    assert sorted(st) == sorted([f"ou:{LEGACY}", f"ou:{SALES}"]), st
