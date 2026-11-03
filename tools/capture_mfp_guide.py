#!/usr/bin/env python3
"""介紹站「事務機掃描取件」使用說明頁的截圖（中英日各一組）。

**對著假設備跑真的流程**：測試裡的假 FUJIFILM 信箱（WebDAV）、假 RICOH 文件伺服器（SOAP，含資料夾與資料夾密碼）、
假 eSCL 掃描器，跟瀏覽器測試是同一套。實例是拋棄式的（未啟用登入、從本機連），啟動時只在**這個行程裡**
放行 127.0.0.x 與假設備的埠（正式程式碼沒有放行開關）；連線測試、列文件、開資料夾、掃描、取件都是本站真的在做。

截圖前把畫面上的本機位址換成 `example.com` 的主機名稱、埠號換成 443 / 80 —— 截圖是公開的，
本機位址與測試埠在畫面上沒有意義也不好看。名稱、檔名都是英文（三種語言的截圖共用一批設備資料）。

用法：
    python tools/capture_mfp_guide.py            # 三種語言都拍
    python tools/capture_mfp_guide.py --locale en
輸出：`github/docs/screenshots/mfp-guide/<名字>.png`（中文）與 `.../<語言>/mfp-guide/<名字>.png`
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.test_mfp_box import FakeBox, tiff_bytes  # noqa: E402
from tests.test_mfp_escl import Fake as FakeEscl  # noqa: E402
from tests.test_mfp_fetch_page_browser import _Tab, _browser, _free_port, _open, _profile_arg  # noqa: E402
from tests.test_mfp_soap import FakeRicoh, _CUSTOMER_CLASSES  # noqa: E402
from tools.repo_paths import public_root  # noqa: E402

WIDTH, HEIGHT = 1400, 1020

_LAUNCH = """
import sys
sys.path.insert(0, {root!r})
import app.core.mfp_client as m
_real = m.check_address
m.check_address = lambda ip: None if ip in ("127.0.0.1", "127.0.0.2") else _real(ip)
m.ALLOWED_PORTS = tuple(m.ALLOWED_PORTS) + tuple({ports!r})
import app.core.mfp_escl as e
e.STATUS_POLL = 0.1
e.PAGE_POLL = 0.1
sys.path.insert(0, {tools!r})
import seed_demo_data
seed_demo_data.seed_users_and_groups()
import uvicorn
uvicorn.run("app.main:app", host="127.0.0.1", port={port}, log_level="warning")
"""

FUJI_HOME = ("<html><head><title>Apeos C5571 - FUJIFILM Business Innovation</title></head>"
             "<body>Internet Services</body></html>").encode()
RICOH_HOME = b"<html><head><title>Web Image Monitor</title></head><body>RICOH IM C3510</body></html>"


def _out(locale: str) -> Path:
    base = public_root(REPO) / "docs" / "screenshots"
    return (base if locale == "zh-Hant" else base / locale) / "mfp-guide"


def _wait(url: str, tries: int = 120) -> bool:
    for _ in range(tries):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _api(base: str, method: str, path: str, body=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def _fakes():
    box = FakeBox()
    box.home = FUJI_HOME
    box.realm = "box-1"
    box.add_whole(5, data=tiff_bytes(3), name="Contract-signed", Pages="3")
    box.add_whole(6, data=tiff_bytes(2), name="Invoice-0421", Pages="2")
    box_srv = box.serve()
    box.port = box_srv.server_address[1]

    ricoh = type("R", (FakeRicoh,), {"handle_get": lambda self, path, headers: (
        (200, RICOH_HOME, "text/html; charset=utf-8") if path in ("/", "/index.html")
        else FakeRicoh.handle_get(self, path, headers))})()
    ricoh.auth_url_host = ricoh.url_host_override = "127.0.0.2"
    ricoh.docs = {
        "doc-1": {"title": "Quotation-0421", "elements": ["e1", "e2"], "scheme": "http+get", "locked": False},
        "doc-2": {"title": "Meeting-notes", "elements": ["e3"], "scheme": "http+get", "locked": False},
    }
    ricoh.classes = _CUSTOMER_CLASSES
    ricoh.folders = {
        "fid=1": {"title": "Finance", "locked": True, "password": "001234",
                  "docs": {"f1-doc": {"title": "Expense-claims", "elements": ["e9"], "scheme": "http+get", "locked": False},
                           "f1-doc2": {"title": "Purchase-order", "elements": ["e7"], "scheme": "http+get", "locked": False}}},
        "fid=2": {"title": "Sales", "locked": False, "password": "",
                  "docs": {"f2-doc": {"title": "Business-trip", "elements": ["e8"], "scheme": "http+get", "locked": False}}},
    }
    ricoh.start("127.0.0.2")

    escl = FakeEscl()
    escl_srv = escl.serve()
    escl.port = escl_srv.server_address[1]
    return box, box_srv, ricoh, escl, escl_srv


#: 畫面上的位址換成範例主機名稱（截圖前跑；文字節點與輸入框都換）
def _rewrite_js(mapping: dict) -> str:
    return """(() => {
      const map = %s;
      const fix = (s) => {
        for (const [k, v] of Object.entries(map)) s = s.split(k).join(v);
        return s.replace(/\\b(https?):(\\d{4,5})\\b/g, (m, sch) => sch + ':' + (sch === 'https' ? '443' : '80'))
                .replace(/\\b(HTTPS?) (\\d{4,5})\\b/g, (m, sch) => sch + ' ' + (sch === 'HTTPS' ? '443' : '80'))
                .replace(/127\\.0\\.0\\.\\d+/g, 'mfp.example.com');
      };
      const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      // 單機的紅字提醒寫的就是「服務只開在本機（127.0.0.1）」，那是說明不是設備位址，不可以換
      for (let n = w.nextNode(); n; n = w.nextNode()) {
        if (n.parentElement && n.parentElement.closest('#mfSingleWarn')) continue;
        const f = fix(n.textContent); if (f !== n.textContent) n.textContent = f; }
      document.querySelectorAll('input[type=text], input:not([type])').forEach(i => { i.value = fix(i.value); });
      return true; })()""" % json.dumps(mapping)


def _shoot(t: _Tab, path: Path, rewrite: str, focus: str | None = None, block: str = "start") -> None:
    """只截 `focus` 那一塊（四周留一點邊）：整個視窗連左側選單一起截，放進說明頁縮小之後字就看不清楚。"""
    t.js("window.scrollTo(0, 0)")
    time.sleep(0.4)
    t.js(rewrite)
    time.sleep(0.3)
    import base64
    params = {"format": "png", "captureBeyondViewport": True}
    if focus:
        r = t.js(f"""(() => {{ const el = document.querySelector({focus!r}); if (!el) return null;
                    const b = el.getBoundingClientRect();
                    return [b.left + window.scrollX, b.top + window.scrollY, b.width, b.height]; }})()""")
        assert r, f"找不到 {focus}"
        pad = 14
        x, y, w, h = r[0] - pad, r[1] - pad, r[2] + pad * 2, min(r[3] + pad * 2, 1400)
        params["clip"] = {"x": max(0, x), "y": max(0, y), "width": w, "height": h, "scale": 1}
    shot = t.send("Page.captureScreenshot", params)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(shot["result"]["data"]))
    print("  ", path.relative_to(public_root(REPO)))


def _doc_pdf(title: str, pages: int) -> bytes:
    """看得出是文件的掃描頁（標題、表格線、印章框）：預覽縮圖才不會是一片空白。內容全部虛構。"""
    import fitz
    doc = fitz.open()
    for n in range(1, pages + 1):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((60, 90), f"{title}  {n}/{pages}", fontsize=22)
        pg.draw_line((60, 105), (535, 105), color=(0.2, 0.3, 0.6), width=1.5)
        for i in range(14):
            y = 140 + i * 34
            pg.draw_rect(fitz.Rect(60, y, 535, y + 26), color=(0.75, 0.78, 0.85), width=0.6)
            pg.draw_line((60 + 120 * (i % 3 + 1), y + 13), (60 + 120 * (i % 3 + 1) + 160, y + 13), color=(0.45, 0.45, 0.5), width=2)
        pg.draw_circle((470, 740), 38, color=(0.8, 0.15, 0.15), width=2.5)
    data = doc.tobytes()
    doc.close()
    return data


def _select(t: _Tab, sid: str, value_js: str) -> None:
    t.js(f"""(() => {{ const s = document.getElementById({sid!r}); s.value = {value_js};
                     s.dispatchEvent(new Event('change')); return true; }})()""")


def capture(locale: str, port: int, cdp: int, ids: dict, rewrite: str, escl: FakeEscl) -> None:
    base = f"http://127.0.0.1:{port}"
    out = _out(locale)
    with _open(cdp) as ws:
        t = _Tab(ws)
        for d in ("Runtime", "Page", "Network"):
            t.send(f"{d}.enable")
        t.send("Network.setCookie", {"name": "jtdt_locale", "value": locale, "url": base})
        t.send("Emulation.setDeviceMetricsOverride", {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1,
                                                      "mobile": False})
        tool = base + "/tools/mfp-fetch/"

        def open_tool():
            t.send("Page.navigate", {"url": tool})
            assert t.wait_for("!!document.querySelector('#mfListWrap table') && typeof window.jtTime !== 'undefined'"), "清單沒出來"
            time.sleep(0.8)

        # 1. 我的事務機（清單）
        open_tool()
        _shoot(t, out / "01-list.png", rewrite, "div.panel:has(#mfListWrap)")

        # 2. 新增事務機：連線測試結果（不儲存）
        t.js(f"""(() => {{ const s = document.getElementById('mfTarget');
                 const o = document.createElement('option'); o.value = 'http:{ids["box_port"]}'; o.textContent = 'x';
                 s.append(o); s.value = 'http:{ids["box_port"]}';
                 document.getElementById('mfHost').value = '127.0.0.1';
                 document.getElementById('mfTestBtn').click(); return true; }})()""")
        assert t.wait_for("!document.getElementById('mfResultPanel').hidden && !document.getElementById('mfSaveBar').hidden", 60)
        t.js("document.getElementById('mfHost').value = 'mfp-3f.example.com'; "
             "const s = document.getElementById('mfTarget'); s.value = s.options[0].value; s.dispatchEvent(new Event('change'))")
        _shoot(t, out / "02-test.png", rewrite, "#mfResultPanel")

        # 3. 掃描面板（eSCL）
        open_tool()
        t.js(f"document.querySelector('[data-mf-scan=\"{ids['desk']}\"]').click()")
        assert t.wait_for("!document.getElementById('mfScanPanel').hidden")
        _shoot(t, out / "03-scan.png", rewrite, "#mfScanPanel")

        # 4. 先暫存、接著掃（平台玻璃一次一張），最後存成一份 PDF（2026-11-03）
        escl.docs = [_doc_pdf("Receipt-0421", 2)]
        t.js("document.getElementById('mfScanName').value = 'Receipts-0421'; document.getElementById('mfScanStart').click()")
        assert t.wait_for("document.querySelectorAll('#mfTrayGrid .mf-tray-page').length === 2", 90), \
            t.js("document.getElementById('mfScanState').innerText")
        escl.docs = [_doc_pdf("Contract-0421", 1)]
        t.js("document.getElementById('mfScanStart').click()")
        assert t.wait_for("document.querySelectorAll('#mfTrayGrid .mf-tray-page').length === 3", 90)
        assert t.wait_for("[...document.querySelectorAll('#mfTrayGrid img')].every(i => i.complete && i.naturalWidth > 0)", 30)
        _shoot(t, out / "04-scan-tray.png", rewrite, "#mfTray")
        t.js("document.getElementById('mfTraySave').click()")
        assert t.wait_for("document.getElementById('mfTrayState').classList.contains('done')", 90), \
            t.js("document.getElementById('mfTrayState').innerText")
        _shoot(t, out / "04-scan-done.png", rewrite, "#mfTrayState")

        # 5. FUJIFILM 信箱取件：列出文件、勾一份
        open_tool()
        t.js(f"document.querySelector('[data-mf-box=\"{ids['fuji']}\"]').click()")
        assert t.wait_for("!document.getElementById('mfBoxPanel').hidden")
        t.js("document.getElementById('mfBoxNo').value = '1'; document.getElementById('mfBoxPw').value = '1234';"
             "document.getElementById('mfBoxListBtn').click()")
        assert t.wait_for("!document.getElementById('mfBoxConfirm').hidden || !document.getElementById('mfBoxDocs').hidden", 60)
        if not t.js("document.getElementById('mfBoxConfirm').hidden"):
            t.js("document.querySelector('#mfBoxConfirm .btn-primary').click()")
        assert t.wait_for("!document.getElementById('mfBoxDocs').hidden", 60), t.js("document.getElementById('mfBoxError').innerText")
        t.js("document.querySelector('#mfBoxTable input[data-doc]').click()")
        t.js("document.getElementById('mfBoxPw').value = ''")
        _shoot(t, out / "05-box.png", rewrite, "#mfBoxPanel")

        # 6. RICOH 文件伺服器：不需登入 → 列出 → 選有密碼的資料夾 → 輸入資料夾密碼 → 列出
        open_tool()
        t.js(f"document.querySelector('[data-mf-share=\"{ids['ricoh']}\"]').click()")
        assert t.wait_for("!document.getElementById('mfSharePanel').hidden")
        _select(t, "mfShareMode", "'none'")
        t.js("document.getElementById('mfShareListBtn').click()")
        assert t.wait_for("!document.getElementById('mfShareConfirm').hidden || !document.getElementById('mfShareFiles').hidden", 60)
        if not t.js("document.getElementById('mfShareConfirm').hidden"):
            t.js("document.querySelector('#mfShareConfirm .btn-primary').click()")
        assert t.wait_for("!document.getElementById('mfShareFiles').hidden", 60), t.js("document.getElementById('mfShareError').innerText")
        _select(t, "mfShareRoot", "document.getElementById('mfShareRoot').options[1].value")
        assert t.wait_for("!document.getElementById('mfShareFolderPwWrap').hidden")
        t.js("document.getElementById('mfShareFolderPw').value = '001234'; document.getElementById('mfShareFolderOpen').click()")
        assert t.wait_for("document.getElementById('mfShareTable').innerText.includes('Expense-claims')", 60), \
            t.js("document.getElementById('mfShareFolderMsg').innerText")
        t.js("document.querySelector('#mfShareTable input[data-file]').click()")
        t.js("document.getElementById('mfShareFolderPw').value = ''")
        _shoot(t, out / "06-share.png", rewrite, "#mfSharePanel")

        # 7. 協定紀錄（技術細節）
        t.js("(() => { const d = document.querySelector('#mfSharePanel details'); if (d) d.open = true; return !!d; })()")
        time.sleep(0.5)
        _shoot(t, out / "07-log.png", rewrite, "#mfSharePanel details")

        # 8. 共用事務機（管理員）：成員面板
        t.send("Page.navigate", {"url": base + "/admin/mfp"})
        assert t.wait_for("!!document.querySelector('#mfListWrap table')")
        time.sleep(0.6)
        t.js("[...document.querySelectorAll('#mfListWrap tbody tr')].find(r => r.innerText.includes('2F Sales'))"
             ".querySelector('button').click()")
        assert t.wait_for("!document.getElementById('mfMembersPanel').hidden && "
                          "document.querySelectorAll('#mfMUsers label').length > 0", 30)
        _shoot(t, out / "08-members.png", rewrite, "div.panel:has(#mfMembersPanel)")


def capture_standalone(locale: str, port: int, cdp: int, rewrite: str) -> None:
    """單機版使用說明（`mfp-fetch-standalone.html`）另外要的三張：紅字提醒、新增事務機的表單、全是自己新增的清單。
    掃描、取件、協定紀錄的面板跟共用時一模一樣，直接沿用 mfp-guide 那組。"""
    base = f"http://127.0.0.1:{port}"
    out = _out(locale).parent / "mfp-standalone"
    with _open(cdp) as ws:
        t = _Tab(ws)
        for d in ("Runtime", "Page", "Network"):
            t.send(f"{d}.enable")
        t.send("Network.setCookie", {"name": "jtdt_locale", "value": locale, "url": base})
        t.send("Emulation.setDeviceMetricsOverride", {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1,
                                                      "mobile": False})
        t.send("Page.navigate", {"url": base + "/tools/mfp-fetch/"})
        assert t.wait_for("!!document.querySelector('#mfListWrap table') && typeof window.jtTime !== 'undefined'"), "清單沒出來"
        time.sleep(0.8)
        t.js("document.getElementById('mfHost').value = 'mfp-3f.example.com'")
        _shoot(t, out / "00-warn.png", rewrite, "#mfSingleWarn")
        _shoot(t, out / "01-add.png", rewrite, "div.panel:has(#mfHost)")
        _shoot(t, out / "02-list.png", rewrite, "div.panel:has(#mfListWrap)")


def main() -> int:
    from app.core import ui_locale
    ap = argparse.ArgumentParser()
    ap.add_argument("--locale", choices=list(ui_locale.SUPPORTED), default=None)
    ap.add_argument("--standalone", action="store_true",
                    help="只拍單機版說明頁要的兩張（事務機全部自己新增、沒有共用）")
    args = ap.parse_args()
    locales = [args.locale] if args.locale else list(ui_locale.SUPPORTED)
    if not _browser():
        raise SystemExit("找不到 chromium")
    box, box_srv, ricoh, escl, escl_srv = _fakes()
    data = tempfile.mkdtemp(prefix="mfpguide-")
    port, cdp = _free_port(), _free_port()
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    srv = subprocess.Popen([sys.executable, "-c", _LAUNCH.format(
        root=str(REPO), tools=str(REPO / "tools"), port=port, ports=(box.port, ricoh.port, escl.port))],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen([_browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                           _profile_arg(), f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        if not (_wait(base + "/healthz") and _wait(f"http://127.0.0.1:{cdp}/json/version")):
            raise SystemExit("實例或瀏覽器起不來")
        api = "/tools/mfp-fetch/api"
        ids = {"box_port": box.port}
        group = (_api(base, "GET", api + "/shared-subjects").get("groups") or [{}])[0].get("key")
        for key, host, p, name, shared in (("fuji", "127.0.0.1", box.port, "3F Office", True),
                                           ("ricoh", "127.0.0.2", ricoh.port, "2F Sales", True),
                                           ("desk", "127.0.0.1", escl.port, "My desk printer", False)):
            det = _api(base, "POST", api + "/detect", {"host": host, "port": p, "scheme": "http"})
            assert det.get("token"), det
            if args.standalone:
                shared = False
            res = _api(base, "POST", api + ("/shared" if shared else "/connections"),
                       {"token": det["token"], "display_name": name})
            ids[key] = res["connection"]["id"]
            if shared and group:
                _api(base, "PUT", f"{api}/shared/{ids[key]}/members", {"members": [{"type": "group", "key": group}]})
        # 先換「位址:埠」（三台的組合各不相同），剩下單獨的位址再換
        mapping = {"Fake LaserJet MFP M1": "HP LaserJet MFP M141w", f"127.0.0.1:{escl.port}": "printer-desk.example.com", f"127.0.0.1:{box.port}": "mfp-3f.example.com",
                   f"127.0.0.2:{ricoh.port}": "mfp-2f.example.com",
                   "127.0.0.2": "mfp-2f.example.com", "127.0.0.1": "mfp-3f.example.com"}
        rewrite = _rewrite_js(mapping)
        for loc in locales:
            print(loc)
            if args.standalone:
                capture_standalone(loc, port, cdp, rewrite)
            else:
                capture(loc, port, cdp, ids, rewrite, escl)
    finally:
        br.terminate()
        srv.terminate()
        for p in (br, srv):
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        box_srv.shutdown()
        ricoh.stop()
        escl_srv.shutdown()
        shutil.rmtree(data, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
