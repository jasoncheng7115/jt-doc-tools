"""合規頁的頁內搜尋 —— 真的在瀏覽器裡打字（頁面的產生與內容在 `test_compliance_page.py`）。

2026-10-10 加入。判準：

* 打一個控制項編號（`A.8.15`）：只剩那幾列與提到它的卡片，其他區塊整塊收起來，計數對得上。
* 不看空白與全形半形：`第12條`、`ａ．８．１５` 跟 `第 12 條`、`A.8.15` 一樣找得到。
* 卡片：只有「如何驗證」符合時，卡片留著、清單自動展開，不相干的條列藏起來。
* 找不到：計數是 0、出現「找不到」的說明，整頁沒有任何項目。
* 搜尋中按「對應」的條號：先清掉搜尋，目標那一列看得到而且在畫面裡。
* Esc 清掉：所有東西回來，停在剛才最上面那一個結果的位置。
* 網址帶 `?q=`：一打開就是搜尋結果。英文頁的計數用英文頁自己的文字。
* 手機寬度：搜尋框放得下、整頁沒有橫向捲動。
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.repo_paths import public_root  # noqa: E402

DOCS = public_root(ROOT) / "docs"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def site():
    """介紹站（靜態檔）＋ 一個開著遠端除錯的瀏覽器。回 (網址開頭, send, 例外清單)。"""
    from tools import browser_probe
    br_path = browser_probe.browser()
    if not br_path or not browser_probe.browser_runs(br_path):
        pytest.skip("沒有可用的瀏覽器")
    try:
        import websockets.sync.client as wsc
    except ImportError:
        pytest.skip("沒有 websockets")
    port, cdp = _free_port(), _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                           cwd=DOCS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen(
        [br_path, "--headless=new", "--no-sandbox", "--disable-gpu", browser_probe.profile_arg(),
         f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/compliance.html", timeout=1)
                urllib.request.urlopen(f"http://127.0.0.1:{cdp}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.skip("網站或瀏覽器起不來")
        req = urllib.request.Request(f"http://127.0.0.1:{cdp}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            tab = json.loads(r.read())
        ws = wsc.connect(tab["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
        n = [0]
        errs: list[str] = []

        def send(method, params=None):
            n[0] += 1
            i = n[0]
            ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
            while True:
                m = json.loads(ws.recv(timeout=120))
                if m.get("method") == "Runtime.exceptionThrown":
                    d = m["params"]["exceptionDetails"]
                    errs.append((d.get("exception", {}).get("description") or d.get("text", "?"))[:300])
                if m.get("id") == i:
                    return m

        send("Runtime.enable")
        send("Page.enable")
        yield f"http://127.0.0.1:{port}", send, errs
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        for p in (br, srv):            # 只收自己起的那兩支
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()


def js(send, expr: str):
    r = send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
    res = r.get("result", {})
    if "exceptionDetails" in res:
        raise AssertionError(res["exceptionDetails"])
    return res.get("result", {}).get("value")


def wait(send, expr: str, timeout: float = 20.0):
    end = time.time() + timeout
    while time.time() < end:
        v = js(send, expr)
        if v:
            return v
        time.sleep(0.1)
    return None


def open_page(send, url: str) -> None:
    send("Page.navigate", {"url": url})
    assert wait(send, "document.readyState === 'complete' && !!document.getElementById('cpSearch')"), url


def type_query(send, q: str) -> None:
    """跟使用者打字一樣送 input 事件，等到計數出來（搜尋延遲 80 毫秒）。"""
    js(send, "(function (q) { var i = document.getElementById('cpSearch'); i.focus(); i.value = q;"
             " i.dispatchEvent(new Event('input', {bubbles: true})); return true; })(%s)" % json.dumps(q))
    assert wait(send, "document.getElementById('cpSearchN').textContent !== '' || "
                      "!document.body.classList.contains('cp-searching') && %s === ''" % json.dumps(q.strip()), 5)
    time.sleep(0.15)


#: 目前看得到的東西
_STATE = """(function () {
  function vis(el) { return !el.hidden && !el.closest('[hidden]') && el.getClientRects().length > 0; }
  var rows = Array.from(document.querySelectorAll('.cp-table tbody tr')).filter(vis);
  var cards = Array.from(document.querySelectorAll('.cp-card')).filter(vis);
  var secs = Array.from(document.querySelectorAll('section.cp-sec')).filter(vis).map(function (s) { return s.id; });
  return {
    rows: rows.map(function (r) { return r.id || r.textContent.trim().slice(0, 40); }),
    cards: cards.map(function (c) { return c.querySelector('h3').textContent; }),
    lis: Array.from(document.querySelectorAll('.cp-card .cp-bullets > li')).filter(vis).length,
    others: Array.from(document.querySelectorAll('.cp-bullets > li, .cp-sec-lead, .cp-p, .cp-note, .cp-sub-lead, '
                                                 + '.cp-flow, .cp-shot'))
      .filter(function (el) { return !el.closest('.cp-card'); }).filter(vis).length,
    secs: secs,
    n: document.getElementById('cpSearchN').textContent,
    empty: !document.getElementById('cpSearchEmpty').hidden,
    hero: vis(document.querySelector('.cp-hero')),
    hl: (window.CSS && CSS.highlights && CSS.highlights.get('cp-hit')) ? CSS.highlights.get('cp-hit').size : -1
  };
})()"""


def test_search_filters_items_and_folds_empty_sections(site):
    base, send, errs = site
    open_page(send, base + "/compliance.html")
    total_rows = js(send, "document.querySelectorAll('.cp-table tbody tr').length")

    type_query(send, "A.8.15")
    st = js(send, _STATE)
    assert "a27-A.8.15" in st["rows"], st
    assert len(st["rows"]) < total_rows / 5, f"沒有篩掉不相干的列：{len(st['rows'])} / {total_rows}"
    assert "稽核記錄" in st["cards"], "卡片的「對應」有 A.8.15，卡片要留著：%s" % st
    assert set(st["secs"]) <= {"controls", "iso27001", "acceptance"}, st["secs"]
    assert "data" not in st["secs"] and "gdpr" not in st["secs"], "沒有符合項目的區塊要收起來"
    assert not st["hero"], "搜尋時頁首收起來，結果接在頁內導覽下面"
    # 計數＝看得到的項目：列、卡片、卡片以外的條列與段落（盤點表欄位那一條也提到 A.8.15）
    assert st["others"] >= 1, st
    assert st["n"] == f"{len(st['rows']) + len(st['cards']) + st['others']} 項", st
    assert st["hl"] != 0, "符合的字要標出來"

    # 不看空白、全形半形
    type_query(send, "ａ．８．１５")
    assert "a27-A.8.15" in js(send, _STATE)["rows"]
    type_query(send, "第12條")
    st = js(send, _STATE)
    assert "pdpa-12" in st["rows"], st["rows"]
    assert not errs, errs


def test_a_card_keeps_only_the_matching_part_and_opens_the_test_list(site):
    base, send, errs = site
    open_page(send, base + "/compliance.html")
    type_query(send, "test_pdf_isolate")
    st = js(send, _STATE)
    assert st["cards"] == ["文件處理的隔離"], st
    assert st["lis"] == 0, "只有「如何驗證」符合，卡片裡的條列要藏起來"
    opened = js(send, """Array.from(document.querySelectorAll('.cp-card details.cp-verify'))
        .filter(function (d) { return !d.closest('[hidden]') && !d.hidden; })
        .map(function (d) { return d.open; })""")
    assert opened == [True], "符合的「如何驗證」要自動展開"
    # 清掉之後展開的收回去
    js(send, "document.getElementById('cpSearch').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})), true")
    time.sleep(0.2)
    assert js(send, "Array.from(document.querySelectorAll('details.cp-verify')).every(function (d) { return !d.open; })")
    assert not errs, errs


def test_nothing_found_says_so(site):
    base, send, errs = site
    open_page(send, base + "/compliance.html")
    type_query(send, "量子糾纏不存在的字")
    st = js(send, _STATE)
    assert st["rows"] == [] and st["cards"] == [] and st["secs"] == [], st
    assert st["empty"] and st["n"].startswith("0"), st
    assert not errs, errs


def test_escape_restores_the_page_at_the_first_result(site):
    base, send, errs = site
    open_page(send, base + "/compliance.html")
    rows = js(send, "document.querySelectorAll('.cp-table tbody tr').length")
    type_query(send, "A.8.13")
    js(send, "document.getElementById('cpSearch').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})), true")
    time.sleep(0.2)
    st = js(send, _STATE)
    assert len(st["rows"]) == rows and st["hero"] and not st["empty"] and st["n"] == "", st
    assert js(send, "document.getElementById('cpSearch').value") == ""
    # 停在剛才最上面那一個結果（卡片「保留期限與備份」）附近，而不是跳回頁首
    where = js(send, """(function () {
        var nav = document.querySelector('.cp-pagenav').getBoundingClientRect().bottom;
        var c = Array.from(document.querySelectorAll('.cp-card')).find(function (x) {
            return x.querySelector('h3').textContent === '保留期限與備份'; });
        var r = c.getBoundingClientRect();
        return {y: window.pageYOffset, top: r.top, nav: nav, vh: innerHeight}; })()""")
    assert where["y"] > 300 and where["nav"] - 5 <= where["top"] <= where["vh"] / 2, where
    assert not errs, errs


def test_a_chip_clicked_while_searching_lands_on_its_row(site):
    base, send, errs = site
    open_page(send, base + "/compliance.html")
    type_query(send, "存取控制")
    js(send, "document.querySelector('.cp-card .cp-chip[href=\"#gdpr-32\"]').click(), true")
    # 介紹站是平滑捲動：等它停下來
    wait(send, "(function () { var t = document.getElementById('gdpr-32').getBoundingClientRect().top;"
               " return t >= 0 && t < innerHeight; })()", 8)
    got = js(send, """(function () {
        var r = document.getElementById('gdpr-32'), b = r.getBoundingClientRect();
        return {searching: document.body.classList.contains('cp-searching'), hidden: !!r.closest('[hidden]'),
                top: b.top, vh: innerHeight, hash: location.hash}; })()""")
    assert not got["searching"] and not got["hidden"], got
    assert got["hash"] == "#gdpr-32" and 0 <= got["top"] < got["vh"], got
    assert not errs, errs


def test_query_string_and_the_english_page(site):
    base, send, errs = site
    open_page(send, base + "/compliance-en.html?q=A.8.15")
    st = js(send, _STATE)
    assert "a27-A.8.15" in st["rows"], st
    assert re.fullmatch(r"\d+ found", st["n"]), "英文頁的計數用英文：" + st["n"]
    ph = js(send, "document.getElementById('cpSearch').placeholder")
    assert ph and not re.search(r"[一-鿿]", ph), ph
    assert not errs, errs


def test_phone_width_fits(site):
    base, send, errs = site
    send("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 2,
                                                "mobile": True})
    try:
        open_page(send, base + "/compliance.html")
        box = js(send, """(function () {
            var s = document.querySelector('.cp-search').getBoundingClientRect();
            var links = document.querySelector('.cp-pagenav-links').getBoundingClientRect();
            return {sw: s.width, sr: s.right, lw: links.width, vw: innerWidth,
                    page: document.documentElement.scrollWidth}; })()""")
        assert box["sw"] >= 120 and box["sr"] <= box["vw"] and box["lw"] >= 120, box
        assert box["page"] <= box["vw"] + 1, f"手機寬度出現橫向捲動：{box}"
        type_query(send, "A.8.15")
        assert "a27-A.8.15" in js(send, _STATE)["rows"]
        assert js(send, "document.documentElement.scrollWidth") <= 391
        # 手機上表格的列是 display:block —— 藏起來的列要真的不見（不能只有 hidden 屬性）
        assert js(send, "Array.from(document.querySelectorAll('.cp-table tbody tr[hidden], section.cp-sec[hidden]'))"
                        ".every(function (r) { return r.getClientRects().length === 0; })"), "藏起來的列在手機上還看得到"
    finally:
        send("Emulation.clearDeviceMetricsOverride", {})
    assert not errs, errs
