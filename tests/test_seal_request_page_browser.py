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
        sample2 = _pdf(str(Path(_uploadable_dir()) / "seal-annex.pdf"))
        yield port, send, info, (sample, sample2)
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
    port, send, info, (sample, sample2) = site

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

    # 卡片之間要有間距（#srEdit 包了一層，站台的 .panel + .panel 接不上；使用者 2026-11-09 截圖：申請內容貼著上面）
    GAP = """(() => {
      const vis = [...document.querySelectorAll('#srCase .panel:not(.sr-actionbar)')].filter(e => e.offsetParent !== null);
      let m = 999;
      for (let i = 1; i < vis.length; i++) m = Math.min(m, vis[i].getBoundingClientRect().top - vis[i-1].getBoundingClientRect().bottom);
      return vis.length > 1 ? m : -1;
    })()"""
    g = ev(GAP)
    assert g >= 12, ("編輯畫面卡片之間沒有間距", g)

    doc = send("DOM.getDocument", {"depth": -1})["result"]
    # 用印文件：用共用的上傳框一次選兩份
    assert ev("document.getElementById('srDocUp-input').multiple") is True, "用印文件要能一次選多份"
    node = send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#srDocUp-input"})["result"]
    send("DOM.setFileInputFiles", {"files": [sample, sample2], "nodeId": node["nodeId"]})
    assert wait("document.querySelectorAll('#srDocList .sr-doc').length === 2", 60), \
        ("兩份文件沒有都列出來", ev("document.querySelectorAll('#srDocList .sr-doc').length"),
         ev("document.getElementById('srDocMsg').textContent"), ev("document.querySelector('#srDocUp .drop-zone-filename').textContent"))
    assert wait("!document.getElementById('srDocPanel').hidden && document.getElementById('srPageImg').naturalWidth > 0"), \
        "預覽圖沒有載出來"
    assert ev("document.querySelectorAll('#srDocTabs .sr-doctab').length") == 2, "多份文件時預覽上方要能切換"

    ev("""(() => {
      document.getElementById('srFSubject').value = '年度維護報價';
      document.getElementById('srFPurpose').value = '報價用';
      const cb = document.querySelector('#srAssets input[type=checkbox]');
      cb.checked = true; cb.dispatchEvent(new Event('change', {bubbles: true}));
    })()""")
    # 勾了章：每一份文件各先放一處
    assert wait("document.querySelectorAll('#srWishRows tr').length === 2"), "勾章後每一份文件要各有一處"
    ev("""(() => {
      document.querySelectorAll('#srWishRows input[type=number]').forEach(i => { i.value = '1'; i.dispatchEvent(new Event('change', {bubbles: true})); });
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
    g = ev(GAP)
    assert g >= 12, ("檢視畫面卡片之間沒有間距", g)
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
    assert ev("document.querySelectorAll('#srPlcRows tr').length") == 2, "申請者標的位置要帶進來（兩份各一處）"
    ev("document.getElementById('srStampGo').click()")
    assert wait("!!document.querySelector('.modal-ok')"), "用印前沒有確認視窗"
    card = ev("document.querySelector('.modal-card').textContent")
    assert "第 1 頁" in card and "seal-page.pdf" in card and "seal-annex.pdf" in card, ("確認視窗要逐份列出蓋在哪幾頁", card)
    ev("document.querySelector('.modal-ok').click()")
    assert wait("!!document.querySelector('#srCaseMeta .sr-state.done') && !document.getElementById('srDone').hidden"), \
        ("用印後沒有變成已完成", ev("document.getElementById('srStampMsg').textContent"))

    # 申請者：看得到下載連結、拿得到蓋好章的 PDF
    login(info["a"], f"http://127.0.0.1:{port}/seal-request#mine")
    assert wait("document.querySelectorAll('#srRows tr').length === 1")
    # 清單可以篩選、表頭可以排序（使用者 2026-11-09）
    ev("(() => { const q = document.getElementById('srFq'); q.value = '不存在的字'; q.dispatchEvent(new Event('input')); })()")
    assert ev("document.querySelectorAll('#srRows tr').length") == 0, "關鍵字篩選沒有作用"
    assert not ev("document.getElementById('srEmpty').hidden"), "篩選後沒有結果時要說明"
    assert "0 / 1" in ev("document.getElementById('srFCount').textContent")
    ev("document.getElementById('srFClear').click()")
    assert ev("document.querySelectorAll('#srRows tr').length") == 1, "清除篩選後要回到全部"
    ev("(() => { const s = document.getElementById('srFState'); s.value = 'done'; s.dispatchEvent(new Event('change')); })()")
    assert ev("document.querySelectorAll('#srRows tr').length") == 1, "狀態篩選「已完成」應該留下這一件"
    ev("document.getElementById('srFClear').click()")
    ev("document.querySelector('#srList th[data-sort=number]').click()")
    assert ev("document.querySelector('#srList th[data-sort=number]').classList.contains('asc')"), "點表頭要變升冪"
    ev("document.querySelector('#srList th[data-sort=number]').click()")
    assert ev("document.querySelector('#srList th[data-sort=number]').getAttribute('aria-sort')") == "descending"
    # 再開兩件草稿，排序與狀態篩選要對多筆真的有作用
    for _ in range(2):
        login(info["a"], f"http://127.0.0.1:{port}/seal-request#new")
        assert wait("document.querySelectorAll('#srFlows .sr-flow button').length === 1")
        ev("document.querySelector('#srFlows .sr-flow button').click()")
        assert wait("!document.getElementById('srEdit').hidden")
    login(info["a"], f"http://127.0.0.1:{port}/seal-request#mine")
    assert wait("document.querySelectorAll('#srRows tr').length === 3")
    nums = lambda: ev("[...document.querySelectorAll('#srRows tr td:first-child')].map(t => t.textContent)")
    th = "document.querySelector('#srList th[data-sort=number]').click()"
    ev(th)
    assert nums() == sorted(nums()), ("編號升冪", nums())
    ev(th)
    assert nums() == sorted(nums(), reverse=True), ("編號降冪", nums())
    ev("(() => { const s = document.getElementById('srFState'); s.value = 'draft'; s.dispatchEvent(new Event('change')); })()")
    assert ev("document.querySelectorAll('#srRows tr').length") == 2, "狀態篩選「草稿」應該剩兩件"
    assert "2 / 3" in ev("document.getElementById('srFCount').textContent")
    ev("document.getElementById('srFClear').click()")
    ev("document.querySelector('#srRows .sr-state.done').closest('tr').click()")
    assert wait("!document.getElementById('srDone').hidden"), "申請者看不到完成區"
    href = ev("document.getElementById('srResultDl').getAttribute('href')")
    st = ev("fetch(" + json.dumps(href) + ").then(r => r.status + ' ' + r.headers.get('content-type'))")
    assert st == "200 application/zip", ("兩份時「全部下載」是 ZIP", st)
    assert ev("document.querySelectorAll('#srResultList .sr-doc').length") == 2, "每一份要能各自下載"
    href1 = ev("document.querySelector('#srResultList a').getAttribute('href')")
    st = ev("fetch(" + json.dumps(href1) + ").then(r => r.status + ' ' + r.headers.get('content-type'))")
    assert st == "200 application/pdf", st

