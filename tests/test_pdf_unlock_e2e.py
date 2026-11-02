"""有開啟密碼的 PDF：在真的瀏覽器裡拉進去，跳出密碼視窗、解開後照常往下走。

使用者 2026-11-02：拉進有密碼的 PDF，用印頁什麼反應都沒有。

* 共用上傳元件的工具（PDF 壓縮）：跳出密碼視窗（輸入框是密碼型、附「PDF 密碼解除」連結）
  → 打錯會說密碼不正確、再問一次 → 打對後上傳框裡的檔換成解開的那份；
* 只限制列印 / 編輯的 PDF（只有擁有者密碼）：不跳視窗，照常收下；
* 自己寫上傳框的工具（掃描拼合）：同樣跳視窗，解開後照常處理。
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

PW = "open-1234"


def _write_pdf(path: str, user_pw: str = "", owner_pw: str = "") -> None:
    import fitz
    d = fitz.open()
    page = d.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(60, 60, 320, 220), color=None, fill=(0.2, 0.4, 0.8))
    d.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=user_pw,
           owner_pw=owner_pw or (user_pw + "-owner"), permissions=fitz.PDF_PERM_ACCESSIBILITY)
    d.close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def page():
    data = tempfile.mkdtemp(prefix="pdfunlock-")
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

        def goto(path):
            send("Page.navigate", {"url": f"http://127.0.0.1:{port}{path}"})
            time.sleep(1)
            assert wait("document.readyState === 'complete' && !!window.unlockPdfFiles")
            time.sleep(0.3)

        def set_file(selector, path):
            doc = send("DOM.getDocument", {"depth": -1})
            node = send("DOM.querySelector", {"nodeId": doc["result"]["root"]["nodeId"],
                                              "selector": selector})
            send("DOM.setFileInputFiles", {"files": [path], "nodeId": node["result"]["nodeId"]})

        send("Runtime.enable")
        send("Page.enable")
        send("DOM.enable")
        yield goto, ev, wait, set_file
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


_PROMPT_OPEN = "!!document.querySelector('.modal-overlay .modal-input')"


def _answer(ev, password):
    ev("(() => { const i = document.querySelector('.modal-overlay .modal-input');"
       f" i.value = {json.dumps(password)}; document.querySelector('.modal-overlay .modal-ok').click(); }})()")


_FILE_IS_LOCKED = """(async () => {
  const f = document.querySelector('input[type=file]').files[0];
  if (!f) return null;
  const t = new TextDecoder('latin1').decode(await f.arrayBuffer());
  return t.indexOf('/Encrypt') >= 0;
})()"""


def test_shared_upload_asks_for_the_password_and_swaps_in_the_unlocked_file(page):
    goto, ev, wait, set_file = page
    path = os.path.join(_uploadable_dir(), "鎖住的合約.pdf")
    _write_pdf(path, user_pw=PW)
    goto("/tools/pdf-compress/")
    set_file("input[type=file]", path)
    assert wait(_PROMPT_OPEN, 20), "有開啟密碼的 PDF 沒有跳出密碼視窗"
    info = ev("""(() => {
      const ov = document.querySelector('.modal-overlay');
      const a = ov.querySelector('.modal-body a');
      return { type: ov.querySelector('.modal-input').type, body: ov.querySelector('.modal-body').textContent,
               link: a ? a.getAttribute('href') : null, target: a ? a.getAttribute('target') : null };
    })()""")
    assert info["type"] == "password", info
    assert "鎖住的合約.pdf" in info["body"], info
    assert info["link"] == "/tools/pdf-decrypt/", f"要附「PDF 密碼解除」的連結：{info}"
    # 連結要開新分頁：DOMPurify 預設會拿掉 target，一點就離開目前的工具（截圖時才發現）
    assert info["target"] == "_blank", info

    _answer(ev, "wrong-one")
    assert wait("(() => { const b = document.querySelector('.modal-overlay .modal-body');"
                " return !!b && b.textContent.indexOf('密碼不正確') >= 0; })()", 20), "密碼錯了沒有說"
    _answer(ev, PW)
    assert wait("!document.querySelector('.modal-overlay')", 20)
    assert wait(f"(async () => (await {_FILE_IS_LOCKED}) === false)()", 20), "上傳框裡的檔沒有換成解開的那份"
    assert wait("(document.querySelector('.drop-zone-filename') || {}).textContent === '鎖住的合約.pdf'", 10)
    os.remove(path)


def test_owner_only_pdf_goes_straight_through(page):
    goto, ev, wait, set_file = page
    path = os.path.join(_uploadable_dir(), "只限制列印.pdf")
    _write_pdf(path, owner_pw="owner-only")
    goto("/tools/pdf-compress/")
    set_file("input[type=file]", path)
    assert wait("(document.querySelector('.drop-zone-filename') || {}).textContent === '只限制列印.pdf'", 20)
    time.sleep(1.5)
    assert not ev(_PROMPT_OPEN), "只限制列印的 PDF 打得開，不可以跳密碼視窗"
    os.remove(path)


def test_custom_upload_tool_also_asks(page):
    goto, ev, wait, set_file = page
    path = os.path.join(_uploadable_dir(), "鎖住的證件.pdf")
    _write_pdf(path, user_pw=PW)
    goto("/tools/scan-merge/")
    set_file("#fileInput", path)
    assert wait(_PROMPT_OPEN, 20), "掃描拼合沒有跳出密碼視窗"
    _answer(ev, PW)
    assert wait("sources.length === 1 && crops.length >= 1", 60), "解開之後沒有照常處理"
    os.remove(path)
