"""加密紙本備份：離線還原包（雙擊打開的單一 HTML）真瀏覽器測試。

* 從網站下載的離線包直接用 `file://` 打開（不經過任何伺服器），自我測試通過。
* 拖進主文件 PDF＋一份分片 PDF＋一份分片照片（PNG），還原出來跟原文一樣。
* 內嵌檔被改過一個字元 → 啟動時核對 SHA-256 失敗、顯示警告、不載入任何程式。
* 同一版本同一語言下載兩次位元組相同（可以公開 SHA-256 讓人核對）。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests.test_doc_straighten_quad_overlay_e2e import _browser, _free_port  # noqa: E402
from tools.browser_probe import profile_arg as _profile_arg  # noqa: E402
from tools.browser_probe import uploadable_dir as _uploadable_dir  # noqa: E402

CHROME = "/usr/bin/chromium-browser"
pytestmark = pytest.mark.skipif(not Path(CHROME).exists() or not __import__("shutil").which("node"),
                                reason="沒有 chromium 或 node")


@pytest.fixture(scope="module")
def cdp_tab():
    br_path = _browser()
    if not br_path:
        pytest.skip("沒有 chromium")
    cdp = _free_port()
    br = subprocess.Popen([br_path, "--headless=new", "--no-sandbox", "--disable-gpu", _profile_arg(),
                           f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cdp}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
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
                m = json.loads(ws.recv(timeout=300))
                if m.get("id") == i:
                    return m
        send("Page.enable"); send("Runtime.enable"); send("DOM.enable")
        yield send
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        br.terminate()
        try:
            br.wait(timeout=6)
        except Exception:
            br.kill()


def _helpers(send):
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
    return ev, wait


@pytest.fixture(scope="module")
def pack(client):
    from app.core import auth_settings
    s = auth_settings.get()
    if s.get("backend") != "off":
        pytest.skip("測試環境已啟用登入")
    r = client.get("/paper-backup/offline")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")
    again = client.get("/paper-backup/offline")
    assert again.content == r.content, "同一版本同一語言的離線包位元組要固定"
    d = Path(_uploadable_dir())
    p = d / "pb-offline.html"
    p.write_bytes(r.content)
    return p


def _material(tmp_dir: Path, secret: bytes) -> list[str]:
    import fitz
    p = {"kind": 1, "name": "", "content_b64": base64.b64encode(secret).decode(), "t": 2, "n": 3,
         "density": "medium", "out": str(tmp_dir)}
    r = subprocess.run(["node", "--no-warnings", "--experimental-loader", "./tests/js/pb_loader.mjs", "tests/js/pb_cli.mjs",
                        "pdf", json.dumps(p)], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-1500:]
    with fitz.open(tmp_dir / "share-3.pdf") as d:
        d[0].get_pixmap(dpi=200).save(str(tmp_dir / "share-3-photo.png"))
    return [str(tmp_dir / "main.pdf"), str(tmp_dir / "share-1.pdf"), str(tmp_dir / "share-3-photo.png")]


def test_offline_pack_restores_from_file_url(cdp_tab, pack):
    send = cdp_tab
    ev, wait = _helpers(send)
    work = Path(_uploadable_dir()) / "pb-offline-material"
    work.mkdir(exist_ok=True)
    secret = "離線還原測試：the quick brown fox 1234".encode()
    files = _material(work, secret)

    send("Page.navigate", {"url": pack.as_uri()})
    assert wait("document.querySelectorAll('#pbCodes input').length === 16", 60), \
        ("離線包沒有啟動", ev("document.getElementById('pbBoot') && document.getElementById('pbBoot').textContent"))
    assert ev("location.protocol") == "file:"
    assert ev("document.getElementById('pbBoot').hidden") is True

    ev("document.getElementById('pbSelfTest').click()")
    assert wait("document.getElementById('pbSelfMsg').textContent.length > 0", 30)
    assert ev("document.getElementById('pbSelfMsg').textContent") == "自我測試通過：內建的測試資料可以正確還原"

    doc = send("DOM.getDocument", {"depth": -1})["result"]
    node = send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#pbFiles"})["result"]
    send("DOM.setFileInputFiles", {"files": files, "nodeId": node["nodeId"]})
    assert wait("!document.getElementById('pbRestoreGo').disabled", 180), \
        ("讀不齊", ev("document.getElementById('pbLog').innerText"))
    ev("document.getElementById('pbRestoreGo').click()")
    assert wait("!document.getElementById('pbResult').hidden", 60), ev("document.getElementById('pbRestoreMsg').textContent")
    ev("document.getElementById('pbResultShow').click()")
    assert ev("document.getElementById('pbResultText').value") == secret.decode()


def test_tampered_pack_refuses_to_start(cdp_tab, pack):
    send = cdp_tab
    ev, wait = _helpers(send)
    html = pack.read_text(encoding="utf-8")
    start = html.index('"codec":{"sha256":')
    b64_at = html.index('"b64":"', start) + len('"b64":"') + 40
    bad = html[:b64_at] + ("A" if html[b64_at] != "A" else "B") + html[b64_at + 1:]
    p = pack.with_name("pb-offline-tampered.html")
    p.write_text(bad, encoding="utf-8")
    send("Page.navigate", {"url": p.as_uri()})
    assert wait("location.href.endsWith('pb-offline-tampered.html') && !!document.getElementById('pbBoot') && !document.getElementById('pbBoot').hidden", 30), \
        "改過的離線包沒有被擋下"
    assert "codec" in ev("document.getElementById('pbBoot').textContent")
    time.sleep(1)
    assert ev("document.querySelectorAll('#pbCodes input').length") == 0, "核對失敗後不可以載入任何程式"
