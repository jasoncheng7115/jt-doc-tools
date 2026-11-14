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
    assert ev("document.querySelectorAll('#bcSvg circle').length") == 4, "選取的框顯示四個角"
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
    assert wait("document.querySelectorAll('#bcMenu .bc-menu-i').length === 2")
    assert ev("document.querySelector('#bcMenu .bc-menu-i .bc-rec') !== null")
    shot("bc-export-menu")
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
    assert wait("document.querySelectorAll('#bcMenu .bc-menu-i').length === 2")
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
