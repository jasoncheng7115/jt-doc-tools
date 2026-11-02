"""掃描拼合「排列方式」：在真的瀏覽器裡切換，畫面上的位置真的變。

兩份掃描的內容落在同一個位置：
* 預設「依原始位置」→ 兩塊疊在一起、出現重疊警告；
* 選「自動排列」→ 互不重疊、警告消失；
* 再切回「依原始位置」→ 回到原本的位置（不是停在自動排列的結果）。

另外驗「預覽與微調」卡片的出現時機（使用者 2026-11-02）：要等拉進來的檔案都處理完、
上傳區的預覽圖都載入了才顯示，不可以一拉檔就先打開一張空白卡片。

判準用畫面上元素的實際位置（`getBoundingClientRect`），不是只看資料。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools.browser_probe import (  # noqa: E402
    browser as _browser,
    uploadable_dir as _uploadable_dir,
)
from tools.browser_probe import profile_arg as _profile_arg  # noqa: E402

pytestmark = pytest.mark.skipif(
    _browser() is None or __import__("importlib").util.find_spec("websockets") is None,
    reason="沒有 chromium / websockets —— 這條要真的瀏覽器才驗得到")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _scan(path: str) -> None:
    from PIL import Image
    img = Image.new("RGB", (600, 850), (255, 255, 255))
    img.paste(Image.new("RGB", (300, 180), (40, 90, 200)), (60, 50))
    img.save(path)


@pytest.fixture(scope="module")
def page():
    data = tempfile.mkdtemp(prefix="smlayout-")
    port, cdp = _free_port(), _free_port()
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen(
        [_browser(), "--headless=new", "--no-sandbox", "--disable-gpu",
         _profile_arg(), f"--remote-debugging-port={cdp}", "--remote-allow-origins=*",
         "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        req = urllib.request.Request(
            f"http://127.0.0.1:{cdp}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            tab = json.loads(r.read())
        ws = wsc.connect(tab["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
        n = [0]

        def send(method, params=None):
            n[0] += 1
            i = n[0]
            ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
            while True:
                m = json.loads(ws.recv(timeout=180))
                if m.get("id") == i:
                    return m

        def ev(expr):
            r = send("Runtime.evaluate",
                     {"expression": expr, "returnByValue": True, "awaitPromise": True})
            res = r.get("result", {})
            if "exceptionDetails" in res:
                exc = res["exceptionDetails"].get("exception", {}).get("description")
                raise AssertionError(f"頁面丟例外：{str(exc)[:300]}")
            return res.get("result", {}).get("value")

        def wait(expr, secs=30):
            for _ in range(secs * 4):
                v = ev(expr)
                if v:
                    return v
                time.sleep(0.25)
            return ev(expr)

        send("Runtime.enable")
        send("Page.enable")
        send("DOM.enable")

        def fresh():
            send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/scan-merge/"})
            time.sleep(1)
            assert wait("document.readyState === 'complete' && typeof crops !== 'undefined'"
                        " && crops.length === 0")
            time.sleep(0.3)

        yield send, ev, wait, fresh
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


_RECTS = """(() => Array.from(document.querySelectorAll('#stage .sm-item')).map(el => {
  const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom];
}))()"""


def _overlap(a, b):
    return not (a[2] <= b[0] + 0.5 or b[2] <= a[0] + 0.5 or a[3] <= b[1] + 0.5 or b[3] <= a[1] + 0.5)


def _upload_two(send):
    d = _uploadable_dir()
    paths = [os.path.join(d, "sm_front.png"), os.path.join(d, "sm_back.png")]
    for p in paths:
        _scan(p)
    doc = send("DOM.getDocument", {"depth": -1})
    node = send("DOM.querySelector", {"nodeId": doc["result"]["root"]["nodeId"],
                                      "selector": "#fileInput"})
    send("DOM.setFileInputFiles", {"files": paths, "nodeId": node["result"]["nodeId"]})


# 卡片從隱藏變成顯示的那一刻，記下上傳區的狀態
_WATCH_PANEL = """(() => {
  window.__panelShownAt = null;
  window.__sawUploadBusy = false;
  const panel = document.getElementById('stage-panel');
  const upBusy = document.getElementById('upload-busy');
  new MutationObserver(() => { if (!upBusy.hidden) window.__sawUploadBusy = true; })
    .observe(upBusy, { attributes: true, attributeFilter: ['hidden'] });
  new MutationObserver(() => {
    if (panel.hidden || window.__panelShownAt) return;
    const imgs = Array.from(document.querySelectorAll('#upload-list img'));
    window.__panelShownAt = {
      sawBusy: window.__sawUploadBusy,
      sources: sources.length,
      imgs: imgs.length,
      loaded: imgs.filter(i => i.complete && i.naturalWidth > 0).length,
      busyHidden: document.getElementById('upload-busy').hidden,
    };
  }).observe(panel, { attributes: true, attributeFilter: ['hidden'] });
  return panel.hidden;
})()"""


def test_stage_panel_waits_until_all_previews_are_loaded(page):
    send, ev, wait, fresh = page
    fresh()
    assert ev(_WATCH_PANEL) is True, "還沒上傳時「預覽與微調」卡片不可以顯示"
    _upload_two(send)
    snap = wait("window.__panelShownAt", 60)
    assert snap, "處理完之後卡片沒有顯示"
    assert snap["sawBusy"], f"處理中時上傳區沒有出現進度提示：{snap}"
    assert snap["sources"] == 2 and snap["imgs"] == 2, f"卡片在檔案處理完之前就出現了：{snap}"
    assert snap["loaded"] == 2, f"卡片在預覽圖載入之前就出現了：{snap}"
    assert snap["busyHidden"], f"卡片出現時處理中提示還在：{snap}"


def test_switching_layout_mode_moves_the_blocks(page):
    send, ev, wait, fresh = page
    fresh()
    _upload_two(send)
    assert wait("crops.length === 2 && document.querySelectorAll('#stage .sm-item').length === 2"
                " && !document.getElementById('stage-panel').hidden", 60)

    # 預設：依原始位置 → 疊在一起、有警告
    assert ev("document.querySelector('input[name=smLayout]:checked').value") == "original"
    a, b = ev(_RECTS)
    assert _overlap(a, b), "兩份掃描同一位置，依原始位置時應該疊在一起"
    assert ev("!document.getElementById('overlap-warn').hidden")
    orig = ev("JSON.stringify(crops.map(c => [c.x, c.y, c.w, c.h]))")

    # 自動排列 → 不重疊、警告消失
    ev("document.querySelector('input[name=smLayout][value=auto]').closest('label').click()")
    assert wait("document.getElementById('overlap-warn').hidden", 20), "切到自動排列後還有重疊警告"
    a, b = ev(_RECTS)
    assert not _overlap(a, b), f"自動排列後畫面上仍重疊：{a} {b}"
    # 選取的那一個要看得出來（有底色）
    sel = ev("getComputedStyle(document.querySelector('input[name=smLayout][value=auto]').closest('label')).backgroundColor")
    assert sel not in ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)"), sel

    # 切回依原始位置 → 回到原本的位置
    ev("document.querySelector('input[name=smLayout][value=original]').closest('label').click()")
    assert wait("!document.getElementById('overlap-warn').hidden", 20)
    assert ev("JSON.stringify(crops.map(c => [c.x, c.y, c.w, c.h]))") == orig
