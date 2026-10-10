"""歷史案件的 DI 檔 —— 真的在瀏覽器裡跑一次（伺服器那一側在 `test_official_doc_di_cases.py`）。

* 「上傳 DI 檔」：選檔案就送出，對話框講出匯入幾份、哪幾份沒匯入、為什麼；關掉之後清單上多一件，
  標「DI 匯入」。
* 勾選：全選只勾得到有 DI 檔的；勾了之後「下載勾選的 DI 檔」才按得下去，下載到的是 zip。
* 每一列的「DI」按鈕下載到的是 DI 檔。
* 打開匯入的案件：草稿上方講出是從哪一份 DI 檔匯入的、注意事項；版本記錄的第一版寫「從 DI 檔匯入」。

伺服器、假的語言模型沿用 `test_official_doc_e2e_kb` 的 `live`（每個測試檔各自一份全新的實例）。
"""
from __future__ import annotations

import base64
import json
import os
import time

from tests.test_official_doc_di_cases import REAL_LETTER
from tests.test_official_doc_e2e_kb import _js, _wait, live  # noqa: F401

#: 下載不真的存檔：攔住「按連結下載」那一下，記下檔名與內容的開頭（base64）。
#: 內容從 `URL.createObjectURL` 那一刻拿到的 Blob 讀（不再 fetch 一次 blob: 網址）
_SPY = """(function () {
  window.__dl = [];
  var blobs = {}, make = URL.createObjectURL;
  URL.createObjectURL = function (b) { var u = make.call(URL, b); blobs[u] = b; return u; };
  HTMLAnchorElement.prototype.click = function () {
    var a = this, name = a.download, b = blobs[a.href];
    if (!name || !b) return;
    b.arrayBuffer().then(function (buf) {
      var u = new Uint8Array(buf).slice(0, 12), s = '';
      for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
      window.__dl.push({ name: name, head: btoa(s), size: buf.byteLength });
    });
  };
  return true; })()"""


def _set_files(send, selector: str, paths: list[str]) -> None:
    """選檔案。頁面在選完的那一刻就清掉 input（同一份可以再選一次），所以不看 input 裡剩幾個，
    看對話框出來沒有。"""
    assert _wait(send, "document.readyState === 'complete'", 30)
    doc = send("DOM.getDocument", {"depth": -1})
    node = send("DOM.querySelector", {"nodeId": doc["result"]["root"]["nodeId"], "selector": selector})
    send("DOM.setFileInputFiles", {"files": paths, "nodeId": node["result"]["nodeId"]})


def _cases_page(send, port: int) -> None:
    send("Runtime.enable")
    send("Page.enable")
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/official-doc/cases"})
    assert _wait(send, "document.readyState === 'complete' && !!document.getElementById('odcUpBtn')"), \
        "歷史案件頁沒有載入"


def test_upload_select_and_download_di_files(live):  # noqa: F811
    port, send, errs = live
    from tools import browser_probe
    d = browser_probe.uploadable_dir("odc-di")
    good = os.path.join(d, "東湖區公所來文.di")
    bad = os.path.join(d, "便簽.di")
    with open(good, "wb") as f:
        f.write(REAL_LETTER)
    with open(bad, "wb") as f:
        f.write('<便簽><段落><文字>x</文字></段落></便簽>'.encode("utf-8"))

    _cases_page(send, port)
    _set_files(send, "#odcFile", [good, bad])
    assert _wait(send, "!!document.querySelector('#modal-host .modal-card')", 30), "上傳之後沒有講結果"
    body = _js(send, "document.querySelector('#modal-host .modal-body').textContent")
    assert "已匯入 1 份" in body and "1 份沒有匯入" in body, body
    assert "便簽.di" in body and "只收「函」與「簽」" in body, body
    assert "書函" in body and "2 個附件檔" in body, "匯入時的注意事項要講出來：" + body
    _js(send, "document.querySelector('#modal-host .modal-ok').click(), true")
    assert _wait(send, "document.readyState === 'complete' && "
                       "document.querySelectorAll('tr[data-case-id]').length === 1", 30), \
        "關掉對話框之後清單要多一件"
    assert _js(send, "!!document.querySelector('tr[data-case-id] .odc-imp')"), "要標「DI 匯入」"

    # 勾選 → 批次下載
    assert _js(send, "document.getElementById('odcBatch').disabled"), "還沒勾就按得下去"
    _js(send, _SPY)
    _js(send, "document.getElementById('odcAll').click(), true")
    assert _wait(send, "!document.getElementById('odcBatch').disabled", 5), "全選之後要按得下去"
    assert "1" in _js(send, "document.getElementById('odcSel').textContent")
    _js(send, "document.getElementById('odcBatch').click(), true")
    assert _wait(send, "window.__dl.length === 1", 30), "批次下載沒有下載到東西"
    got = _js(send, "window.__dl[0]")
    assert got["name"].endswith(".zip") and base64.b64decode(got["head"]).startswith(b"PK"), got

    # 每一列的 DI 按鈕
    _js(send, "document.querySelector('[data-odc-di]').click(), true")
    assert _wait(send, "window.__dl.length === 2", 30), "DI 按鈕沒有下載到東西"
    got = _js(send, "window.__dl[1]")
    assert got["name"].endswith(".di") and base64.b64decode(got["head"]).startswith(b"<?xml"), got

    # 打開那件：講出是匯入的、版本記錄寫「從 DI 檔匯入」
    cid = _js(send, "document.querySelector('tr[data-case-id]').dataset.caseId")
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/official-doc/?case={cid}"})
    assert _wait(send, "document.readyState === 'complete' && "
                       "!document.getElementById('odResult').hidden", 60), "打不開匯入的案件"
    assert _wait(send, "!document.getElementById('odImported').hidden", 10), "沒講出是從 DI 檔匯入的"
    note = _js(send, "document.getElementById('odImportedText').textContent")
    assert "東湖區公所來文.di" in note and "書函" in note, note
    assert _wait(send, "!!document.querySelector('.od-src-import')", 20), \
        "版本記錄的第一版要寫「從 DI 檔匯入」"
    assert _js(send, "document.querySelector('.od-src-import').textContent") == "從 DI 檔匯入"
    draft = _js(send, "document.getElementById('odDraft').value")
    assert "主旨：檢送本所115年度里民活動成果報告1份，請　鑒核。" in draft, draft
    time.sleep(0.3)
    assert not errs, errs


def test_endorse_rows_cannot_be_ticked(live):  # noqa: F811
    """簽辦意見沒有 DI 檔：勾選框反灰、全選也不會勾到。"""
    port, send, errs = live
    _cases_page(send, port)
    made = _js(send, """(async function () {
        var r = await fetch('/tools/official-doc/start', {method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({mode: 'endorse', source: '臺北市政府函：請於10月20日前填報資料。',
                                direction: '請資訊室填報後陳核'})});
        return r.status; })()""")
    assert made == 200, made
    assert _wait(send, """(async function () {
        var r = await fetch('/tools/official-doc/api/cases');
        return (await r.json()).cases.some(function (c) { return c.mode === 'endorse' && c.latest_rev > 0; });
        })()""", 90), "簽辦意見沒有產生"
    _cases_page(send, port)
    rows = _js(send, """Array.from(document.querySelectorAll('tr[data-case-id]')).map(function (tr) {
        var ck = tr.querySelector('input[data-odc-pick]');
        return {mode: tr.querySelector('.odc-mode').textContent, disabled: ck ? ck.disabled : null,
                di: !!tr.querySelector('[data-odc-di]')}; })""")
    endorse = [r for r in rows if r["mode"] == "簽辦意見"]
    assert endorse and all(r["disabled"] and not r["di"] for r in endorse), rows
    _js(send, "document.getElementById('odcAll').click(), true")
    ticked = _js(send, "Array.from(document.querySelectorAll('input[data-odc-pick]:checked')).length")
    assert ticked == len([r for r in rows if r["disabled"] is False]), (ticked, rows)
    assert not errs, json.dumps(errs, ensure_ascii=False)
