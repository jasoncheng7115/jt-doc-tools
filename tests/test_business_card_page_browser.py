"""名片辨識畫面（真瀏覽器）：之前的批次、確認範圍（框、新增範圍）、校對（點欄位框出原文＋放大、儲存並確認）、
匯出預覽（含「同時存到工作區」）、用手機接續的 QR；手機寬度不橫向捲動。設 `JTDT_SHOT_DIR` 時另存截圖（發版前目視用）。

辨識在初始化的子行程裡先跑完（OCR 換成假的，這裡驗畫面接線不驗辨識品質），伺服器只讀結果。資料全部杜撰。
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
import json, sys
sys.path.insert(0, ".")
from app.core import auth_db, audit_db
auth_db.init(); audit_db.init()
from app.tools.business_card_reader import card_ocr, pipeline, store
from tests import bc_synth as S
LINES = [("範例科技股份有限公司", 32), ("王小明", 44), ("業務經理", 20), ("電話：(02)2345-6789 分機 302", 18),
         ("手機：0912-345-678", 18), ("Email: kevin@example.com", 18), ("統一編號：04595257", 18)]
def fake(img, **kw):
    lines = [card_ocr.Line(text=t, box=[60, 60 + i * 110, 700, 80], conf=95, height=h) for i, (t, h) in enumerate(LINES)]
    lines[1].alt, lines[1].alt_conf = "王曉明", 90.0          # 信心低的行放大再讀的另一種讀法（杜撰）
    return {"lines": lines, "rotation": 0, "engine": "fake", "image": img, "quality": 99}
card_ocr.ocr_card = fake
single = pipeline.create("local", "", "single", [("a.jpg", S.jpeg(S.scene_single()[0])), ("b.jpg", S.jpeg(S.scene_single(seed=9)[0]))],
                         {"event": "測試展覽"})
pipeline.wait_idle(120)
multi = pipeline.create("local", "", "multi", [("page.jpg", S.jpeg(S.scene_multi(6)[0]))], {})
forgot = pipeline.create("local", "", "single", [("six.jpg", S.jpeg(S.scene_multi(6, seed=5)[0]))], {"event": "忘了選模式"})
pipeline.wait_idle(120)
print(json.dumps({"single": single, "multi": multi, "forgot": forgot}))
"""


@pytest.fixture(scope="module")
def site():
    br_path = _browser()
    if not br_path:
        pytest.skip("沒有 chromium")
    data = tempfile.mkdtemp(prefix="bizcard-")
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    out = subprocess.run([sys.executable, "-c", _SETUP], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-1500:]
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

        send("Page.enable")
        send("Runtime.enable")
        yield port, send, info
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        br.terminate()
        srv.terminate()
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

    def wait(expr, t=40):
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
        time.sleep(0.6)
        r = send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        Path(d, f"{name}-{width}.png").write_bytes(base64.b64decode(r["result"]["data"]))
        send("Emulation.clearDeviceMetricsOverride")

    return ev, wait, shot


def _no_hscroll(send, ev):
    send("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 800, "deviceScaleFactor": 1, "mobile": True})
    time.sleep(0.6)
    w = ev("document.documentElement.scrollWidth")
    send("Emulation.clearDeviceMetricsOverride")
    return w


def test_frames_page_for_several_cards(site):
    port, send, info = site
    ev, wait, shot = _tools(send)
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/business-card-reader/"})
    assert wait("document.querySelectorAll('.bc-batch').length === 3"), "之前的辨識批次要列出三批"
    assert ev("!!document.querySelector('.bc-shared') && document.querySelector('.bc-shared').textContent.includes('共用')"), \
        "未啟用登入時要寫明批次是這台電腦上共用的"
    shot("bc-intake")
    # 用手機接續：QR 圖真的載得出來，Esc 關掉
    ev("document.getElementById('bcHandoff').click()")
    assert wait("!document.getElementById('bcHandoffBox').hidden && document.getElementById('bcHandoffImg').complete"
                " && document.getElementById('bcHandoffImg').naturalWidth > 100"), "QR Code 要載得出來"
    assert ev("document.getElementById('bcHandoffUrl').textContent").endswith("/tools/business-card-reader/")
    shot("bc-handoff")
    ev("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    assert wait("document.getElementById('bcHandoffBox').hidden")
    ev(f"[...document.querySelectorAll('.bc-batch')].find(r => r.textContent.includes('6 張名片')).querySelector('button').click()")
    assert wait("!document.getElementById('bcFrames').hidden"), "多卡模式要停在確認範圍"
    assert wait("document.getElementById('bcPageImg').complete && document.getElementById('bcPageImg').naturalWidth > 0")
    assert ev("document.querySelectorAll('#bcSvg polygon').length") == 6
    assert "6" in ev("document.getElementById('bcPageTitle').textContent")
    assert ev("document.getElementById('bcReview').hidden") is True, "還沒辨識，不顯示校對"
    ev("document.getElementById('bcAddBox').click()")
    assert ev("document.querySelectorAll('#bcSvg polygon').length") == 7
    assert ev("document.querySelectorAll('#bcSvg circle.h').length") == 4, "選取的框顯示四個角"
    assert ev("document.querySelectorAll('#bcSvg circle.rh').length") == 1, "選取的框有一個旋轉圓點"
    assert "7" in ev("document.getElementById('bcRecognizeText').textContent")
    shot("bc-frames")
    assert _no_hscroll(send, ev) <= 390, "手機寬度不可以橫向捲動"
    shot("bc-frames", 390)


def test_review_highlight_confirm_and_export_preview(site):
    port, send, info = site
    ev, wait, shot = _tools(send)
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/business-card-reader/"})
    assert wait("document.querySelectorAll('.bc-batch').length === 3")
    ev("[...document.querySelectorAll('.bc-batch')].find(r => r.textContent.includes('2 張名片')).querySelector('button').click()")
    assert wait("!document.getElementById('bcReview').hidden && document.querySelectorAll('#bcFields .bc-frow').length > 3")
    # 兩種收件模式各有示意圖，而且真的畫得出來（不是 0 寬）
    assert ev("[...document.querySelectorAll('.bc-mode .bc-ill')].filter(s => s.getBoundingClientRect().width > 50).length") == 2
    # 卡片標題要靠左：收折箭頭是標題的 ::before，也算一格；用 space-between 時標題被擠到中間（使用者截圖）
    gap = ev("(() => { const h = document.querySelector('#bcReview > h2'); "
             "return h.querySelector('span').getBoundingClientRect().left - h.getBoundingClientRect().left; })()")
    assert gap < 80, f"「3. 校對辨識結果」離標題列左邊 {gap}px，被擠到中間了"
    assert wait("document.getElementById('bcCardImg').complete && document.getElementById('bcCardImg').naturalWidth > 0"), \
        "卡面圖要載得出來"
    vals = ev("[...document.querySelectorAll('#bcFields input.field')].map(i => i.value)")
    assert "王小明" in vals and "0912-345-678" in vals
    # 點電話欄位 → 卡面框出原文、下方放大
    ev("[...document.querySelectorAll('#bcFields .bc-frow')].find(r => r.textContent.includes('電話')).querySelector('input').click()")
    assert wait("!document.getElementById('bcHl').hidden && !document.getElementById('bcZoomBox').hidden"), \
        "點欄位要框出原文並放大"
    shot("bc-review")
    # 改了沒存就切到下一張再回來：修改還在、提示有 1 位沒存；「全部儲存」一次存
    ev("(() => { const i = [...document.querySelectorAll('#bcFields .bc-frow')].find(r => r.textContent.includes('職稱'))"
       ".querySelector('input'); i.value = '草稿職稱'; i.dispatchEvent(new Event('input', {bubbles: true})); })()")
    assert wait("!document.getElementById('bcDirty').hidden && !document.getElementById('bcSaveAll').disabled")
    ev("document.getElementById('bcNext').click()")
    assert wait("!document.getElementById('bcDirty').hidden")
    ev("document.getElementById('bcPrev').click()")
    assert wait("[...document.querySelectorAll('#bcFields input.field')].some(i => i.value === '草稿職稱')"), \
        "切換名片再回來，沒存的修改要還在"
    ev("document.getElementById('bcSaveAll').click()")
    assert wait("document.getElementById('bcDirty').hidden && document.getElementById('bcSaveAll').disabled"), "全部儲存之後沒有待存的"
    assert wait("[...document.querySelectorAll('#bcFields input.field')].some(i => i.value === '草稿職稱')")
    # 另一種讀法：顯示在欄位下面，按「套用」才換（不自動換）
    assert wait("[...document.querySelectorAll('#bcFields .bc-alt')].some(r => r.textContent.includes('王曉明'))"), "要顯示另一種讀法"
    assert "王小明" in ev("[...document.querySelectorAll('#bcFields input.field')].map(i => i.value)")
    ev("[...document.querySelectorAll('#bcFields .bc-alt')].find(r => r.textContent.includes('王曉明')).querySelector('button').click()")
    assert wait("[...document.querySelectorAll('#bcFields input.field')].some(i => i.value === '王曉明') && !document.getElementById('bcDirty').hidden")
    assert "0" in ev("document.getElementById('bcExSum').textContent") or wait(
        "document.getElementById('bcExSum').textContent.length > 0")
    ev("document.getElementById('bcConfirm').click()")
    assert wait("document.getElementById('bcCounts').textContent.includes('已確認 1')"), "儲存並確認之後已確認 1 位"
    assert wait("document.getElementById('bcExSum').textContent.includes('1')")
    assert wait("document.querySelectorAll('#bcPreview tr').length === 2"), "預覽：標題＋1 列"
    assert ev("!document.getElementById('bcWsSaveRow').hidden"), "工作區有開時要有「同時存到工作區」"
    # XLSX / ODS 按下去先選篩選或純資料；CSV 先選編碼（UTF-8 含 BOM 標「建議」）
    ev("document.querySelector('.bc-fmts [data-fmt=xlsx]').click()")
    # XLSX 多一個「名片總表（中英對照）」（v1.17.53）；ODS 沒有
    assert wait("document.querySelectorAll('#bcMenu .bc-menu-i').length === 3")
    assert "名片總表" in ev("document.querySelectorAll('#bcMenu .bc-menu-i')[2].textContent")
    assert ev("document.querySelector('#bcMenu .bc-menu-i .bc-rec') !== null")
    shot("bc-export-menu")
    ev("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    assert wait("!document.getElementById('bcMenu')")
    ev("document.querySelector('.bc-fmts [data-fmt=ods]').click()")
    assert wait("document.querySelectorAll('#bcMenu .bc-menu-i').length === 2"), "ODS 只有篩選 / 純資料兩種"
    ev("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    assert wait("!document.getElementById('bcMenu')")
    ev("document.querySelector('.bc-fmts [data-fmt=csv]').click()")
    assert wait("document.querySelectorAll('#bcMenu .bc-menu-i').length === 3")
    assert "UTF-8" in ev("document.querySelector('#bcMenu .bc-menu-i').textContent")
    assert ev("document.querySelector('#bcMenu .bc-menu-i').textContent").find("建議") >= 0
    ev("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    # 按鈕在視窗底部時選單要往上開，不可以超出畫面（使用者截圖）
    ev("(() => { const b = document.querySelector('.bc-fmts [data-fmt=xlsx]'); const r = b.getBoundingClientRect(); "
       "window.scrollBy(0, r.bottom - (window.innerHeight - 30)); })()")
    ev("document.querySelector('.bc-fmts [data-fmt=xlsx]').click()")
    assert wait("document.querySelectorAll('#bcMenu .bc-menu-i').length === 3")
    box = ev("(() => { const r = document.getElementById('bcMenu').getBoundingClientRect(); "
             "const b = document.querySelector('.bc-fmts [data-fmt=xlsx]').getBoundingClientRect(); "
             "return [r.top, r.bottom, window.innerHeight, b.top, b.bottom]; })()")
    assert box[0] >= 0 and box[1] <= box[2], f"選單超出畫面：{box}"
    # 上面放得下就往上開，不靠捲動頁面把它拉進來（按鈕還在原本靠底部的位置）
    assert box[1] <= box[3] and box[4] > box[2] - 60, f"選單要開在按鈕上方：{box}"
    ev("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    assert not ev("!!document.getElementById('bcTable')"), "試算表選項移到按鈕的選單裡"
    shot("bc-export")
    assert _no_hscroll(send, ev) <= 390, "手機寬度不可以橫向捲動"
    shot("bc-review", 390)


def test_forgot_the_mode_switch_without_reuploading(site):
    """使用者 2026-11-13：一張圖片拍了好幾張、模式忘了選 → 校對畫面提示，按一下改成多張、停在確認範圍。"""
    port, send, info = site
    ev, wait, shot = _tools(send)
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/business-card-reader/"})
    assert wait("document.querySelectorAll('.bc-batch').length === 3")
    ev("[...document.querySelectorAll('.bc-batch')].find(r => r.textContent.includes('忘了選模式')).querySelector('button').click()")
    assert wait("!document.getElementById('bcReview').hidden && !document.getElementById('bcMultiHint').hidden"), \
        "單卡模式但一張圖片有好幾張：要提示"
    shot("bc-multihint")
    ev("document.getElementById('bcMultiHintBtn').click()")
    assert wait("!!document.querySelector('.modal-ok')")
    ev("document.querySelector('.modal-ok').click()")
    assert wait("!document.getElementById('bcFrames').hidden && document.querySelectorAll('#bcSvg polygon').length === 6", 60), \
        "改成多張後停在確認範圍、找出 6 張"
    assert ev("document.getElementById('bcReview').hidden") is True
    assert ev("!!document.getElementById('bcToSingle')")


_VCF_JS = r"""((sel, cards) => {
  const lines = [];
  cards.forEach(([n, e, m]) => { lines.push('BEGIN:VCARD', 'VERSION:3.0', 'FN:' + n, 'N:' + n + ';;;;', 'ORG:範例科技股份有限公司');
    if (e) lines.push('EMAIL;TYPE=INTERNET:' + e); if (m) lines.push('TEL;TYPE=CELL:' + m); lines.push('END:VCARD'); });
  const dt = new DataTransfer();
  dt.items.add(new File([lines.join('\r\n') + '\r\n'], 'previous.vcf', { type: 'text/vcard' }));
  const inp = document.querySelector(sel); inp.files = dt.files;
  inp.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
})"""


def test_book_import_dedupe_and_the_export_buttons_of_each_section(site):
    """使用者回報：名片簿的匯出按鈕不能按（這一批沒有可匯出的時候，連名片簿那組也被反灰；按名片簿的匯出
    還會連帶觸發這一批的匯出）。另外：名片簿匯入檔案自動去重、檢查重複、合併之前匯出的檔案一步一步走。資料杜撰。"""
    port, send, info = site
    ev, wait, shot = _tools(send)
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/business-card-reader/"})
    assert wait("document.querySelectorAll('.bc-batch').length >= 3")
    # 重現使用者的情況：這一批一位都還沒確認（沒有可匯出的）
    sid = info["single"]
    ev(f"""(async () => {{
      const b = await (await fetch('/tools/business-card-reader/api/batches/{sid}')).json();
      for (const c of b.contacts) await fetch('/tools/business-card-reader/api/batches/{sid}/contacts/' + c.id,
        {{method: 'PUT', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{status: 'check'}})}});
      return true; }})()""")
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/business-card-reader/?r=1#batch={sid}"})
    assert wait("!!document.getElementById('bcExport') && !document.getElementById('bcExport').hidden"
                " && document.getElementById('bcExSum').textContent.length > 0")
    # 客戶回報「掃描區的資料不能匯出」：一位都還沒確認時，範圍自動用「全部」，按鈕可以按
    assert wait("document.getElementById('bcScope').value === 'all' && !document.querySelector('#bcFmts .btn').disabled"), \
        "還沒確認任何一位時，匯出範圍要自動用「全部」"
    # 使用者自己選回「已確認」：沒有可匯出的 → 按鈕反灰、提示要明顯（名片簿那組不受影響，下面驗）
    ev("(() => { const s = document.getElementById('bcScope'); s.value = 'confirmed'; s.dispatchEvent(new Event('change')); })()")
    assert wait("document.querySelector('#bcFmts .btn').disabled === true")
    assert ev("document.getElementById('bcExSum').classList.contains('empty')"), "沒有可匯出時提示要明顯"
    # 匯出的五種格式平均分滿一整列
    ws = ev("[...document.querySelectorAll('#bcFmts .bc-fmt')].map(b => Math.round(b.getBoundingClientRect().width))")
    assert len(ws) == 5 and max(ws) - min(ws) <= 2, ws
    # 合併之前匯出的檔案：上傳完直接比對，四個步驟都出來，建議存成同格式
    ev(_VCF_JS + "('#bcLmFile', [['王小明', 'kevin@example.com', ''], ['林小華', 'hua@example.net', '']])")
    assert wait("!document.getElementById('bcLmPlan').hidden && !document.getElementById('bcLmDl').hidden", 30), \
        "上傳完要直接比對，不必再按"
    assert ev("document.getElementById('bcLmIntro').hidden") is True
    assert ev("document.querySelectorAll('#bcLmCounts .bc-lm-chip').length") >= 1
    assert ev("document.querySelector('#bcLmFmts .bc-fmt.rec').dataset.fmt") == "vcf"
    shot("bc-listmerge")
    ev("document.getElementById('bcLmDone').click()")
    assert wait("document.getElementById('bcLm').hidden && !document.getElementById('bcLmIntro').hidden")
    # 名片簿：匯入檔案 → 自動比對（檔案裡重複的那一列預設不加入）→ 匯入
    ev("document.getElementById('bcTabBooks').click()")
    assert wait("!document.getElementById('bcBookView').hidden && !document.getElementById('bkImportBtn').hidden")
    ev(_VCF_JS + "('#bkImportFile', [['林小華', 'hua@example.net', ''], ['陳大文', '', '0933-000-222'], "
                 "['林小華', 'hua@example.net', '0922-000-111']])")
    assert wait("!document.getElementById('bkImport').hidden && document.querySelectorAll('#bkImpItems .bc-lm-item').length === 3", 30)
    assert "2" in ev("document.getElementById('bkImpGoText').textContent"), "新增 2 位（重複的那列預設不加入）"
    shot("bc-book-import")
    ev("document.getElementById('bkImpGo').click()")
    assert wait("document.getElementById('bkImport').hidden && /2/.test(document.getElementById('bkCount').textContent)", 30)
    # 名片簿的匯出按鈕可以按，而且只開名片簿自己的選單（不會連帶開這一批的）
    assert ev("[...document.querySelectorAll('#bkFmts .btn')].every(b => !b.disabled)"), "名片簿有人時匯出要能按"
    ev("document.querySelector('#bkFmts [data-fmt=xlsx]').click()")
    assert wait("document.querySelectorAll('#bcMenu').length === 1")
    assert ev("document.querySelectorAll('.bc-menu').length") == 1
    ev("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    # VCF 直接下載：只可以打名片簿的匯出，不可以連帶打這一批的（以前按鈕事件綁到三組，會兩個都下載）
    ev("""(() => { window.__reqs = []; const f = window.fetch;
      window.fetch = (u, o) => { window.__reqs.push(String(u)); return f(u, o); }; return true; })()""")
    ev("document.querySelector('#bkFmts [data-fmt=vcf]').click()")
    assert wait("window.__reqs.some(u => /\\/books\\/[^/]+\\/export/.test(u))", 20), "名片簿的 VCF 要打名片簿的匯出"
    time.sleep(1.0)
    assert not ev("window.__reqs.some(u => /\\/batches\\/[^/]+\\/export/.test(u))"), "按名片簿的匯出不可以連帶匯出這一批"
    # 同一個檔案再匯入一次、全部硬是「加成新的一位」→ 檢查重複找得到，合併之後就沒了
    ev(_VCF_JS + "('#bkImportFile', [['林小華', 'hua@example.net', ''], ['陳大文', '', '0933-000-222']])")
    assert wait("!document.getElementById('bkImport').hidden && document.querySelectorAll('#bkImpItems select').length === 2", 30)
    assert ev("[...document.querySelectorAll('#bkImpItems .k')].every(k => k.textContent.length > 0)")
    ev("document.querySelectorAll('#bkImpItems select').forEach(s => { s.value = 'add'; s.dispatchEvent(new Event('change')); })")
    ev("document.getElementById('bkImpGo').click()")
    assert wait("document.getElementById('bkImport').hidden && /4/.test(document.getElementById('bkCount').textContent)", 30)
    ev("document.getElementById('bkDupBtn').click()")
    assert wait("!document.getElementById('bkDup').hidden && document.querySelectorAll('#bkDupList .bk-dup-group').length === 2", 30)
    shot("bc-book-dup")
    ev("document.querySelector('#bkDupList .bk-dup-group button').click()")
    assert wait("document.querySelectorAll('#bkDupList .bk-dup-group').length === 1", 30)


def test_camera_buttons_only_on_touch_devices(site):
    """「拍照」在電腦上跟「選擇檔案」一模一樣（電腦沒有相機選項），「連拍」會開電腦的攝影機 —— 這兩顆只在
    手機、平板顯示；電腦上用「改用手機拍（掃 QR Code）」（使用者 2026-11-14：用途看不懂）。"""
    port, send, info = site
    ev, wait, shot = _tools(send)
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/business-card-reader/"})
    assert wait("!!document.getElementById('bcCamera') && document.querySelectorAll('.bc-batch').length > 0")
    assert ev("document.getElementById('bcCamera').hidden && document.getElementById('bcBurst').hidden"), \
        "電腦上不顯示拍照 / 連拍"
    assert "QR" in ev("document.getElementById('bcHandoff').textContent")
    send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
    send("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 800, "deviceScaleFactor": 2, "mobile": True})
    try:
        send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/business-card-reader/?m=1"})
        assert wait("!!document.getElementById('bcCamera') && document.querySelectorAll('.bc-batch').length > 0")
        assert ev("matchMedia('(pointer: coarse)').matches"), "觸控模擬沒有生效"
        assert ev("!document.getElementById('bcCamera').hidden"), "手機上要有「拍照」"
    finally:
        send("Emulation.setTouchEmulationEnabled", {"enabled": False})
        send("Emulation.clearDeviceMetricsOverride")


def test_fill_a_blank_field_add_another_mobile_and_an_english_title(site):
    """客戶回報（2026-11-19）：掃描後在空白欄位輸入、按儲存出現「請求格式錯誤（400）：欄位狀態不正確」；
    兩支手機手動加不太上去；英文職稱不知道放哪裡。資料杜撰。"""
    port, send, info = site
    ev, wait, shot = _tools(send)
    sid = info["single"]
    api = f"/tools/business-card-reader/api/batches/{sid}"
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/business-card-reader/?r=2#batch={sid}"})
    assert wait("!!document.querySelector('#bcFields .bc-frow[data-key=mobile]') && !document.getElementById('bcReview').hidden")
    # 先把手機刪掉存起來 → 重新整理後「手機」是預先列出的空白格
    ev("document.querySelector('#bcFields .bc-frow[data-key=mobile] .bc-fdel:not(.bc-fmore)').click()")
    ev("document.getElementById('bcSave').click()")
    assert wait("(() => { const r = document.querySelector('#bcFields .bc-frow[data-key=mobile]'); "
                "return r && r.querySelector('input').value === ''; })()"), "刪掉之後要列出空白的手機欄"
    ev("""(() => { const i = document.querySelector('#bcFields .bc-frow[data-key=mobile] input');
      i.value = '0911-222-333'; i.dispatchEvent(new Event('input', {bubbles: true})); return true; })()""")
    ev("window.__errs = []; window.showAlert = (m) => window.__errs.push(String(m)); true")
    ev("document.getElementById('bcSave').click()")
    assert wait("document.getElementById('bcDirty').hidden"), "空白欄位填了要存得進去"
    assert ev("window.__errs") == [], ev("window.__errs")
    cid = ev(f"(async () => (await (await fetch('{api}')).json()).contacts[0].id)()")
    mob = lambda: ev(f"(async () => (await (await fetch('{api}')).json()).contacts.find(c => c.id === '{cid}')"
                     ".fields.filter(f => f.key === 'mobile' && f.status !== 'deleted').map(f => f.value))()")
    assert mob() == ["0911-222-333"]
    # 手機那一列的「＋」：再加一支
    assert ev("!document.querySelector('#bcFields .bc-frow[data-key=mobile] .bc-fmore').hidden")
    assert ev("!!document.querySelector('#bcFields .bc-frow[data-key=mobile] .bc-fmore svg')"), "＋ 要有圖示（不是空按鈕）"
    assert ev("document.querySelector('#bcFields .bc-frow[data-key=name] .bc-fmore').hidden"), "姓名只有一個，不給＋"
    ev("document.querySelector('#bcFields .bc-frow[data-key=mobile] .bc-fmore').click()")
    assert wait("document.querySelectorAll('#bcFields .bc-frow[data-key=mobile]').length === 2")
    assert ev("document.activeElement === document.querySelectorAll('#bcFields .bc-frow[data-key=mobile] input')[1]"), \
        "游標要在新的那一格"
    ev("""(() => { const i = document.activeElement; i.value = '0922-444-555';
      i.dispatchEvent(new Event('input', {bubbles: true})); return true; })()""")
    # 外文職稱：從「新增欄位」加
    assert ev("[...document.getElementById('bcAddKey').options].some(o => o.value === 'title_en')")
    ev("(() => { const s = document.getElementById('bcAddKey'); s.value = 'title_en'; return true; })()")
    ev("document.getElementById('bcAddField').click()")
    assert wait("!!document.querySelector('#bcFields .bc-frow[data-key=title_en] input')")
    ev("""(() => { const i = document.querySelector('#bcFields .bc-frow[data-key=title_en] input'); i.value = 'Sales Manager';
      i.dispatchEvent(new Event('input', {bubbles: true})); return true; })()""")
    shot("bc-add-fields")
    ev("document.getElementById('bcSave').click()")
    assert wait("document.getElementById('bcDirty').hidden")
    assert ev("window.__errs") == []
    assert mob() == ["0911-222-333", "0922-444-555"]
    assert ev(f"(async () => (await (await fetch('{api}')).json()).contacts.find(c => c.id === '{cid}')"
              ".fields.filter(f => f.key === 'title_en').map(f => f.value))()") == ["Sales Manager"]


def test_frames_move_scale_rotate_with_a_real_mouse(site):
    """客戶回報（2026-11-20）：確認範圍的框拖不動（按下時重畫、被按的元素換掉，之後的移動收不到）；
    新增範圍太小；要能等比例縮放、要能旋轉。用真的滑鼠事件（Input.dispatchMouseEvent）驗。"""
    import math
    port, send, info = site
    ev, wait, shot = _tools(send)
    mid = info["multi"]
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/business-card-reader/?r=7#batch={mid}"})
    assert wait("!!document.querySelector('#bcSvg polygon') && !document.getElementById('bcFrames').hidden", 60)
    ev("document.getElementById('bcStage').scrollIntoView({block: 'start'}); true")
    time.sleep(0.8)

    def rect(sel):
        return ev(f"(() => {{ const r = document.querySelector('{sel}').getBoundingClientRect(); "
                  "return [r.x + r.width / 2, r.y + r.height / 2]; })()")

    def drag(x, y, x2, y2, steps=4):
        send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1})
        for n in range(1, steps + 1):
            send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x + (x2 - x) * n / steps,
                                              "y": y + (y2 - y) * n / steps, "button": "left", "buttons": 1})
        send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x2, "y": y2, "button": "left", "buttons": 0, "clickCount": 1})
        time.sleep(0.3)

    quad = lambda i=0: ev(f"S.edits[S.batch.pages[S.page].id][{i}].quad") if ev("typeof S !== 'undefined'") else None
    pts = lambda: [[float(v) for v in p.split(",")] for p in ev("document.querySelectorAll('#bcSvg polygon')[0].getAttribute('points')").split()]
    before = pts()
    cx, cy = rect("#bcSvg polygon")
    drag(cx, cy, cx + 40, cy + 25)
    after = pts()
    assert ev("document.getElementById('bcSelName').textContent") != "—", "按一下要選到這個框"
    assert after != before and abs((after[0][0] - before[0][0]) - (after[2][0] - before[2][0])) < 1, \
        "第一次按下去就要拖得動，而且整個框一起移動（不變形）"
    # 等比例縮放（預設）：拉一個角，對角不動、長寬比不變
    assert ev("document.getElementById('bcKeepRatio').checked") is True
    hx, hy = rect("#bcSvg circle.h")
    drag(hx, hy, hx - 30, hy - 30)
    sc = pts()
    w0, h0 = math.dist(after[0], after[1]), math.dist(after[1], after[2])
    w1, h1 = math.dist(sc[0], sc[1]), math.dist(sc[1], sc[2])
    assert sc[2] == after[2] and w1 > w0 and abs(w1 / h1 - w0 / h0) < 0.01, (after, sc)
    # 取消等比例：只動那一個角
    ev("document.getElementById('bcKeepRatio').click(); true")
    hx, hy = rect("#bcSvg circle.h")
    drag(hx, hy, hx + 15, hy + 5)
    fr = pts()
    assert fr[1:] == sc[1:] and fr[0] != sc[0]
    ev("document.getElementById('bcKeepRatio').click(); true")
    # 旋轉圓點：轉了以後上緣不再水平、框的大小不變
    rx, ry = rect("#bcSvg circle.rh")
    c = [sum(p[0] for p in fr) / 4, sum(p[1] for p in fr) / 4]
    drag(rx, ry, rx + 60, ry + 10)
    ro = pts()
    assert abs(ro[0][1] - ro[1][1]) > 3, "旋轉後上緣要斜"
    assert abs(math.dist(ro[0], ro[2]) - math.dist(fr[0], fr[2])) < 1, "旋轉不改變大小"
    assert abs(sum(p[0] for p in ro) / 4 - c[0]) < 1, "繞中心轉"
    time.sleep(3.5)
    assert pts() == ro, "改過的框不可以過幾秒就跳回原位（原本畫面每 1.5 秒重讀、清掉改過的框）"
    # 新增範圍：跟這一頁其他框差不多大（不是頁寬的三成）
    n = ev("document.querySelectorAll('#bcSvg polygon').length")
    ev("document.getElementById('bcAddBox').click(); true")
    assert wait(f"document.querySelectorAll('#bcSvg polygon').length === {n + 1}")
    sizes = ev("[...document.querySelectorAll('#bcSvg polygon')].map(p => p.getBBox().width)")
    others = sorted(sizes[:-1])
    assert abs(sizes[-1] - others[len(others) // 2]) / others[len(others) // 2] < 0.25, sizes
    shot("bc-frames-edit")


def test_japanese_language_choice_and_retry_in_another_language(site):
    """日文名片（2026-11-20）：收件區有辨識語言、校對區「改用日文辨識」送出 `{"lang": "ja"}`。
    這裡的伺服器沒有假 OCR，重新辨識那一下用攔截的 fetch 驗送出的內容，不真的辨識。"""
    port, send, info = site
    ev, wait, shot = _tools(send)
    sid = info["single"]
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/business-card-reader/?r=8#batch={sid}"})
    assert wait("!!document.querySelector('#bcFields .bc-frow') && !document.getElementById('bcReview').hidden")
    assert ev("document.querySelectorAll('input[name=bcOcrLang]').length") == 2
    assert ev("document.querySelector('input[name=bcOcrLang][value=zh]').checked") is True
    assert "繁中" in ev("document.getElementById('bcBatchLangName').textContent")
    assert wait("document.getElementById('bcRetryLangText').textContent.length > 0")
    assert "日文" in ev("document.getElementById('bcRetryLangText').textContent")
    ev("""(() => { window.__sent = null; const f = window.fetch;
      window.fetch = (u, o) => { if (/\\/retry$/.test(String(u))) { window.__sent = [String(u), o && o.body];
        return Promise.resolve(new Response('{"ok":true}', {status: 200, headers: {'Content-Type': 'application/json'}})); }
        return f(u, o); }; return true; })()""")
    ev("document.getElementById('bcRetryLang').click()")
    assert wait("!!document.querySelector('.modal-ok')")
    ev("document.querySelector('.modal-ok').click()")
    assert wait("window.__sent !== null")
    url, body = ev("window.__sent")
    assert url.endswith("/retry") and '"lang":"ja"' in body.replace(" ", "")
    shot("bc-lang")
