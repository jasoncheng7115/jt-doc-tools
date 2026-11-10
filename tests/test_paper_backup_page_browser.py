"""加密紙本備份：真瀏覽器端到端。

在頁面上建立一組備份（瀏覽器裡加密、畫 PDF），再把產生的主文件 PDF 與兩份分片 PDF
從還原頁的上傳框放回去（PDF.js 畫頁面、逐塊認 QR），還原出來要跟原文一模一樣；
另外驗手動輸入備援碼的逐組檢查與整段貼上、稽核只記不含秘密的欄位。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests.test_doc_straighten_quad_overlay_e2e import _browser, _free_port  # noqa: E402
from tools.browser_probe import profile_arg as _profile_arg  # noqa: E402

CHROME = "/usr/bin/chromium-browser"
pytestmark = pytest.mark.skipif(not Path(CHROME).exists(), reason="沒有 chromium，跳過真瀏覽器測試")

SECRET = "root 密碼：Tr0ub4dor&3 / 復原金鑰 4F2A-99C1-ZZ07"


@pytest.fixture(scope="module")
def site():
    br_path = _browser()
    if not br_path:
        pytest.skip("沒有 chromium")
    data = tempfile.mkdtemp(prefix="pbpage-")
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    port, cdp = _free_port(), _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                            "--port", str(port), "--log-level", "warning"],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # pbinsecure.test 指到本機：用來開一個「不是 HTTPS、也不是 localhost」的連線（P08）
    br = subprocess.Popen([br_path, "--headless=new", "--no-sandbox", "--disable-gpu", _profile_arg(),
                           "--host-resolver-rules=MAP pbinsecure.test 127.0.0.1",
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
        events: list[dict] = []

        def send(method, params=None):
            n[0] += 1
            i = n[0]
            ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
            while True:
                m = json.loads(ws.recv(timeout=300))
                if m.get("id") == i:
                    return m
                if "method" in m:
                    events.append(m)

        send.events = events
        yield port, send, data
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


def test_create_print_and_restore_in_the_browser(site):
    port, send, data = site

    def ev(expr):
        r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        res = r.get("result") or {}
        assert not res.get("exceptionDetails"), res.get("exceptionDetails")
        return (res.get("result") or {}).get("value")

    def wait(expr, t=60):
        for _ in range(int(t / 0.25)):
            if ev(expr):
                return True
            time.sleep(0.25)
        return False

    send("Page.enable"); send("Runtime.enable"); send("Network.enable", {"maxPostDataSize": 1048576})
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/paper-backup/"})
    assert wait("document.readyState === 'complete' && !!document.getElementById('pbN') && document.getElementById('pbN').options.length > 0"), \
        "頁面程式沒有跑起來"
    assert ev("window.isSecureContext") is True
    assert ev("document.getElementById('pbInsecure').hidden") is True
    assert ev("document.getElementById('pbT').value") == "2" and ev("document.getElementById('pbN').value") == "3"
    # 頁首說明主文件與分片的關係，數字跟著設定走
    assert "3" in ev("document.getElementById('pbHowShares').textContent")
    ev("(() => { const n = document.getElementById('pbN'); n.value = '5'; n.dispatchEvent(new Event('change')); })()")
    assert "5" in ev("document.getElementById('pbHowShares').textContent")
    ev("(() => { const n = document.getElementById('pbN'); n.value = '3'; n.dispatchEvent(new Event('change')); })()")
    # 不用切換：檔案拖進文字框就改成備份檔案，「改用文字」回來
    ev("""(() => {
      const dt = new DataTransfer(); dt.items.add(new File(['abc'], 'k.bin'));
      const e = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt });
      document.getElementById('pbText').dispatchEvent(e);
    })()""")
    assert ev("document.getElementById('pbText').hidden") is True and "k.bin" in ev("document.getElementById('pbDropText').textContent")
    ev("document.getElementById('pbFileClear').click()")
    assert ev("document.getElementById('pbText').hidden") is False
    # 這支工具只做紙本：沒有「存成電子檔」的選項（使用者 2026-11-09）
    assert ev("document.querySelectorAll('input[name=pbMode]').length") == 0

    # 建立
    ev(f"document.getElementById('pbText').value = {json.dumps(SECRET)}")
    ev("document.getElementById('pbLabel').value = 'test label'")
    ev("document.getElementById('pbMake').click()")
    assert wait("document.querySelectorAll('#pbMadeFiles li').length === 4", 120), \
        ("沒有產生主文件＋3 份分片", ev("document.getElementById('pbMakeMsg').textContent"))
    names = ev("[...document.querySelectorAll('#pbMadeFiles a')].map(a => a.download)")
    assert names[0].endswith("-main.pdf") and names[1].endswith("-share-1-of-3.pdf") and names[3].endswith("-share-3-of-3.pdf")
    bid = ev("document.getElementById('pbMadeId').textContent")
    assert len(bid) == 15 and bid.count("-") == 2
    # 預覽縮圖（主文件 2 頁＋每份分片 1 頁）
    assert wait("document.querySelectorAll('#pbMadeFiles .pb-thumbs img').length === 5", 60), \
        ev("document.querySelectorAll('#pbMadeFiles .pb-thumbs img').length")
    assert ev("document.querySelector('#pbMadeFiles .pb-thumbs img').naturalWidth") > 300
    # 全部下載（ZIP）：Python 打得開、裡面 4 個檔
    import base64 as _b64, io as _io, zipfile as _zf
    zb64 = ev("document.getElementById('pbZip')._pbBlob.arrayBuffer().then(b => { let s=''; const u=new Uint8Array(b); for (let k=0;k<u.length;k+=8192) s+=String.fromCharCode(...u.subarray(k,k+8192)); return btoa(s); })")
    with _zf.ZipFile(_io.BytesIO(_b64.b64decode(zb64))) as z:
        assert sorted(n.split('-', 4)[-1] for n in z.namelist()) == ["main.pdf", "share-1-of-3.pdf", "share-2-of-3.pdf", "share-3-of-3.pdf"]
        assert all(z.read(n).startswith(b"%PDF-1.7") for n in z.namelist())
    assert ev("document.getElementById('pbZip').download").endswith(".zip")
    head = ev("document.querySelector('#pbMadeFiles a')._pbBlob.arrayBuffer().then(b => String.fromCharCode(...new Uint8Array(b).slice(0, 8)))")
    assert head.startswith("%PDF-1.7")

    # 把主文件與分片 1、3 的 PDF 放回還原頁的上傳框（跟使用者拖進來一樣走 FileUpload）
    ev("document.querySelector('.pb-tab[data-tab=restore]').click()")
    ev("""(async () => {
      const links = [...document.querySelectorAll('#pbMadeFiles a')];
      const dt = new DataTransfer();
      for (const i of [0, 1, 3]) {
        const b = links[i]._pbBlob;
        dt.items.add(new File([b], links[i].download, { type: 'application/pdf' }));
      }
      const inp = document.getElementById('pbScanUp-input');
      inp.files = dt.files;
      inp.dispatchEvent(new Event('change', { bubbles: true }));
    })()""")
    assert wait("!document.getElementById('pbRestoreGo').disabled", 180), \
        ("讀不齊", ev("document.getElementById('pbLog').innerText"), ev("document.getElementById('pbProgMain').textContent"))
    assert ev("document.getElementById('pbProgShare').textContent").startswith("2 / 2")
    assert bid in ev("document.getElementById('pbProgId').textContent")
    # 已載入的檔案一列一個，可以個別移除（傳錯檔案時）；移除後進度重算，再放回來又夠了
    rows = ev("[...document.querySelectorAll('#pbLog .pb-src')].map(li => li.innerText)")
    assert len(rows) == 3 and any("share-3-of-3" in r for r in rows), rows
    ev("[...document.querySelectorAll('#pbLog .pb-src')].find(li => li.innerText.includes('share-3-of-3')).querySelector('button').click()")
    assert ev("document.querySelectorAll('#pbLog .pb-src').length") == 2
    assert ev("document.getElementById('pbProgShare').textContent").startswith("1 / 2")
    assert ev("document.getElementById('pbRestoreGo').disabled") is True
    ev("""(async () => {
      const a = [...document.querySelectorAll('#pbMadeFiles a')][3];
      const dt = new DataTransfer(); dt.items.add(new File([a._pbBlob], a.download, { type: 'application/pdf' }));
      const inp = document.getElementById('pbScanUp-input'); inp.files = dt.files;
      inp.dispatchEvent(new Event('change', { bubbles: true }));
    })()""")
    assert wait("!document.getElementById('pbRestoreGo').disabled", 120)
    assert ev("document.getElementById('pbProgShare').textContent").startswith("2 / 2")

    ev("document.getElementById('pbRestoreGo').click()")
    assert wait("!document.getElementById('pbResult').hidden", 60), ev("document.getElementById('pbRestoreMsg').textContent")
    assert ev("document.getElementById('pbResultTextBox').hidden") is True, "內容預設不顯示"
    ev("document.getElementById('pbResultShow').click()")
    assert ev("document.getElementById('pbResultText').value") == SECRET

    # 清除：畫面上的內容要拿掉
    ev("document.getElementById('pbResultClear').click()")
    assert ev("document.getElementById('pbResultText').value") == "" and ev("document.getElementById('pbResult').hidden") is True

    # 手動輸入：打錯一組會標紅；整段貼上一份另一組的分片 → 標示是另一組
    ev("""(() => {
      const i = document.querySelectorAll('#pbCodes input')[0];
      i.value = 'ZZZZZZ'; i.dispatchEvent(new Event('input'));
    })()""")
    assert ev("document.querySelectorAll('#pbCodes input')[0].classList.contains('bad')")
    ev("""(async () => {
      const C = await import('/static/js/paper_backup/pb_codec.mjs');
      const share = crypto.getRandomValues(new Uint8Array(33)); share[32] = 7;
      const rec = C.packShareRecord(crypto.getRandomValues(new Uint8Array(8)), 2, 3, 2, share);
      const text = C.shareRecordToCode(rec).join(' ');
      const i = document.querySelectorAll('#pbCodes input')[0];
      const e = new Event('paste', { bubbles: true, cancelable: true });
      e.clipboardData = { getData: () => text };
      i.dispatchEvent(e);
    })()""")
    assert ev("[...document.querySelectorAll('#pbCodes input')].every(i => i.classList.contains('good'))"), "整段貼上沒有分到 16 格"
    ev("document.getElementById('pbCodeAdd').click()")
    assert wait("document.getElementById('pbLog').innerText.includes('ID')"), ev("document.getElementById('pbCodeMsg').textContent")

    # S13：整段建立＋還原期間，頁面送出的每一個請求都不含秘密，而且只連本站
    reqs = [e["params"]["request"] for e in send.events if e.get("method") == "Network.requestWillBeSent"]
    assert reqs, "沒有收到網路事件"
    for rq in reqs:
        url = rq.get("url", "")
        assert url.startswith((f"http://127.0.0.1:{port}/", "data:", "blob:")), url
        blob = url + json.dumps(rq.get("postData") or "") + json.dumps(rq.get("postDataEntries") or [])
        assert "Tr0ub4dor" not in blob and "4F2A-99C1" not in blob, url
    posts = [rq["url"] for rq in reqs if rq.get("method") == "POST"]
    assert posts and all(u.endswith("/tools/paper-backup/audit") for u in posts), posts
    # S14：瀏覽器儲存區沒有留下秘密（localStorage / sessionStorage / IndexedDB / Cache Storage）
    store = ev("""(async () => {
      const kv = s => Object.keys(s).map(k => k + '=' + s.getItem(k)).join('\\n');
      const dbs = indexedDB.databases ? (await indexedDB.databases()).map(d => d.name) : [];
      const cs = window.caches ? await caches.keys() : [];
      return JSON.stringify({ local: kv(localStorage), session: kv(sessionStorage), dbs, cs });
    })()""")
    assert "Tr0ub4dor" not in store and "4F2A" not in store, store
    st = json.loads(store)
    assert not [d for d in st["dbs"] if d and ("pb" in d.lower() or "paper" in d.lower())] and st["cs"] == [], st
    # 手機寬度（390）兩個分頁都不可以橫向溢出
    send("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 800, "deviceScaleFactor": 1, "mobile": True})
    time.sleep(0.5)
    for tab in ("create", "restore"):
        ev(f"document.querySelector('.pb-tab[data-tab={tab}]').click()")
        time.sleep(0.3)
        sw = ev("document.documentElement.scrollWidth")
        assert sw <= 390, (tab, sw)
    send("Emulation.clearDeviceMetricsOverride")

    # P03：列印測試頁 → 放回還原頁 → 三種密度全部讀到、內容正確；不會被算進正式備份
    ev("document.getElementById('pbRestoreReset').click()")
    ev("document.querySelector('.pb-tab[data-tab=create]').click()")
    ev("document.getElementById('pbTestMake').click()")
    assert wait("document.querySelectorAll('#pbTestFiles a').length === 1", 60), ev("document.getElementById('pbTestMsg').textContent")
    assert wait("document.querySelectorAll('#pbTestFiles .pb-thumbs img').length === 2", 60)
    ev("document.querySelector('.pb-tab[data-tab=restore]').click()")
    ev("""(() => {
      const a = document.querySelector('#pbTestFiles a');
      const dt = new DataTransfer(); dt.items.add(new File([a._pbBlob], a.download, { type: 'application/pdf' }));
      const inp = document.getElementById('pbScanUp-input'); inp.files = dt.files;
      inp.dispatchEvent(new Event('change', { bubbles: true }));
    })()""")
    assert wait("!document.getElementById('pbTestResult').hidden && document.getElementById('pbTestResult').classList.contains('pb-ok')", 120), \
        ev("document.getElementById('pbTestResult').innerText + ' | ' + document.getElementById('pbLog').innerText")
    assert "3 / 3" in ev("document.getElementById('pbTestResult').innerText")
    assert "還原驗證：成功" in ev("document.getElementById('pbTestResult').innerText")
    assert ev("document.getElementById('pbProgMain').textContent").startswith("0 /"), "測試頁不可以被算進正式備份"

    # 稽核：建立與還原各一筆，內容、標題都不在裡面
    time.sleep(1.5)
    with sqlite3.connect(Path(data) / "audit.sqlite") as db:
        rows = db.execute("SELECT event_type, target, details_json FROM audit_events WHERE event_type LIKE 'paper_backup_%'").fetchall()
    kinds = sorted(r[0] for r in rows)
    assert kinds == ["paper_backup_create", "paper_backup_restore"], rows
    blob = json.dumps(rows, ensure_ascii=False)
    assert "Tr0ub4dor" not in blob and "test label" not in blob


def test_plain_http_shows_the_warning_and_offers_the_offline_tool(site):
    """P08：用一般 http（不是 HTTPS、也不是 localhost）開，瀏覽器不提供加解密 → 紅框說明、不顯示建立與還原、給離線還原程式。"""
    port, send, _data = site

    def ev(expr):
        r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        res = r.get("result") or {}
        assert not res.get("exceptionDetails"), res.get("exceptionDetails")
        return (res.get("result") or {}).get("value")

    send("Page.enable"); send("Runtime.enable")
    send("Page.navigate", {"url": f"http://pbinsecure.test:{port}/tools/paper-backup/"})
    for _ in range(120):
        if ev("document.readyState === 'complete' && !!document.getElementById('pbInsecure')"):
            break
        time.sleep(0.25)
    assert ev("window.isSecureContext") is False
    for _ in range(40):
        if ev("!document.getElementById('pbInsecure').hidden"):
            break
        time.sleep(0.25)
    assert ev("!document.getElementById('pbInsecure').hidden"), "http 連線沒有顯示紅框"
    assert ev("document.getElementById('pbTool').hidden") is True
    assert ev("document.querySelector('#pbInsecure a').getAttribute('href')") == "/paper-backup/offline"
