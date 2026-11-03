"""從事務機掃描、共用事務機的管理頁 —— 在真的瀏覽器裡跑一次（對本機一台假的 eSCL 設備）。

* 事務機取件：連線測試讀到 eSCL → 儲存 → 清單出現「掃描」→ 選項照能力表 → 開始掃描 →
  完成、存進工作區（有「開啟」）→「最近的掃描」列出來。
* 管理頁（設定 → 共用事務機）：同一份畫面、標題與儲存鈕換成共用事務機 → 建立 → 清單有「成員」→
  成員面板打得開、存得了。

假設備在 127.0.0.1 —— 實例用這支測試裡的啟動程式碼放行 127.0.0.1 與那個埠（正式程式碼沒有放行開關）。
未啟用登入、從 localhost 連（「只准本機」那條會放行；管理頁在未啟用登入時也開放）。
截圖存到 `JTDT_SHOT_DIR`（有設才存）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

from tests.test_mfp_fetch_page_browser import _Tab, _browser, _free_port, _open, _profile_arg  # noqa: F401
from tests.test_mfp_escl import Fake, pdf_bytes

pytestmark = pytest.mark.skipif(
    _browser() is None or __import__("importlib").util.find_spec("websockets") is None,
    reason="沒有 chromium / websockets —— 這條要真的瀏覽器才驗得到")

_LAUNCH = """
import sys
sys.path.insert(0, {root!r})
import app.core.mfp_client as m
_real = m.check_address
m.check_address = lambda ip: None if ip == "127.0.0.1" else _real(ip)
m.ALLOWED_PORTS = tuple(m.ALLOWED_PORTS) + ({fake},)
import app.core.mfp_escl as e
e.STATUS_POLL = 0.1
e.PAGE_POLL = 0.1
import uvicorn
uvicorn.run("app.main:app", host="127.0.0.1", port={port}, log_level="warning")
"""


@pytest.fixture(scope="module")
def live():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    fake = Fake()
    srv_fake = fake.serve()
    fport = srv_fake.server_address[1]
    fake.port = fport
    data = tempfile.mkdtemp(prefix="mfpscan-")
    port, cdp = _free_port(), _free_port()
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    srv = subprocess.Popen([sys.executable, "-c", _LAUNCH.format(root=str(root), fake=fport, port=port)],
                           cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen(
        [_browser(), "--headless=new", "--no-sandbox", "--disable-gpu",
         _profile_arg(), f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
                urllib.request.urlopen(f"http://127.0.0.1:{cdp}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.skip("實例或瀏覽器起不來")
        yield port, cdp, fake
    finally:
        br.terminate()
        srv.terminate()
        srv_fake.shutdown()
        try:
            br.wait(timeout=5)
            srv.wait(timeout=5)
        except Exception:
            br.kill()
            srv.kill()
        shutil.rmtree(data, ignore_errors=True)


def _start(t, base, path, locale="zh-Hant"):
    t.send("Runtime.enable")
    t.send("Log.enable")
    t.send("Page.enable")
    t.send("Network.enable")
    t.send("Network.setCookie", {"name": "jtdt_locale", "value": locale, "url": base})
    t.send("Page.navigate", {"url": base + path})
    assert t.wait_for("document.querySelector('#mfListWrap .mf-empty, #mfListWrap table') "
                      "&& !document.querySelector('#mfListWrap').textContent.includes('載入中')"), \
        "清單沒有載入（JS 沒跑起來？）"


def _test_and_save(t, fport, name):
    t.js(f"""(() => {{ const s = document.getElementById('mfTarget');
             const o = document.createElement('option'); o.value = 'http:{fport}'; o.textContent = 'HTTP {fport}';
             s.append(o); s.value = 'http:{fport}';
             document.getElementById('mfHost').value = '127.0.0.1';
             document.getElementById('mfTestBtn').click(); return true; }})()""")
    # 連線測試最多排隊 30 秒（PROBE_WAIT）加上連線本身；整批測試時機器很忙，40 秒的預設等不到（2026-10-22 完整測試紅過一次）
    # 失敗時把畫面狀態一起印出來：按鈕停在「測試中」＝請求卡在伺服器；按鈕沒變＝根本沒送出（2026-10-22、23 整批時紅過，單跑都過）
    assert t.wait_for("!document.getElementById('mfResultPanel').hidden", 90), \
        "測試結果沒有出現：" + str(t.js("""(() => { const b = document.getElementById('mfTestBtn');
            return JSON.stringify({err: document.getElementById('mfAddError').innerText,
              btn_disabled: b.disabled, btn_text: b.innerText,
              save_bar_hidden: document.getElementById('mfSaveBar').hidden,
              host: document.getElementById('mfHost').value, target: document.getElementById('mfTarget').value,
              ready: document.readyState}); })()"""))
    body = t.js("document.getElementById('mfResult').innerText")
    assert "Fake LaserJet MFP M1" in body, "連線測試要讀到 eSCL 能力表"
    t.js(f"document.getElementById('mfSaveName').value = {name!r}; document.getElementById('mfSaveBtn').click()")
    assert t.wait_for("document.querySelectorAll('#mfListWrap tbody tr').length >= 1"), "儲存後清單沒有出現"


@pytest.mark.parametrize("locale", ["zh-Hant", "en"])
def test_scan_from_the_tool_page(live, locale):
    port, cdp, fake = live
    base = f"http://localhost:{port}"
    with _open(cdp) as ws:
        t = _Tab(ws)
        _start(t, base, "/tools/mfp-fetch/", locale)
        # 名稱與檔名照使用者實際的資料（2026-10-15 截圖：「HP M141w」與 scan-…pdf 都被折成三行）
        _test_and_save(t, fake.port, "HP M141w")
        assert t.wait_for("!!document.querySelector('[data-mf-scan]')"), "支援 eSCL 的事務機要有「掃描」"
        # 清單每一列的按鈕排成一排（使用者 2026-10-14：「排列 下一版要改善」）—— 1280 寬
        t.send("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 900, "deviceScaleFactor": 1,
                                                      "mobile": False})
        time.sleep(0.3)
        assert t.js("[...document.querySelectorAll('#mfListWrap .mf-row-actions')].every(box => "
                    "new Set([...box.children].map(b => Math.round(b.getBoundingClientRect().top))).size === 1)"), \
            "按鈕折成兩排"
        t.js("document.querySelector('[data-mf-scan]').click()")
        assert t.wait_for("!document.getElementById('mfScanPanel').hidden")
        opts = t.js("['mfScanSource','mfScanColor','mfScanRes','mfScanPaper'].map(id => "
                    "[...document.getElementById(id).options].map(o => o.value))")
        assert opts[0] == ["platen"] and opts[1] == ["color", "gray"] and opts[2] == ["200", "300", "600"], opts
        assert t.js("document.getElementById('mfScanRes').value") == "300"
        assert t.js("!!document.querySelector('#mfScanPanel .jt-select-trigger')"), "掃描選項要是本站樣式的下拉"
        t.shot(f"mfp_scan_{locale}_panel_1280", 1280)
        # 存成 PDF：先暫存、預覽，接著掃，最後存成一份（使用者 2026-11-03）
        fake.docs = [pdf_bytes(2)]
        t.js("document.getElementById('mfScanName').value = 'scan-20261014-144007'; document.getElementById('mfScanStart').click()")
        assert t.wait_for("document.querySelectorAll('#mfTrayGrid .mf-tray-page').length === 2", 60), \
            t.js("document.getElementById('mfScanState').innerText")
        state = t.js("document.getElementById('mfScanState').innerText")
        # 走什麼協定要顯示（使用者 2026-10-14）
        assert "eSCL" in state and ("HTTP " + str(fake.port)) in state, state
        assert t.js("!document.querySelector('#mfScanState a[href^=\"/workspace/file/\"]')"), "先暫存：還不可以已經在工作區"
        assert t.wait_for("[...document.querySelectorAll('#mfTrayGrid img')].every(i => i.complete && i.naturalWidth > 0)", 20), \
            "預覽圖要真的載得出來"
        # 平台玻璃換一張，接著掃
        fake.docs = [pdf_bytes(1)]
        t.js("document.getElementById('mfScanStart').click()")
        assert t.wait_for("document.querySelectorAll('#mfTrayGrid .mf-tray-page').length === 3", 60), "第二次掃描沒有接在後面"
        title = t.js("document.getElementById('mfTrayTitle').innerText")
        assert "3" in title, title
        t.shot(f"mfp_scan_{locale}_tray_1280", 1280)
        # 調整順序、刪一頁、放大看
        first = t.js("document.querySelector('#mfTrayGrid img').getAttribute('src')")
        t.js("document.querySelectorAll('#mfTrayGrid .mf-tray-page')[0].querySelectorAll('button')[1].click()")
        assert t.wait_for(f"document.querySelectorAll('#mfTrayGrid img')[1].getAttribute('src') === {first!r}"), "往後移沒有作用"
        t.js("[...document.querySelectorAll('#mfTrayGrid .mf-tray-page')].pop().querySelectorAll('button')[2].click()")
        assert t.wait_for("document.querySelectorAll('#mfTrayGrid .mf-tray-page').length === 2")
        t.js("document.querySelector('#mfTrayGrid img').click()")
        assert t.wait_for("!document.getElementById('mfTrayZoom').hidden && document.getElementById('mfTrayZoomImg').naturalWidth > 600", 20), \
            "放大預覽沒有出現"
        t.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        assert t.wait_for("document.getElementById('mfTrayZoom').hidden")
        # 存成一份 PDF
        t.js("document.getElementById('mfTraySave').click()")
        assert t.wait_for("document.getElementById('mfTrayState').classList.contains('done')", 60), \
            t.js("document.getElementById('mfTrayState').innerText")
        assert t.js("document.getElementById('mfTray').hidden"), "存好之後暫存區要清空"
        merged = t.js("document.getElementById('mfTrayState').innerText")
        assert "2" in merged, merged
        assert t.js("!!document.querySelector('#mfTrayState a[href^=\"/workspace/file/\"]')"), "完成後要能打開工作區的檔案"
        # 也要能直接下載（使用者 2026-10-14：「掃描完成 應該還要有下載按鈕」）
        assert t.js("!!document.querySelector('#mfTrayState a[href$=\"?dl=1\"]')"), "完成後要有下載"
        assert t.wait_for("document.getElementById('mfScansWrap').innerText.includes('scan-20261014-144007.pdf')"), \
            "最近的掃描沒有列出合併的檔案"
        assert t.wait_for("document.querySelectorAll('#mfScansWrap tbody tr').length >= 1"), "最近的掃描沒有列出來"
        # 「最近的掃描與取件」的按鈕排成一排、不超出外框（使用者 2026-10-15：欄位還有很大空位，按鈕卻被擠成三行）
        for width in (1280, 1180, 900):
            t.send("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1,
                                                          "mobile": False})
            time.sleep(0.3)
            rows = t.js("[...document.querySelectorAll('#mfScansWrap tbody tr')].map(tr => { const bs = "
                        "[...tr.querySelectorAll('td:last-child .btn')]; return [bs.length, "
                        "new Set(bs.map(b => Math.round(b.getBoundingClientRect().top))).size]; })")
            rows = [r for r in rows if r[0]]      # 合併掉的暫存掃描沒有按鈕（檔案已經清掉）
            assert rows and all(n >= 2 and (lines == 1 or width < 1100) for n, lines in rows), \
                f"{width} 寬：按鈕折行了 {rows}"
            assert t.js("(() => { const w = document.getElementById('mfScansWrap');"
                        " return w.querySelector('table').getBoundingClientRect().right"
                        " <= w.getBoundingClientRect().right + 1; })()"), f"{width} 寬：表格超出外框"
            # 時間、事務機、內容不可以折行（有空間的時候）—— 每一格的文字只有一行
            # 只量文字節點（Range 包住元素時會連外框也算進去，跟文字行差 1 像素就被當成兩行）
            lines = t.js("[...document.querySelectorAll('#mfScansWrap tbody tr:first-child td')].slice(0, 3).map(td => {"
                         " const tops = new Set(); const w = document.createTreeWalker(td, NodeFilter.SHOW_TEXT);"
                         " for (let n = w.nextNode(); n; n = w.nextNode()) { if (!n.textContent.trim()) continue;"
                         "   const r = document.createRange(); r.selectNodeContents(n);"
                         "   for (const x of r.getClientRects()) tops.add(Math.round(x.top / 4)); }"
                         " return tops.size; })")
            if width >= 1280:
                assert lines == [1, 1, 1], (f"{width} 寬：時間 / 事務機 / 內容被折行（每格行數）{lines}", t.js(
                    "[...document.querySelectorAll('#mfScansWrap tbody tr:first-child td')].map(td => [td.className,"
                    " Math.round(td.getBoundingClientRect().width), td.innerHTML.slice(0, 160)])"))
        t.shot(f"mfp_scan_{locale}_done_1280", 1280)
        t.shot(f"mfp_scan_{locale}_done_390", 390)
        # 手機寬度：事務機名稱不可以一個字一行（格子變區塊後還留著 1% 寬）
        t.send("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 900, "deviceScaleFactor": 1, "mobile": True})
        time.sleep(0.3)
        assert t.js("(() => { const c = document.querySelector('#mfScansWrap tbody td:nth-child(2) .mf-cap');"
                    " return c.getBoundingClientRect().height < parseFloat(getComputedStyle(c).fontSize) * 2; })()"), \
            "390 寬：事務機名稱折成一字一行"
        t.send("Emulation.clearDeviceMetricsOverride", {})
        # 收拾：移除這台，下一個語言從空清單開始
        t.js("[...document.querySelectorAll('#mfListWrap tbody button')].find(b => b.classList.contains('mf-del')).click()")
        assert t.wait_for("!!document.querySelector('.modal-ok')")
        t.js("document.querySelector('.modal-ok').click()")
        assert t.wait_for("!!document.querySelector('#mfListWrap .mf-empty')")
        assert not t.errs, t.errs


def test_shared_admin_page(live):
    port, cdp, fake = live
    base = f"http://localhost:{port}"
    with _open(cdp) as ws:
        t = _Tab(ws)
        _start(t, base, "/admin/mfp")
        assert "共用事務機" in t.js("document.querySelector('h1').innerText")
        assert t.js("document.querySelector('#mfSaveBtn').innerText").strip() == "儲存為共用事務機"
        _test_and_save(t, fake.port, "總公司")
        assert t.wait_for("[...document.querySelectorAll('#mfListWrap tbody button')].some(b => b.innerText.includes('成員'))")
        assert not t.js("!!document.querySelector('[data-mf-scan]')"), "管理頁不掃描"
        assert t.js("!!document.querySelector('#mfListWrap .mf-del')"), "管理頁要能移除共用事務機"
        fits = ("(() => { const w = document.getElementById('mfListWrap'); const tb = w.querySelector('table');"
                " return tb.getBoundingClientRect().right <= w.getBoundingClientRect().right + 1; })()")
        one_row = ("[...document.querySelectorAll('#mfListWrap .mf-row-actions')].every(box => "
                   "new Set([...box.children].map(b => Math.round(b.getBoundingClientRect().top))).size === 1)")
        for width in (1280, 900):
            t.send("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1,
                                                          "mobile": False})
            time.sleep(0.3)
            assert t.js(fits), f"{width} 寬：表格超出外框（最右邊的按鈕被擠出去）"
            if width >= 1280:
                assert t.js(one_row), "1280 寬：按鈕要排成一排"
        t.send("Emulation.clearDeviceMetricsOverride", {})
        assert t.js("[...document.querySelectorAll('#mfListWrap th')].every(th => th.getBoundingClientRect().height < 44)"), \
            "表頭不可以被擠成直排"
        # 客戶 2026-10-21：同一個人有 AD 與 SSO 兩個同名帳號，挑成員時看起來一模一樣 —— 標出來源與「同名帳號」
        t.js("""(() => { const real = window.fetch; window.fetch = (u, o) => String(u).endsWith('/api/shared-subjects')
              ? Promise.resolve(new Response(JSON.stringify({ok: true, groups: [{key: '7', name: 'Domain Users', source: 'ad', members: 0}], users: [
                  {key: '91', username: 'jchang', display_name: '張先生', source: 'ad'},
                  {key: '92', username: 'jchang', display_name: '張先生', source: 'oidc'},
                  {key: '93', username: 'amy', display_name: 'amy', source: 'local'}]}),
                  {status: 200, headers: {'Content-Type': 'application/json'}}))
              : real(u, o); return true; })()""")
        t.js("[...document.querySelectorAll('#mfListWrap tbody button')].find(b => b.innerText.includes('成員')).click()")
        assert t.wait_for("!document.getElementById('mfMembersPanel').hidden"), "成員面板沒有打開"
        assert t.wait_for("document.querySelectorAll('#mfMUsers label').length === 3")
        rows = t.js("[...document.querySelectorAll('#mfMUsers label')].map(l => [l.querySelector('span').firstChild.textContent, !!l.querySelector('.mf-tag.dup')])")
        assert rows == [["jchang@ad", True], ["jchang@oidc", True], ["amy@local", False]], rows
        g = t.js("document.querySelector('#mfMGroups label').innerText")
        assert "Domain Users" in g and "ad" in g and "本站 0 人" in g, g
        t.send("Page.reload", {})
        assert t.wait_for("[...document.querySelectorAll('#mfListWrap tbody button')].some(b => b.innerText.includes('成員'))")
        t.js("[...document.querySelectorAll('#mfListWrap tbody button')].find(b => b.innerText.includes('成員')).click()")
        assert t.wait_for("!document.getElementById('mfMembersPanel').hidden"), "成員面板沒有打開"
        t.js("document.getElementById('mfMembersSave').click()")
        assert t.wait_for("document.getElementById('mfMembersMsg').innerText.includes('0')"), "成員存不了"
        t.shot("mfp_shared_admin_1280", 1280)
        # 客戶 2026-10-21：在這一頁找掃描與取件按鈕 —— 說明講清楚在哪裡用，附連結；自己是成員的標出來
        assert t.js("document.getElementById('mfGoTool').getAttribute('href')") == "/tools/mfp-fetch/"
        assert t.js("!!document.querySelector('#mfListWrap .mf-tag.member')"), "未啟用登入：每台都看得到，標「你是成員」"
        shared_id = t.js("document.querySelector('#mfListWrap [data-mf-detail]').dataset.mfDetail")
        # 共用的這台出現在工具頁，標「共用」，沒有改名與移除
        t.send("Page.navigate", {"url": base + "/tools/mfp-fetch/"})
        assert t.wait_for("!!document.querySelector('#mfListWrap .mf-src.shared')"), "工具頁要列出共用事務機"
        assert not t.js("!!document.querySelector('#mfListWrap .mf-del')"), "共用事務機在工具頁不能移除"
        # 自己又新增了同一台：那一列提示「已有共用的同一台」（客戶同時有自己的 C3510 與共用的 C3510）
        _test_and_save(t, fake.port, "自己的")
        assert t.wait_for("!!document.querySelector('#mfListWrap [data-mf-dup]')"), "同一台要提示已有共用"
        assert t.js("document.querySelector('[data-mf-dup]').title").count("總公司") == 1
        # 在別的分頁改了共用事務機（這裡用 API 改名），切回來就重讀清單，不用重新整理
        t.js(f"fetch('/tools/mfp-fetch/api/shared/{shared_id}', {{method: 'PATCH', headers: {{'Content-Type': 'application/json'}},"
             " body: JSON.stringify({display_name: '總公司二樓'})})")
        time.sleep(3.2)
        t.js("document.dispatchEvent(new Event('visibilitychange'))")
        assert t.wait_for("document.getElementById('mfListWrap').innerText.includes('總公司二樓')"), "切回分頁沒有重讀清單"
        # 只認得廠牌、型號沒抓到：型號欄寫廠牌（客戶 KYOCERA ECOSYS M6535cidn 的型號由網頁程式之後才填）
        t.js("""(() => { const real = window.fetch; window.fetch = (u, o) => (String(u).endsWith('/api/connections') && !(o && o.method && o.method !== 'GET'))
              ? Promise.resolve(new Response(JSON.stringify({ok: true, can_add: true, connections: [{id: 999, display_name: 'm6535',
                  host: '10.0.0.9', vendor: 'Kyocera', detect_status: 'experimental_vendor', shared: false, can_manage: true}]}),
                  {status: 200, headers: {'Content-Type': 'application/json'}}))
              : real(u, o); return true; })()""")
        time.sleep(3.2)
        t.js("document.dispatchEvent(new Event('visibilitychange'))")
        assert t.wait_for("document.getElementById('mfListWrap').innerText.includes('Kyocera（型號未辨識）')"), \
            t.js("document.getElementById('mfListWrap').innerText")
        t.send("Page.reload", {})
        assert t.wait_for("!!document.querySelector('#mfListWrap [data-mf-dup]')")
        t.js("[...document.querySelectorAll('#mfListWrap tbody button')].find(b => b.classList.contains('mf-del')).click()")
        assert t.wait_for("!!document.querySelector('.modal-ok')")
        t.js("document.querySelector('.modal-ok').click()")
        assert t.wait_for("!document.querySelector('#mfListWrap [data-mf-dup]')")
        assert not t.errs, t.errs
