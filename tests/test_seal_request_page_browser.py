"""用印申請頁（真瀏覽器）：申請者建立 → 上傳 → 選章與位置 → 送出；審核者在「待我審核」核准。

API 層在 `test_seal_flow.py`；這裡驗畫面接線：按鈕真的打到 API、預覽圖載得出來、示意框畫得出來、
確認視窗按下去才送出、換人登入後待審清單看得到。
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
from tools.browser_probe import uploadable_dir as _uploadable_dir  # noqa: E402

CHROME = "/usr/bin/chromium-browser"
pytestmark = pytest.mark.skipif(not Path(CHROME).exists(), reason="沒有 chromium，跳過真瀏覽器測試")

_SETUP = r"""
import io, json
from PIL import Image
from app.core import auth_settings, auth_db, audit_db, sessions, user_manager, seal_policy, seal_flow
auth_db.init(); audit_db.init()
pw = "TestAdmin1234"
auth_settings.enable_local_with_admin(admin_username="jtdt-admin", admin_display_name="admin",
    admin_password=pw, admin_password_confirm=pw, actor_ip="127.0.0.1")
a = user_manager.create_local("applicant", "applicant", "UserPass1234")
r = user_manager.create_local("reviewer", "reviewer", "UserPass1234")
c = user_manager.create_local("custodian", "custodian", "UserPass1234")
from app.core.asset_manager import asset_manager
buf = io.BytesIO(); Image.new("RGBA", (100, 100), (200, 20, 20, 255)).save(buf, "PNG")
st = asset_manager.create_from_bytes("page-seal", "stamp", buf.getvalue())
seal_policy.set_enabled(True)
seal_policy.set_policy(st.id, "stamp", {"mode": "approval", "requesters": [{"type": "user", "key": str(a)}],
                                        "custodians": [{"type": "user", "key": str(c)}]})
f = seal_flow.save_flow(None, {"name": "page-flow", "stages": [
    {"name": "boss", "rule": "any", "candidates": [{"type": "user", "key": str(r)}]}]})
ta, _ = sessions.issue(a, remember=False, ip="127.0.0.1", ua="pytest")
tr_, _ = sessions.issue(r, remember=False, ip="127.0.0.1", ua="pytest")
tc, _ = sessions.issue(c, remember=False, ip="127.0.0.1", ua="pytest")
print(json.dumps({"cookie": sessions.COOKIE_NAME, "a": ta, "r": tr_, "c": tc, "flow": f["id"]}))
"""


def _pdf(path: str) -> str:
    import fitz
    d = fitz.open()
    for i in range(2):
        d.new_page(width=595, height=842).insert_text((72, 100), f"Quotation page {i + 1}", fontsize=20)
    d.save(path)
    d.close()
    return path


@pytest.fixture(scope="module")
def site():
    br_path = _browser()
    if not br_path:
        pytest.skip("沒有 chromium")
    data = tempfile.mkdtemp(prefix="sealpage-")
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    out = subprocess.run([sys.executable, "-c", _SETUP], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-1200:]
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

        sample = _pdf(str(Path(_uploadable_dir()) / "seal-page.pdf"))
        yield port, send, info, sample
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


def test_apply_submit_and_approve(site):
    port, send, info, sample = site

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

    def login(token, url):
        # 先離開這一頁：同一頁只改 # 後面的部分，瀏覽器不會重新載入，畫面還是上一個人的
        send("Page.navigate", {"url": "about:blank"})
        time.sleep(0.3)
        send("Network.clearBrowserCookies")
        send("Network.setCookie", {"name": info["cookie"], "value": token, "domain": "127.0.0.1", "path": "/"})
        send("Page.navigate", {"url": url})
        time.sleep(1.0)

    send("Page.enable"); send("Runtime.enable"); send("Network.enable"); send("DOM.enable")
    login(info["a"], f"http://127.0.0.1:{port}/seal-request#new")
    assert wait("document.querySelectorAll('#srFlows .sr-flow button').length === 1"), "流程卡片沒有出來"
    assert ev("!!document.querySelector('.sb-seal-link')"), "側欄沒有「用印申請」"
    ev("document.querySelector('#srFlows .sr-flow button').click()")
    assert wait("!document.getElementById('srEdit').hidden"), "沒有進到編輯畫面"

    doc = send("DOM.getDocument", {"depth": -1})["result"]
    node = send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#srDocFile"})["result"]
    send("DOM.setFileInputFiles", {"files": [sample], "nodeId": node["nodeId"]})
    ev("document.getElementById('srDocFile').dispatchEvent(new Event('change', {bubbles: true}))")
    assert wait("document.getElementById('srDocMsg').textContent.includes('2')"), "上傳後沒有顯示頁數"
    assert wait("!document.getElementById('srDocPanel').hidden && document.getElementById('srPageImg').naturalWidth > 0"), \
        "預覽圖沒有載出來"

    ev("""(() => {
      document.getElementById('srFSubject').value = '年度維護報價';
      document.getElementById('srFPurpose').value = '報價用';
      const row = document.querySelector('#srAssets .sr-asset');
      row.querySelector('input[type=checkbox]').checked = true;
      row.querySelector('input[type=number]').value = '1';
      row.querySelector('select').value = 'br';
      document.getElementById('srSave').click();
    })()""")
    assert wait("document.querySelectorAll('#srPage .sr-box').length === 1"), "存檔後第 1 頁沒有畫出示意框"

    ev("document.getElementById('srSubmit').click()")
    assert wait("!!document.querySelector('.modal-ok')"), "送出前沒有確認視窗"
    ev("document.querySelector('.modal-ok').click()")
    assert wait("document.getElementById('srEdit').hidden && !!document.querySelector('#srCaseMeta .sr-state.reviewing')"), \
        ("送出後狀態沒有變成審核中", ev("document.getElementById('srEditMsg').textContent"))

    # 換審核者
    login(info["r"], f"http://127.0.0.1:{port}/seal-request#review")
    assert wait("document.querySelectorAll('#srRows tr').length === 1"), "待我審核看不到這件"
    assert ev("document.getElementById('srReviewCount').textContent") == "1"
    ev("document.querySelector('#srRows tr').click()")
    assert wait("!document.getElementById('srReview').hidden"), "審核區沒有出現"
    assert wait("document.getElementById('srPageImg').naturalWidth > 0"), "審核者看不到文件預覽"
    ev("document.getElementById('srApprove').click()")
    assert wait("!!document.querySelector('#srCaseMeta .sr-state.awaiting_stamp')"), "核准後沒有變成待用印"
    assert ev("document.getElementById('srReview').hidden") is True

    # 保管人：待我用印 → 預覽上有實際的章圖 → 確認用印 → 已完成
    login(info["c"], f"http://127.0.0.1:{port}/seal-request#stamp")
    assert wait("document.querySelectorAll('#srRows tr').length === 1"), "待我用印看不到這件"
    assert ev("document.getElementById('srStampCount').textContent") == "1"
    ev("document.querySelector('#srRows tr').click()")
    assert wait("!document.getElementById('srStamp').hidden"), "保管人的用印區沒有出現"
    assert wait("[...document.querySelectorAll('#srPage .sr-seal img')].some(i => i.naturalWidth > 0)"), \
        "預覽上沒有畫出實際的章圖"
    assert ev("document.querySelectorAll('#srPlcRows tr').length") == 1, "申請者標的位置要帶進來"
    ev("document.getElementById('srStampGo').click()")
    assert wait("!!document.querySelector('.modal-ok')"), "用印前沒有確認視窗"
    assert "第 1 頁" in ev("document.querySelector('.modal-card').textContent"), "確認視窗要列出蓋在哪幾頁"
    ev("document.querySelector('.modal-ok').click()")
    assert wait("!!document.querySelector('#srCaseMeta .sr-state.done') && !document.getElementById('srDone').hidden"), \
        ("用印後沒有變成已完成", ev("document.getElementById('srStampMsg').textContent"))

    # 申請者：看得到下載連結、拿得到蓋好章的 PDF
    login(info["a"], f"http://127.0.0.1:{port}/seal-request#mine")
    assert wait("document.querySelectorAll('#srRows tr').length === 1")
    ev("document.querySelector('#srRows tr').click()")
    assert wait("!document.getElementById('srDone').hidden"), "申請者看不到完成區"
    href = ev("document.getElementById('srResultDl').getAttribute('href')")
    st = ev("fetch(" + json.dumps(href) + ").then(r => r.status + ' ' + r.headers.get('content-type'))")
    assert st == "200 application/pdf", st

