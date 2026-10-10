"""公文撰擬：重新產生時的反灰與轉圈、載入範例、之前填過的、本站樣式的下拉、
重新產生接在同一個案件後面、草稿旁的預覽圖。

2026-10-08 使用者一連串要求：

* 「如果已有產生過草稿，再填入內容或修改時，再按一次產生草稿，產生中時下面的草稿區要反灰，
  完成後才解除」—— 依修改後的資料重新產生、改寫一段也一樣，草稿上要有轉圈。
* 「需求描述下面下一個下拉清單，可以選擇載入範例文字。下拉清單要用本站樣式」
* 「可以自動記住之前填過的，旁邊有按鈕點下拉可以選之前填過的，也可以刪除」
* 「這個下拉 展開的清單要用本站樣式」（整頁的下拉）
* 「重新產生後要自動存第 n 版 不要等按下儲存版本」
* 「草稿右邊可以順便產生預覽圖」「文字先產，然後邊產圖片，圖片還沒出來時有個 spinner」

**判準一律是真的在瀏覽器裡跑一次**：反灰、`inert`、轉圈這一類只看程式碼永遠綠 ——
元素都在、沒有例外，只有實際點下去（CDP 送真的滑鼠事件）才知道按不按得到。
每一條出口（成功、失敗、停止、改寫回來）都各自驗「還原了」，只驗成功那條的話，
拿掉失敗時的還原也照樣全綠。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest

from app.core import official_doc as od
from tests.test_official_doc_tool import (BASE, NARRATIVE, ROOT, SIGN_DRAFT, TEMPLATE,  # noqa: F401
                                          FakeLLM, _eval, _free_port, _js, _revs, _run,
                                          _save, _start, _until, _user_client, _wait_job,
                                          fake_llm)


def _rt():
    import importlib
    return importlib.import_module("app.tools.official_doc.router")


def _examples():
    import importlib
    return importlib.import_module("app.tools.official_doc.examples")


# ------------------------------------------------------------------ 範例：資料本身

def test_there_are_52_examples_with_fixed_keys():
    """範例集 v1.1：機關的簽、簽辦意見、函各 12，企業發給政府機關的函 16。"""
    ex = _examples()
    keys = [e["key"] for e in ex.EXAMPLES]
    assert len(keys) == 52 and len(set(keys)) == 52
    for prefix, mode, n, who in (("SIGN-", "sign", 12, "agency"), ("OPINION-", "endorse", 12, "agency"),
                                 ("LETTER-", "letter", 12, "agency"),
                                 ("BIZ-LETTER-", "letter", 16, "company")):
        got = [e for e in ex.EXAMPLES if e["key"].startswith(prefix)]
        assert len(got) == n and all(e["mode"] == mode and e["issuer"] == who for e in got), prefix
    for e in ex.EXAMPLES:
        assert e["label"].strip() and e["category"].strip(), e["key"]
        # 函的範例一定帶發文身分（範例集 v1.1 第 9 點：不靠模型從需求猜）
        if e["mode"] == "letter":
            assert e["fields"].get("issuer") == e["issuer"], e["key"]
            assert next(iter(e["fields"])) == "issuer", "發文身分要先填（換身分會換掉記住的公司 / 機關資料）"
    # 發文身分 → 文別
    assert [(g[0], g[1]) for g in ex.grouped()] == [
        ("sign", "公務機關｜簽"), ("endorse", "公務機關｜簽辦意見"), ("letter", "公務機關｜函"),
        ("letter", "企業｜函（發給政府機關）")]
    assert all(e["issuer"] == "company" for e in ex.grouped()[3][2])


@pytest.mark.parametrize("key", [e["key"] for e in _examples().EXAMPLES])
def test_every_example_is_accepted_by_start(key):
    """每一份照畫面送出的樣子（模式＋範例的欄位）伺服器都收得下：不超過上限、選項在白名單裡。
    **不截斷** —— 超過上限是 400，所以收得下就代表一個字都沒少。"""
    ex = next(e for e in _examples().EXAMPLES if e["key"] == key)
    body = {"mode": ex["mode"], **ex["fields"]}
    inputs = _rt()._parse_inputs(body)
    for k, v in ex["fields"].items():
        assert inputs[k] == v, (key, k)
    if ex["mode"] == "letter" and ex["issuer"] == "company":
        assert inputs["relation"] == od.COMPANY_RELATION
    elif ex["mode"] == "letter":
        assert ex["fields"]["relation"] in od.RELATIONS
    if "length" in ex["fields"]:
        assert ex["fields"]["length"] in od.LENGTHS


def _ex_fields_in_template() -> dict:
    """樣板裡 `EX_FIELDS` 那張表（範例的欄位 → 畫面上的欄位）。"""
    src = TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"var EX_FIELDS = \{(.*?)\n  \};", src, re.S)
    assert m, "樣板裡找不到 EX_FIELDS"
    out = {}
    for mode, body in re.findall(r"(\w+): \{([^}]*)\}", m.group(1)):
        out[mode] = dict(re.findall(r"(\w+): '(od\w+)'", body))
    return out


def test_every_example_field_maps_to_a_field_on_the_page(client, auth_off):
    """範例用到的欄位名稱，前端都對得到畫面上的欄位 —— 對不到的那一格會**安靜地**不填。"""
    table = _ex_fields_in_template()
    html = client.get(f"{BASE}/").text
    for e in _examples().EXAMPLES:
        for k in e["fields"]:
            assert k in table[e["mode"]], (e["key"], k)
            assert f'id="{table[e["mode"]][k]}"' in html, (e["key"], k)


def test_the_page_lists_the_examples_in_four_groups(client, auth_off):
    html = client.get(f"{BASE}/").text
    sel = html[html.index('id="odExample"'):]
    sel = sel[:sel.index("</select>")]
    assert len(re.findall(r"<optgroup ", sel)) == 4
    assert len(re.findall(r"<option value=\"[A-Z-]+-\d\d\"", sel)) == 52
    assert "企業｜函（發給政府機關）" in sel and "驗收申請｜完成安裝測試後申請驗收" in sel
    assert "資訊採購｜採購備份主機與儲存設備" in sel
    # 範例的文字是輸入資料（JSON 放在 data-examples）
    data = re.search(r"data-examples='([^']*)'", html).group(1)
    assert "個資保護教育訓練" in json.loads(data.replace("&#39;", "'"))[24]["fields"]["narrative"]


# ------------------------------------------------------------------ 重新產生接在同一個案件後面

def _regen(c, cid, res, **kw):
    # 跟 `_start` 預設送的一樣（陳核對象也要一樣，不然草稿本來就會不同）
    body = {"mode": "sign", "narrative": NARRATIVE, "unit": "資訊室", "addressee": "主任秘書\n局長",
            "closing": "核示", "length": "normal", "case_id": cid,
            "facts": res["draft"]["facts"], "overrides": {}}
    body.update(kw)
    return c.post(f"{BASE}/start", json=body)


def test_regen_appends_the_next_revision_to_the_same_case(client, auth_off, fake_llm):
    _, cid, res = _run(client)
    assert _save(client, cid, res["draft"]["text"] + "\n我改過的", 1).status_code == 200
    fake_llm.sign_draft = dict(SIGN_DRAFT, subject="汰換資訊室雷射印表機2台並辦理報廢")
    r = _regen(client, cid, res)
    assert r.status_code == 200, r.text
    assert r.json()["case_id"] == cid, "重新產生開了一個新的案件 —— 版本清單會從第 1 版重來"
    j = _wait_job(r.json()["job_id"])
    assert j.status == "done", j.error
    assert j.meta["case_id"] == cid and j.meta["regen_rev"] == 3
    revs = _revs(client, cid)
    assert [x["rev"] for x in revs["revisions"]] == [1, 2, 3]
    assert [x["source"] for x in revs["revisions"]] == ["ai", "edit", "regen"]
    assert revs["revisions"][-1]["parent"] == 2
    new = client.get(f"{BASE}/result/{cid}").json()
    assert "並辦理報廢" in new["draft"]["text"]
    third = client.get(f"{BASE}/revisions/{cid}/3").json()
    assert third["text"] == new["draft"]["text"]
    # 前兩版原封不動
    assert client.get(f"{BASE}/revisions/{cid}/1").json()["text"] == res["draft"]["text"]


def test_regen_with_the_same_text_does_not_add_a_revision(client, auth_off, fake_llm):
    _, cid, res = _run(client)
    j = _wait_job(_regen(client, cid, res).json()["job_id"])
    assert j.status == "done" and j.meta["regen_rev"] is None
    assert [x["rev"] for x in _revs(client, cid)["revisions"]] == [1]


def test_a_failed_regen_leaves_the_case_untouched(client, auth_off, fake_llm):
    _, cid, res = _run(client)
    before = client.get(f"{BASE}/result/{cid}").json()
    fake_llm.sign_draft = {"note": "我不照格式"}
    j = _wait_job(_regen(client, cid, res, narrative=NARRATIVE + "另請總務科協助驗收。").json()["job_id"])
    assert j.status == "error"
    assert client.get(f"{BASE}/result/{cid}").json() == before, "失敗的重新產生動到了原本的結果"
    assert [x["rev"] for x in _revs(client, cid)["revisions"]] == [1]
    case = json.loads(_rt()._case_path(cid).read_text(encoding="utf-8"))
    assert "另請總務科" not in case["inputs"]["narrative"], "失敗的重新產生換掉了案件的輸入"


def test_a_plain_start_still_creates_a_new_case(client, auth_off, fake_llm):
    _, cid, _res = _run(client)
    _, cid2, _ = _run(client)
    assert cid2 != cid
    assert [x["rev"] for x in _revs(client, cid2)["revisions"]] == [1]


def test_regen_must_keep_the_mode(client, auth_off, fake_llm):
    _, cid, res = _run(client)
    r = client.post(f"{BASE}/start", json={"mode": "endorse", "source": "來文", "direction": "擬照辦",
                                           "case_id": cid})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("bad", ["../x", "abc", "0" * 31, "Z" * 32])
def test_regen_case_id_is_validated(client, auth_off, fake_llm, bad):
    r = _start(client, case_id=bad)
    assert r.status_code == 400, r.text


def test_regen_on_another_users_case_is_refused(admin_session, fake_llm):
    owner = _user_client("odrb_owner")
    other = _user_client("odrb_other")
    _, cid, res = _run(owner)
    r = _regen(other, cid, res)
    assert r.status_code in (403, 404), r.text
    assert [x["rev"] for x in _revs(owner, cid)["revisions"]] == [1]


def test_clients_cannot_claim_a_regen_revision(client, auth_off, fake_llm):
    _, cid, res = _run(client)
    r = _save(client, cid, res["draft"]["text"] + "\n自己寫的", 1, source="regen")
    assert r.status_code == 400, r.text
    assert "regen" not in _rt().CLIENT_REVISION_SOURCES


# ------------------------------------------------------------------ 預覽圖：端點

def _tiny_pdf(text: str, pages: int = 1) -> bytes:
    import fitz
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=300, height=420)
        p.insert_text((40, 60), f"{i + 1} {len(text)}")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def fake_export(monkeypatch):
    """把匯出換成一份小 PDF（不需要 Office 引擎），順便數被叫了幾次。"""
    from app.core import official_doc_odt
    calls = []

    def export(text, fmt, *, title="", draft_mark=True, template=None, extras=None):
        calls.append((text, fmt, title, draft_mark))
        return _tiny_pdf(text, pages=2), "application/pdf"

    monkeypatch.setattr(official_doc_odt, "export", export)
    return calls


def _pv(c, cid, text, **kw):
    body = {"case_id": cid, "text": text, "title": "汰換印表機", "draft_mark": True}
    body.update(kw)
    return c.post(f"{BASE}/preview", json=body)


def test_preview_goes_through_the_pdf_export_and_serves_pngs(client, auth_off, fake_llm, fake_export):
    _, cid, res = _run(client)
    text = res["draft"]["text"]
    r = _pv(client, cid, text)
    assert r.status_code == 200, r.text
    d = r.json()
    assert re.fullmatch(r"[0-9a-f]{24}", d["hash"]) and d["total"] == 2 and len(d["pages"]) == 2
    assert fake_export == [(text, "pdf", "汰換印表機", True)], "預覽沒有走匯出 PDF 那一條路"
    img = client.get(d["pages"][0])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.content[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image
    import io
    w, h = Image.open(io.BytesIO(img.content)).size
    assert 400 <= w <= 500, w        # 300pt × 110dpi / 72 ≈ 458px
    # 檔名帶著案件編號（保留期清理靠它認人）
    assert list(_rt().settings.temp_dir.glob(f"od_{cid}_pv_{d['hash']}_p*.png"))


def test_the_same_content_reuses_the_cached_images(client, auth_off, fake_llm, fake_export):
    _, cid, res = _run(client)
    text = res["draft"]["text"]
    a = _pv(client, cid, text).json()
    b = _pv(client, cid, text).json()
    assert a["hash"] == b["hash"] and len(fake_export) == 1, "同一份內容又跑了一次轉檔"
    c = _pv(client, cid, text + "\n多一行").json()
    assert c["hash"] != a["hash"] and len(fake_export) == 2
    d = _pv(client, cid, text, draft_mark=False).json()
    assert d["hash"] not in (a["hash"], c["hash"]), "草稿標示不同卻拿到同一份預覽"


def test_only_the_latest_few_previews_are_kept(client, auth_off, fake_llm, fake_export):
    _, cid, res = _run(client)
    hashes = []
    for i in range(_rt().PREVIEW_KEEP + 2):
        hashes.append(_pv(client, cid, res["draft"]["text"] + f"\n第{i}次").json()["hash"])
        time.sleep(0.02)
    mans = list(_rt().settings.temp_dir.glob(f"od_{cid}_pv_*.json"))
    assert len(mans) == _rt().PREVIEW_KEEP
    assert client.get(f"{BASE}/preview/{cid}/{hashes[-1]}/1").status_code == 200, "最新那一份被清掉了"
    assert client.get(f"{BASE}/preview/{cid}/{hashes[0]}/1").status_code == 404


@pytest.mark.parametrize("h, n, code", [("z" * 24, 1, 400), ("A" * 24, 1, 400), ("0" * 25, 1, 400),
                                        ("0" * 24, 1, 404), ("0" * 24, 0, 404), ("0" * 24, 99, 404)])
def test_bad_preview_requests(client, auth_off, fake_llm, h, n, code):
    _, cid, _res = _run(client)
    assert client.get(f"{BASE}/preview/{cid}/{h}/{n}").status_code == code


def test_preview_page_out_of_range_is_404(client, auth_off, fake_llm, fake_export):
    _, cid, res = _run(client)
    d = _pv(client, cid, res["draft"]["text"]).json()
    assert client.get(f"{BASE}/preview/{cid}/{d['hash']}/3").status_code == 404
    assert client.get(f"{BASE}/preview/{'f' * 32}/{d['hash']}/1").status_code in (403, 404)


def test_preview_of_another_users_case_is_refused(admin_session, fake_llm, fake_export):
    owner = _user_client("odpv_owner")
    other = _user_client("odpv_other")
    _, cid, res = _run(owner)
    d = _pv(owner, cid, res["draft"]["text"]).json()
    assert owner.get(d["pages"][0]).status_code == 200
    assert _pv(other, cid, res["draft"]["text"]).status_code in (403, 404)
    r = other.get(d["pages"][0])
    assert r.status_code in (403, 404) and not r.content.startswith(b"\x89PNG")


def test_preview_without_office_is_503(client, auth_off, fake_llm, monkeypatch):
    from app.core import office_convert
    _, cid, res = _run(client)
    monkeypatch.setattr(office_convert, "find_soffice", lambda: None)
    r = _pv(client, cid, res["draft"]["text"])
    assert r.status_code == 503, r.text


# ------------------------------------------------------------------ 真的在瀏覽器裡跑

def _slow_llm_server(port: int, fake: FakeLLM, ctl: dict):
    """假的 OpenAI 相容伺服器（同 `test_official_doc_tool._fake_llm_server`），
    但每次回答前先等 `ctl["delay"]` 秒 —— 處理中的畫面才看得到。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            prompt = "\n".join(str(m.get("content") or "") for m in body.get("messages") or [])
            time.sleep(float(ctl.get("delay") or 0))
            try:
                content = fake.text_query(prompt)
            except AssertionError:
                content = "{}"
            chunk = json.dumps({"choices": [{"delta": {"content": content}}]})
            out = (f"data: {chunk}\n\n" + "data: [DONE]\n\n").encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            except (BrokenPipeError, ConnectionResetError):
                pass       # 作業被停掉了：對方已經不等了

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def live():
    import os
    import subprocess
    import sys
    import tempfile
    import urllib.request

    sys.path.insert(0, str(ROOT))
    from tools import browser_probe

    br_path = browser_probe.browser()
    if not br_path:
        pytest.skip("沒有 chromium")
    try:
        import websockets.sync.client as wsc
    except ImportError:
        pytest.skip("沒有 websockets")

    data = tempfile.mkdtemp(prefix="odbusy-")
    port, cdp, llm_port = _free_port(), _free_port(), _free_port()
    fake, ctl = FakeLLM(), {"delay": 0.0}
    llm = _slow_llm_server(llm_port, fake, ctl)
    (Path(data) / "auth_settings.json").write_text(json.dumps({"backend": "off"}), encoding="utf-8")
    (Path(data) / "llm_settings.json").write_text(json.dumps({
        "enabled": True, "base_url": f"http://127.0.0.1:{llm_port}/v1",
        "model": "fake", "timeout_seconds": 60}), encoding="utf-8")
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen(
        [br_path, "--headless=new", "--no-sandbox", "--disable-gpu",
         browser_probe.profile_arg(), f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", "about:blank"],
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
                m = json.loads(ws.recv(timeout=180))
                if m.get("method") == "Runtime.exceptionThrown":
                    d = m["params"]["exceptionDetails"]
                    errs.append((d.get("exception", {}).get("description")
                                 or d.get("text", "?"))[:300])
                if m.get("id") == i:
                    return m

        send("Page.enable")
        send("Runtime.enable")
        send("DOM.enable")
        yield port, send, errs, fake, ctl
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        for p in (br, srv):            # 只收自己起的那兩支（不用名字批次殺）
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
        llm.shutdown()


def _open(port, send, width=1366, mobile=False):
    send("Emulation.setDeviceMetricsOverride",
         {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": mobile})
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/official-doc/"})
    assert _until(send, "document.readyState === 'complete' && !!document.getElementById('odGo') "
                        "&& !!document.querySelector('#odEx .jt-select-trigger')", 30), "頁面沒開起來"
    # 記下每一個送出去的請求（點了按鈕有沒有真的送出，看這裡）
    _eval(send, """(function(){ if (window.__fx) return 1; window.__fx = [];
      var o = window.fetch;
      window.fetch = function(u, opt){ window.__fx.push(String(u)); return o.apply(this, arguments); };
      return 1; })()""")


def _set(send, el_id, value):
    _eval(send, "(function(){var e=document.getElementById(%s); e.value=%s;"
                "e.dispatchEvent(new Event('input', {bubbles:true})); return 1;})()"
          % (_js(el_id), _js(value)))


def _center(send, selector):
    for _ in range(3):
        _eval(send, "(function(){var e=document.querySelector(%s); e.scrollIntoView({block:'center'});"
                    "return 1;})()" % _js(selector))
        time.sleep(0.15)
    return _eval(send, "(function(){var r=document.querySelector(%s).getBoundingClientRect();"
                       "return [r.left + r.width / 2, r.top + r.height / 2];})()" % _js(selector))


def _mouse_click(send, x, y):
    for t in ("mousePressed", "mouseReleased"):
        send("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left",
                                          "clickCount": 1})


def _click(send, selector):
    """像使用者那樣點（真的滑鼠事件，不是 `el.click()`）—— 被蓋住、`inert` 的就點不到。"""
    x, y = _center(send, selector)
    _mouse_click(send, x, y)


def _key(send, key, code=None, vk=0):
    for t in ("keyDown", "keyUp"):
        send("Input.dispatchKeyEvent", {"type": t, "key": key, "code": code or key,
                                        "windowsVirtualKeyCode": vk})


def _exports(send) -> int:
    return _eval(send, "window.__fx.filter(function(u){return u.indexOf('/export') >= 0;}).length")


_BUSY_ON = ("document.getElementById('odResultBody').hasAttribute('inert') && "
            "document.getElementById('odResult').getAttribute('aria-busy') === 'true'")
_BUSY_OFF = ("!document.getElementById('odResultBody').hasAttribute('inert') && "
             "!document.getElementById('odResult').hasAttribute('aria-busy') && "
             "document.getElementById('odDraftBusy').hidden && !document.getElementById('odDraft').readOnly")

#: 草稿上那層轉圈：看得到、有轉圈、而且**沒有跟著淡掉**（祖先的透明度乘起來是 1）
_OVERLAY_SHOWN = """(function(){
  var o = document.getElementById('odDraftBusy');
  if (o.hidden) return false;
  var r = o.getBoundingClientRect();
  if (r.width < 50 || r.height < 50 || !o.querySelector('.spinner')) return false;
  var op = 1;
  for (var e = o; e; e = e.parentElement) op *= Number(getComputedStyle(e).opacity);
  return op > 0.99;
})()"""


def _check_busy_screen(send, label):
    """產生 / 重新產生進行中：整塊反灰、不能操作、草稿上轉圈（實際點、實際打字都沒有作用）。"""
    assert _eval(send, _OVERLAY_SHOWN), "草稿上沒有轉圈（或轉圈跟著淡掉了）"
    assert _eval(send, "document.getElementById('odDraftBusyLabel').textContent") == label
    assert _eval(send, "document.getElementById('odDraft').readOnly"), "處理中草稿還能改"
    assert _eval(send, "!document.getElementById('odBusyNote').hidden")
    # 反灰有 0.15 秒的淡出 —— 剛切過去那一刻量到的可能還是 1，等它淡完再量
    # 匯出那一排在卡片（`.od-box-body`）裡面 —— 量的是**看起來**的透明度（祖先乘起來），
    # 不是那個元素自己的 `opacity`（反灰設在卡片的內容那一層）
    assert _until(send, "Number(getComputedStyle(document.getElementById('odFacts')).opacity) < 0.5 && "
                        "(function(){var op=1; for (var e=document.querySelector('[data-od-dl=odt]'); e; "
                        "e=e.parentElement) op*=Number(getComputedStyle(e).opacity); return op < 0.5;})()",
                  3), "整理出來的資料 / 匯出那一排沒有反灰"
    # 真的去點匯出鈕：不可以送出匯出請求
    n0 = _exports(send)
    _click(send, "[data-od-dl=txt]")
    time.sleep(0.8)
    assert _exports(send) == n0, "處理中按得到匯出鈕"
    # 草稿中間被轉圈蓋住；真的打字也打不進去
    hit = _eval(send, """(function(){var t=document.getElementById('odDraft'); t.scrollIntoView({block:'center'});
      var r=t.getBoundingClientRect(); var e=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
      return e ? (e.id || e.className || e.tagName) : '';})()""")
    assert hit != "odDraft", "草稿沒有被轉圈蓋住"
    before = _eval(send, "document.getElementById('odDraft').value")
    _eval(send, "document.getElementById('odDraft').focus(), 1")
    send("Input.insertText", {"text": "（不該打得進去）"})
    assert _eval(send, "document.getElementById('odDraft').value") == before, "處理中還打得進草稿"


def _generate_first(send, fake, ctl):
    fake.sign_draft = dict(SIGN_DRAFT)
    ctl["delay"] = 1.0
    _set(send, "odNarrative", NARRATIVE)
    _set(send, "odUnit", "資訊室")
    _eval(send, "document.getElementById('odGo').click(), 1")
    # 第一次產生：還沒有草稿 —— 照舊，不反灰、不出提示
    assert _until(send, "!document.getElementById('odCancel').hidden", 10)
    assert _eval(send, "document.getElementById('odResult').hidden && "
                       "document.getElementById('odBusyNote').hidden && "
                       "!document.getElementById('odResultBody').hasAttribute('inert')")
    assert _until(send, "!document.getElementById('odResult').hidden && "
                        "document.getElementById('odDraft').value.indexOf('主旨：') >= 0", 60), \
        "第一次產生的草稿沒有出來"
    assert _until(send, _BUSY_OFF, 10)
    return _eval(send, "document.getElementById('odDraft').value")


def test_regenerating_greys_out_the_old_draft_until_the_job_ends(live):
    port, send, errs, fake, ctl = live
    _open(port, send)
    old = _generate_first(send, fake, ctl)

    # ---- 1. 再按一次「產生草稿」（成功）：處理中整塊反灰，完成後換成新的一份 ----
    fake.sign_draft = dict(SIGN_DRAFT, subject="汰換資訊室雷射印表機2台並辦理報廢")
    ctl["delay"] = 3.0
    _set(send, "odNarrative", NARRATIVE + "另請總務科協助驗收。")
    _eval(send, "document.getElementById('odGo').click(), 1")
    assert _until(send, _BUSY_ON, 10), "已經有草稿、又按了產生草稿，下面卻沒有反灰"
    assert _eval(send, "!!document.querySelector('#odGo .spinner')"), "產生草稿那顆沒有轉圈"
    _check_busy_screen(send, "重新產生中…")
    assert _until(send, "document.getElementById('odDraft').value.indexOf('並辦理報廢') >= 0", 60), \
        "作業完成了，草稿沒有換成新的一份"
    assert _until(send, _BUSY_OFF, 10), "作業完成了，草稿區還是反灰 / 唯讀"
    assert _until(send, "Number(getComputedStyle(document.getElementById('odFacts')).opacity) === 1", 3)
    assert _eval(send, "!document.querySelector('#odGo .spinner')")
    second = _eval(send, "document.getElementById('odDraft').value")
    assert second != old
    n0 = _exports(send)
    _click(send, "[data-od-dl=txt]")
    assert _until(send, "window.__fx.filter(function(u){return u.indexOf('/export') >= 0;}).length > %d"
                  % n0, 10), "完成之後匯出鈕按不到"

    # ---- 2.「依修改後的資料重新產生」失敗：原本那一份原樣還回來、照常能用 ----
    fake.sign_draft = {"note": "我不照格式"}
    ctl["delay"] = 4.0
    _eval(send, "document.getElementById('odRegen').click(), 1")
    assert _until(send, _BUSY_ON, 10), "按了重新產生，下面卻沒有反灰"
    assert _eval(send, "!!document.querySelector('#odRegen .spinner')"), "重新產生那顆沒有轉圈"
    # 正在跑的那顆不跟著整塊淡掉（它自己是停用的樣子，那是按鈕本來的樣式）
    assert _eval(send, """(function(){var op = 1;
      for (var e = document.getElementById('odRegen').parentElement; e; e = e.parentElement)
        op *= Number(getComputedStyle(e).opacity);
      return op;})()""") == 1, "重新產生那顆的轉圈跟著整塊淡掉了"
    _check_busy_screen(send, "重新產生中…")
    assert _until(send, _BUSY_OFF, 60), "重新產生失敗之後，草稿區沒有還原"
    assert _eval(send, "document.getElementById('odDraft').value") == second, "失敗之後草稿不是原本那一份"
    assert _eval(send, "!document.querySelector('#odRegen .spinner') && "
                       "!document.getElementById('odRegen').disabled")
    n0 = _exports(send)
    _click(send, "[data-od-dl=txt]")
    assert _until(send, "window.__fx.filter(function(u){return u.indexOf('/export') >= 0;}).length > %d"
                  % n0, 10), "失敗之後匯出鈕按不到"
    _eval(send, "(function(){var t=document.getElementById('odDraft'); t.focus();"
                "t.setSelectionRange(t.value.length, t.value.length); return 1;})()")
    send("Input.insertText", {"text": "Z"})
    assert _eval(send, "document.getElementById('odDraft').value") == second + "Z", "失敗之後草稿打不進字"
    _eval(send, "(function(){var t=document.getElementById('odDraft'); t.value=%s;"
                "t.dispatchEvent(new Event('input')); return 1;})()" % _js(second))

    # ---- 3. 按「停止」：原本那一份原樣還回來 ----
    fake.sign_draft = dict(SIGN_DRAFT, subject="這一份不該出現")
    ctl["delay"] = 4.0
    _eval(send, "document.getElementById('odGo').click(), 1")
    assert _until(send, _BUSY_ON + " && !document.getElementById('odCancel').hidden", 10)
    _eval(send, "document.getElementById('odCancel').click(), 1")
    assert _until(send, _BUSY_OFF, 5), "按了停止，草稿區沒有還原"
    assert _eval(send, "document.getElementById('odDraft').value") == second
    assert _eval(send, "!document.querySelector('#odGo .spinner')")
    time.sleep(ctl["delay"] + 1)   # 被停掉的那件不可以晚一點又把草稿換掉
    assert _eval(send, "document.getElementById('odDraft').value") == second

    # ---- 4. 改寫一段：按鈕轉圈、草稿上轉圈、草稿唯讀；回來之後還原（原本唯讀的維持唯讀）----
    ctl["delay"] = 3.0
    _eval(send, "(function(){var t=document.getElementById('odDraft'); var i=t.value.indexOf('擬辦：')+6;"
                "t.focus(); t.setSelectionRange(i,i); t.dispatchEvent(new Event('keyup')); return 1;})()")
    assert _until(send, "document.getElementById('odRwTarget').textContent.indexOf('雷射印表機') >= 0", 10)
    _eval(send, "document.getElementById('odRwGo').click(), 1")
    assert _until(send, _OVERLAY_SHOWN, 5), "改寫中草稿上沒有轉圈"
    assert _eval(send, "document.getElementById('odDraftBusyLabel').textContent") == "改寫中…"
    assert _eval(send, "!!document.querySelector('#odRwGo .spinner') && "
                       "document.getElementById('odDraft').readOnly")
    assert not _eval(send, "document.getElementById('odResultBody').hasAttribute('inert')"), \
        "改寫一段不該把整塊鎖起來"
    assert _until(send, "!document.getElementById('odRwOut').hidden", 30)
    assert _until(send, _BUSY_OFF, 5), "改寫回來之後草稿區沒有還原"
    _eval(send, "document.getElementById('odRwReject').click(), 1")
    ctl["delay"] = 1.0
    _eval(send, "document.getElementById('odDraft').readOnly = true, 1")
    _eval(send, "document.getElementById('odRwGo').click(), 1")
    assert _until(send, _OVERLAY_SHOWN, 5)
    assert _until(send, "!document.getElementById('odRwOut').hidden && "
                        "document.getElementById('odDraftBusy').hidden", 30)
    assert _eval(send, "document.getElementById('odDraft').readOnly"), "原本就唯讀的草稿被打開了"
    _eval(send, "document.getElementById('odDraft').readOnly = false, 1")
    _eval(send, "document.getElementById('odRwReject').click(), 1")

    # ---- 5. 重新產生（成功）：畫面上沒存的修改先存成一版，新的草稿自動存成下一版 ----
    revs0 = _eval(send, "document.querySelectorAll('#odRevs li').length")
    _eval(send, "(function(){var t=document.getElementById('odDraft'); t.value += '\\n（重新產生前我改的）';"
                "t.dispatchEvent(new Event('input')); return 1;})()")
    fake.sign_draft = dict(SIGN_DRAFT, subject="汰換資訊室雷射印表機3台")
    ctl["delay"] = 2.0
    _eval(send, "document.getElementById('odRegen').click(), 1")
    assert _until(send, _BUSY_ON, 15)
    assert _eval(send, _OVERLAY_SHOWN)
    assert _until(send, "document.getElementById('odDraft').value.indexOf('印表機3台') >= 0", 60)
    assert _until(send, _BUSY_OFF + " && document.querySelectorAll('#odRevs li').length === %d" % (revs0 + 2),
                  15), "重新產生之後版本清單沒有多兩版（先存的修改＋重新產生的那一份）"
    srcs = _eval(send, "Array.from(document.querySelectorAll('#odRevs li .od-src'))"
                       ".map(function(x){return x.className;})")
    assert "od-src-regen" in srcs[0] and "od-src-edit" in srcs[1], srcs
    assert _until(send, "document.getElementById('odRevState').textContent.indexOf(%s) >= 0"
                  % _js(str(revs0 + 2)), 10)

    assert not errs, "主控台有 JS 例外：\n  " + "\n  ".join(errs)


def test_examples_and_custom_selects(live):
    port, send, errs, fake, ctl = live
    _open(port, send)

    # ---- 整頁的下拉都是本站樣式：原生的藏起來，外面包著 jt-select ----
    bad = _eval(send, """Array.from(document.querySelectorAll('#odRoot select, #odResult select')).filter(function(s){
      var w = s.parentElement; var r = s.getBoundingClientRect();
      return !(w && w.classList.contains('jt-select-wrap') && s.classList.contains('jt-select-native')
               && w.querySelector('.jt-select-trigger') && (r.width <= 1 || getComputedStyle(s).opacity === '0'));
    }).map(function(s){return s.id;})""")
    assert bad == [], f"這幾個下拉還是瀏覽器原生的：{bad}"
    assert _eval(send, "document.querySelectorAll('#odRoot select, #odResult select').length") >= 11

    # ---- 載入範例：在需求敘述正下方；點開是本站樣式的清單，依「發文身分 → 文別」分四組 ----
    assert _eval(send, "document.getElementById('odEx').closest('[data-ex-slot]').dataset.exSlot") == "sign"
    _click(send, "#odEx .jt-select-trigger")
    assert _until(send, "!document.querySelector('#odEx .jt-select-panel').hidden", 5), "範例清單點不開"
    groups = _eval(send, "Array.from(document.querySelectorAll('#odEx .jt-select-group'))"
                         ".map(function(g){return g.textContent;})")
    assert groups == [g[-1] for g in _examples().GROUPS], groups
    assert _eval(send, "document.querySelectorAll('#odEx .jt-select-option').length") == 53
    _click(send, "#odEx .jt-select-option[data-value='LETTER-01']")
    assert _until(send, "document.querySelector('input[name=odMode]:checked').value === 'letter'", 5), \
        "選了函的範例，模式沒有切到函"
    assert "個資保護教育訓練" in _eval(send, "document.getElementById('odLetterNarrative').value")
    assert _eval(send, "document.getElementById('odRelation').value") == "down"
    assert _eval(send, "document.querySelector('#odRelation').parentElement"
                       ".querySelector('.jt-select-label').textContent") == od.RELATIONS["down"]
    assert _eval(send, "!document.getElementById('odLetterClosing')._jtSelect.trigger.disabled")
    assert _eval(send, "document.getElementById('odExample').value") == "", "載入之後下拉沒有回到「選一個範例」"
    assert _eval(send, "document.querySelector('#odEx .jt-select-label').textContent") == "（選一個範例載入）"
    # 範例的下拉跟著搬到函的需求敘述下面、而且看得到
    assert _eval(send, "document.getElementById('odEx').closest('[data-ex-slot]').dataset.exSlot") == "letter"
    assert _eval(send, "document.getElementById('odEx').getBoundingClientRect().height > 10")
    # 要看得出來（2026-10-08 使用者：「載入範例 區 做明顯一點」）：有底色、有圖示、有一句提示
    assert _eval(send, "getComputedStyle(document.getElementById('odEx')).backgroundColor") not in (
        "rgba(0, 0, 0, 0)", "transparent"), "載入範例沒有底色，跟字數那一列分不開"
    assert _eval(send, "!!document.querySelector('#odEx .od-ex-lab svg') && "
                       "document.querySelector('#odEx .od-ex-tip').getBoundingClientRect().width > 0")
    assert not _eval(send, "!!document.querySelector('.modal-overlay')"), "欄位是空的卻先問了"
    assert _eval(send, "document.getElementById('odResult').hidden"), "載入範例不可以送出"
    assert _eval(send, "document.getElementById('odIssuer').value") == "agency"
    assert not _eval(send, "document.getElementById('odRelationRow').hidden")

    # ---- 企業發函的範例：發文身分跟著切成企業、行文關係收起來、期望語換成企業那一組 ----
    _click(send, "#odEx .jt-select-trigger")
    _click(send, "#odEx .jt-select-option[data-value='BIZ-LETTER-03']")
    assert _until(send, "!!document.querySelector('.modal-overlay .modal-ok')", 10), "函的需求敘述有字卻沒有先問"
    _eval(send, "document.querySelector('.modal-overlay .modal-ok').click(), 1")
    assert _until(send, "document.getElementById('odIssuer').value === 'company'", 10), "企業的範例沒有切發文身分"
    biz = next(e for e in _examples().EXAMPLES if e["key"] == "BIZ-LETTER-03")["fields"]["narrative"]
    assert _until(send, "document.getElementById('odLetterNarrative').value === %s" % _js(biz), 10)
    assert _eval(send, "document.getElementById('odRelationRow').hidden"), "企業的函還看得到行文關係"
    assert _eval(send, "document.getElementById('odOrgLab').textContent") == "公司名稱"
    assert _eval(send, "document.querySelector('#odIssuer').parentElement"
                       ".querySelector('.jt-select-label').textContent") == od.ISSUERS["company"]
    opts = _eval(send, "Array.from(document.getElementById('odLetterClosing').options).map(function(o){return o.value;})")
    assert opts == list(od.LETTER_CLOSINGS[od.COMPANY_RELATION]), opts
    assert _until(send, "document.getElementById('odSaluteSelf').textContent === '本公司'", 10), "自稱沒有變本公司"
    assert _eval(send, "document.getElementById('odSaluteTerm').textContent") == "貴機關"

    _click(send, "#odEx .jt-select-trigger")
    _click(send, "#odEx .jt-select-option[data-value='OPINION-01']")
    assert _until(send, "document.querySelector('input[name=odMode]:checked').value === 'endorse'", 5)
    ex = next(e for e in _examples().EXAMPLES if e["key"] == "OPINION-01")
    assert _eval(send, "document.getElementById('odSource').value") == ex["fields"]["source"]
    assert _eval(send, "document.getElementById('odDirection').value") == ex["fields"]["direction"]
    assert _eval(send, "document.getElementById('odLength2').value") == "short"
    assert _eval(send, "document.getElementById('odSourceCount').textContent").startswith(
        str(len(ex["fields"]["source"]))), "字數沒有跟著更新"

    # ---- 已經有不一樣的字：先問；取消就什麼都不動、同意才換 ----
    _eval(send, "document.querySelector('input[name=odMode][value=sign]').click(), 1")
    _set(send, "odNarrative", "我先打的字。")
    _click(send, "#odEx .jt-select-trigger")
    _click(send, "#odEx .jt-select-option[data-value='SIGN-02']")
    assert _until(send, "!!document.querySelector('.modal-overlay .modal-cancel')", 10), "有字卻沒有先問"
    assert _eval(send, "document.getElementById('odNarrative').value") == "我先打的字。"
    _eval(send, "document.querySelector('.modal-overlay .modal-cancel').click(), 1")
    assert _until(send, "!document.querySelector('.modal-overlay')", 10)
    assert _eval(send, "document.getElementById('odNarrative').value") == "我先打的字。", "取消了還是換掉"
    _click(send, "#odEx .jt-select-trigger")
    _click(send, "#odEx .jt-select-option[data-value='SIGN-02']")
    assert _until(send, "!!document.querySelector('.modal-overlay .modal-ok')", 10)
    _eval(send, "document.querySelector('.modal-overlay .modal-ok').click(), 1")
    sign02 = next(e for e in _examples().EXAMPLES if e["key"] == "SIGN-02")["fields"]["narrative"]
    assert _until(send, "document.getElementById('odNarrative').value === %s" % _js(sign02), 10)
    # 同一份再載一次（內容一樣）：不用問
    _click(send, "#odEx .jt-select-trigger")
    _click(send, "#odEx .jt-select-option[data-value='SIGN-02']")
    time.sleep(0.5)
    assert not _eval(send, "!!document.querySelector('.modal-overlay')")

    # ---- 期望語：行文關係還沒選時是停用的（畫面上那一份也要停用）----
    _eval(send, "document.querySelector('input[name=odMode][value=letter]').click(), 1")
    # 前面載過企業的範例 —— 切回公務機關：行文關係那一格回來（企業的期望語不會停用）
    _click(send, "#odIssuer + .jt-select-trigger")
    _click(send, "#odIssuer ~ .jt-select-panel .jt-select-option[data-value='agency']")
    assert _until(send, "!document.getElementById('odRelationRow').hidden", 5), "切回公務機關，行文關係沒有回來"
    assert _eval(send, "document.getElementById('odOrgLab').textContent") == "發文機關全銜"
    _eval(send, "(function(){var s=document.getElementById('odRelation'); s.value='';"
                "s.dispatchEvent(new Event('change')); return 1;})()")
    assert _eval(send, "document.getElementById('odLetterClosing')._jtSelect.trigger.disabled")
    _click(send, "#odRelation + .jt-select-trigger")
    _click(send, "#odRelation ~ .jt-select-panel .jt-select-option[data-value='up']")
    assert _until(send, "document.getElementById('odRelation').value === 'up'", 5), "行文關係用面板選不到"
    assert _eval(send, "!document.getElementById('odLetterClosing')._jtSelect.trigger.disabled")
    opts = _eval(send, "Array.from(document.querySelectorAll('#odLetterClosing ~ .jt-select-panel "
                       ".jt-select-option')).map(function(o){return o.dataset.value;})")
    assert opts == list(od.LETTER_CLOSINGS["up"]), opts

    # ---- 同一列的欄位一樣高、頂端對齊（下拉量的是畫面上那顆）----
    for mode in ("sign", "endorse", "letter"):
        _eval(send, "document.querySelector('input[name=odMode][value=%s]').click(), 1" % mode)
        rows = _eval(send, """(function(){ var out = [];
          document.querySelectorAll('.od-grid').forEach(function(g){
            if (!g.offsetParent) return;
            var rows = {};
            Array.from(g.children).forEach(function(cell){
              var c = cell.querySelector(':scope > input.field, :scope > .jt-select-wrap > .jt-select-trigger');
              if (!c) return;
              var top = Math.round(cell.getBoundingClientRect().top), r = c.getBoundingClientRect();
              (rows[top] = rows[top] || []).push({top: r.top, h: r.height});
            });
            Object.keys(rows).forEach(function(k){ out.push(rows[k]); });
          }); return out; })()""")
        multi = [r for r in rows if len(r) >= 2]
        assert multi, (mode, rows)
        for row in multi:
            assert max(c["top"] for c in row) - min(c["top"] for c in row) <= 1, (mode, row)
            assert max(c["h"] for c in row) - min(c["h"] for c in row) <= 1, (mode, row)

    assert not errs, "主控台有 JS 例外：\n  " + "\n  ".join(errs)


def test_field_history_remembers_values_after_a_successful_draft(live):
    port, send, errs, fake, ctl = live
    _open(port, send)
    _eval(send, "localStorage.removeItem('jtdt.od.hist.odUnit'), 1")
    _eval(send, "document.querySelector('input[name=odMode][value=sign]').click(), 1")
    fake.sign_draft = dict(SIGN_DRAFT)
    ctl["delay"] = 0.2
    _set(send, "odNarrative", NARRATIVE)
    _set(send, "odUnit", "測試資訊科")
    _eval(send, "document.getElementById('odGo').click(), 1")
    assert _until(send, "!document.getElementById('odResult').hidden && "
                        "document.getElementById('odDraft').value.indexOf('主旨：') >= 0", 60)
    assert _eval(send, "JSON.parse(localStorage.getItem('jtdt.od.hist.odUnit') || '[]')") == ["測試資訊科"]

    # 產生失敗：打的字不記
    fake.sign_draft = {"note": "我不照格式"}
    _set(send, "odUnit", "不該被記住")
    _eval(send, "document.getElementById('odGo').click(), 1")
    assert _until(send, "!document.getElementById('odCancel').hidden", 10)
    assert _until(send, "document.getElementById('odCancel').hidden", 60)
    assert _eval(send, "JSON.parse(localStorage.getItem('jtdt.od.hist.odUnit') || '[]')") == ["測試資訊科"], \
        "產生失敗的那一次也被記住了"

    # ---- 重新開頁：按鈕在輸入框裡的右緣，點開是本站樣式的清單 ----
    _open(port, send)
    assert _eval(send, "document.getElementById('odUnit').value") == ""
    geo = _eval(send, """(function(){var i=document.getElementById('odUnit').getBoundingClientRect();
      var b=document.querySelector('.fh-btn[data-fh-for=odUnit]').getBoundingClientRect();
      return [i.left, i.right, i.top, i.bottom, b.left, b.right, b.top, b.bottom];})()""")
    il, ir, it, ib, bl, br_, bt, bb = geo
    assert il < bl and br_ <= ir and it <= bt and bb <= ib and ir - br_ < 12, \
        f"按鈕不在輸入框裡的右緣：{geo}"
    _click(send, ".fh-btn[data-fh-for=odUnit]")
    assert _until(send, "(function(){var p=document.querySelector('#odUnit ~ .fh-panel');"
                        "return p && !p.hidden;})()", 5), "之前填過的清單點不開"
    assert _eval(send, "document.querySelector('#odUnit ~ .fh-panel').tagName") == "DIV"
    assert _eval(send, "!document.querySelector('#odUnit ~ .fh-panel select, #odUnit ~ .fh-panel datalist') "
                       "&& !document.getElementById('odUnit').getAttribute('list')"), "清單不可以是原生的"
    rows = _eval(send, "Array.from(document.querySelectorAll('#odUnit ~ .fh-panel .fh-row .fh-val'))"
                       ".map(function(x){return x.textContent;})")
    assert rows == ["測試資訊科"], rows
    _click(send, "#odUnit ~ .fh-panel .fh-row .fh-val")
    assert _until(send, "document.getElementById('odUnit').value === '測試資訊科' && "
                        "document.querySelector('#odUnit ~ .fh-panel').hidden", 5), "點了一筆沒有填回去"

    # ---- 刪掉一筆：清單不關、那一筆不見、存的也拿掉 ----
    _click(send, ".fh-btn[data-fh-for=odUnit]")
    assert _until(send, "!document.querySelector('#odUnit ~ .fh-panel').hidden", 5)
    _click(send, "#odUnit ~ .fh-panel .fh-del")
    time.sleep(0.3)
    assert _eval(send, "!document.querySelector('#odUnit ~ .fh-panel').hidden"), "按刪除就把清單關掉了"
    assert _eval(send, "document.querySelectorAll('#odUnit ~ .fh-panel .fh-row').length") == 0
    assert _eval(send, "document.querySelector('#odUnit ~ .fh-panel .fh-empty').textContent") == "還沒有填過的紀錄"
    assert _eval(send, "localStorage.getItem('jtdt.od.hist.odUnit')") is None
    _key(send, "Escape", vk=27)
    assert _until(send, "document.querySelector('#odUnit ~ .fh-panel').hidden", 5), "Esc 沒有關掉清單"

    # ---- 鍵盤：↓ 打開並移進清單、↓ 移到下一筆、Enter 選 ----
    _eval(send, "window.FieldHistory.push('jtdt.od.hist.odAddressee', '局長'), 1")
    _eval(send, "window.FieldHistory.push('jtdt.od.hist.odAddressee', '主任秘書'), 1")
    _eval(send, "document.querySelector('.fh-btn[data-fh-for=odAddressee]').focus(), 1")
    _key(send, "ArrowDown", vk=40)
    assert _until(send, "document.activeElement && document.activeElement.classList.contains('fh-row') && "
                        "document.activeElement.dataset.value === '主任秘書'", 5), "↓ 沒有打開並移到第一筆"
    _key(send, "ArrowDown", vk=40)
    _key(send, "Enter", vk=13)
    assert _until(send, "document.getElementById('odAddressee').value === '局長'", 5)
    _eval(send, "localStorage.removeItem('jtdt.od.hist.odAddressee'), 1")

    # 機關地址簿的建議（datalist）不受影響：受文者旁也有按鈕
    assert _eval(send, "!!document.querySelector('.fh-btn[data-fh-for=odReceiver]')")
    assert not errs, "主控台有 JS 例外：\n  " + "\n  ".join(errs)



#: 發文代字的紀錄：鍵＝前綴＋發文機關（NFKC、去空白）。測試自己寫一份，不借產品的函式。
_DW = "jtdt.od.docword."
_DOCNO_BTN = ".fh-btn[data-fh-for=odDocNo]"
_DOCNO_ROWS = ("Array.from(document.querySelectorAll('#odDocNo ~ .fh-panel .fh-row .fh-val'))"
               ".map(function(x){return x.textContent;})")


def _dismiss_modal(send):
    """這個實例沒有機關地址簿：第一次在機關欄位打字會跳一次「地址簿還沒下載」的提醒
    （正確行為），蓋住後面的按鈕 —— 照使用者那樣按確定。"""
    if _until(send, "!!document.querySelector('.modal-overlay .modal-ok')", 2):
        _eval(send, "document.querySelector('.modal-overlay .modal-ok').click(), 1")
        assert _until(send, "!document.querySelector('.modal-overlay')", 5)


_STARTS = "window.__fx.filter(function(u){return u.indexOf('/start') >= 0;}).length"


def _letter_done(send):
    """送出並等**這一次**做完。結果區上一份還開著，只看「有主旨」的話第二次送出立刻就成立
    （變異驗證時抓到：後面幾次根本沒等到）—— 要看到這一次的送出、進行中、結束。"""
    n = _eval(send, _STARTS)
    _eval(send, "document.getElementById('odGo').click(), 1")
    assert _until(send, f"{_STARTS} > {n}", 10), "沒有送出"
    assert _until(send, "!document.getElementById('odCancel').hidden", 10), "沒有看到進行中"
    assert _until(send, "document.getElementById('odCancel').hidden", 60)
    assert _eval(send, "document.getElementById('odDraft').value.indexOf('主旨：') >= 0"), "函沒有產生出來"


def test_doc_word_is_remembered_per_issuing_org_and_only_suggested(live):
    """發文代字（2026-10-10 使用者核准）：依發文機關記住「字第」前面那段，在發文字號旁
    當建議，**不自動填**、**不記號碼**（號碼是公文系統給的）。"""
    from tests.test_official_doc_tool import LETTER_DRAFT, LETTER_FACTS, LETTER_NARRATIVE
    port, send, errs, fake, ctl = live
    _open(port, send)
    _eval(send, "Object.keys(localStorage).forEach(function(k){"
                "if (k.indexOf('jtdt.od.') === 0 || k.indexOf('jtdt.officialDoc') === 0) localStorage.removeItem(k);}), 1")
    _open(port, send)
    fake.letter_facts, fake.letter_draft = dict(LETTER_FACTS), dict(LETTER_DRAFT)
    ctl["delay"] = 0.5          # 每次呼叫模型慢一點，「進行中」才看得到
    _eval(send, "document.querySelector('input[name=odMode][value=letter]').click(), 1")
    _set(send, "odLetterNarrative", LETTER_NARRATIVE)
    _eval(send, "(function(){var s=document.getElementById('odRelation'); s.value='up';"
                "s.dispatchEvent(new Event('change')); return 1;})()")
    _set(send, "odOrg", "嘉禾市 資訊局")              # 中間的空白不影響是哪一個機關
    _set(send, "odReceiver", "嘉禾市政府")
    _set(send, "odDocNo", "嘉資字第1150000123號")
    _letter_done(send)
    assert _eval(send, "JSON.parse(localStorage.getItem(%s) || '[]')" % _js(_DW + "嘉禾市資訊局")) == ["嘉資字第"]
    dump = _eval(send, "JSON.stringify(Object.keys(localStorage).map(function(k){return [k, localStorage.getItem(k)];}))")
    assert "1150000123" not in dump, "發文字號的號碼被記在瀏覽器裡了"

    # 沒有「字第」的（企業自己的編號）不記；佔位的○字第也不記
    _set(send, "odOrg", "嘉禾市環保局")
    for docno in ("A-2026-001", "○字第1150000001號"):
        _set(send, "odDocNo", docno)
        _letter_done(send)
    assert _eval(send, "localStorage.getItem(%s)" % _js(_DW + "嘉禾市環保局")) is None

    # ---- 重新開頁：不自動填；按鈕裡只有這個機關用過的 ----
    _open(port, send)
    _eval(send, "document.querySelector('input[name=odMode][value=letter]').click(), 1")
    _set(send, "odOrg", "嘉禾市資訊局")
    _dismiss_modal(send)
    _set(send, "odDocNo", "")
    time.sleep(0.3)
    assert _eval(send, "document.getElementById('odDocNo').value") == "", "發文字號被自動填了"
    _click(send, _DOCNO_BTN)
    assert _until(send, "!document.querySelector('#odDocNo ~ .fh-panel').hidden", 5), "代字清單點不開"
    assert _eval(send, _DOCNO_ROWS) == ["嘉資字第"]
    _click(send, "#odDocNo ~ .fh-panel .fh-row .fh-val")
    assert _until(send, "document.getElementById('odDocNo').value === '嘉資字第'", 5), "點了代字沒有填進去"

    # 別的機關：看不到嘉禾市資訊局的，講出「還沒有用過」
    _set(send, "odOrg", "嘉禾市環保局")
    _click(send, _DOCNO_BTN)
    assert _until(send, "!document.querySelector('#odDocNo ~ .fh-panel').hidden", 5)
    assert _eval(send, _DOCNO_ROWS) == [], "別的機關看到了不是它的代字"
    empty = _eval(send, "document.querySelector('#odDocNo ~ .fh-panel .fh-empty').textContent")
    assert empty == _eval(send, "document.querySelector('%s').dataset.fhEmpty" % _DOCNO_BTN) and "代字" in empty
    _key(send, "Escape", vk=27)
    # 還沒填機關：講出要先填
    _set(send, "odOrg", "")
    _click(send, _DOCNO_BTN)
    assert _until(send, "!document.querySelector('#odDocNo ~ .fh-panel').hidden", 5)
    nokey = _eval(send, "document.querySelector('#odDocNo ~ .fh-panel .fh-empty').textContent")
    assert nokey == _eval(send, "document.querySelector('%s').dataset.fhNokey" % _DOCNO_BTN), nokey
    _key(send, "Escape", vk=27)

    # ---- 切換成企業：「之前填過的」要讀企業那一組（按鈕的紀錄鍵是執行中才換的）----
    _eval(send, "window.FieldHistory.push('jtdt.od.hist.odOrg', '嘉禾市資訊局'), 1")
    _eval(send, "window.FieldHistory.push('jtdt.od.hist.odOrg.company', '範例資訊股份有限公司'), 1")
    _eval(send, "(function(){var s=document.getElementById('odIssuer'); s.value='company';"
                "s.dispatchEvent(new Event('change')); return 1;})()")
    _click(send, ".fh-btn[data-fh-for=odOrg]")
    assert _until(send, "!document.querySelector('#odOrg ~ .fh-panel').hidden", 5)
    rows = _eval(send, "Array.from(document.querySelectorAll('#odOrg ~ .fh-panel .fh-row .fh-val'))"
                       ".map(function(x){return x.textContent;})")
    assert rows == ["範例資訊股份有限公司"], f"切換成企業之後清單還是機關那一組：{rows}"
    _key(send, "Escape", vk=27)
    _eval(send, "(function(){var s=document.getElementById('odIssuer'); s.value='agency';"
                "s.dispatchEvent(new Event('change')); return 1;})()")
    _eval(send, "Object.keys(localStorage).forEach(function(k){"
                "if (k.indexOf('jtdt.od.') === 0 || k.indexOf('jtdt.officialDoc') === 0) localStorage.removeItem(k);}), 1")
    assert not errs, "主控台有 JS 例外：\n  " + "\n  ".join(errs)

_RW_KIND = "(document.querySelector('input[name=odRwKind]:checked') || {}).value"


def test_rewrite_kind_is_chosen_by_clicking_a_card(live):
    """改寫方式是一排卡片（2026-10-08 使用者：跟「版面加註」同一種樣式）：點卡片就選中、選中的有框線。"""
    port, send, errs, fake, ctl = live
    _open(port, send)
    _generate_first(send, fake, ctl)
    assert _eval(send, _RW_KIND) == "shorter", "預設要是「精簡」"
    _click(send, "#odRwKinds input[value=custom] + .opt-ic")
    assert _until(send, _RW_KIND + " === 'custom' && !document.getElementById('odRwInstr').hidden", 5), \
        "點「自訂」那張卡片沒有選中（或要求那一格沒出來）"
    # 選中的那張換底色（框線有 0.12 秒的轉場，剛點完量不準；底色沒有轉場）
    bg = "getComputedStyle(document.querySelector('#odRwKinds input[value=%s]').closest('.option-card')).backgroundColor"
    assert _eval(send, bg % "custom") != _eval(send, bg % "expand"), "選中的卡片要看得出來"
    _click(send, "#odRwKinds input[value=expand] + .opt-ic")
    assert _until(send, _RW_KIND + " === 'expand' && document.getElementById('odRwInstr').hidden", 5)
    # 卡片一樣大、不超出改寫那一區
    g = _eval(send, """(function(){var w=document.getElementById('odRw').getBoundingClientRect();
      var c=Array.from(document.querySelectorAll('#odRwKinds .option-card')).map(function(x){
        var r=x.getBoundingClientRect(); return [r.left, r.right, r.width, r.height];});
      return {w:[w.left, w.right], c:c};})()""")
    assert all(g["w"][0] - 1 <= c[0] and c[1] <= g["w"][1] + 1 for c in g["c"]), g
    assert len({round(c[2]) for c in g["c"]}) == 1, ("卡片要一樣寬", g)
    assert not errs, "主控台有 JS 例外：\n  " + "\n  ".join(errs)


# ------------------------------------------------------------------ 預覽圖：畫面

def _soffice():
    from app.core import office_convert
    return office_convert.find_soffice()


_RW_GEO = """(function(){
  var d = document.getElementById('odDraft').getBoundingClientRect();
  var w = document.getElementById('odRw').getBoundingClientRect();
  var p = document.getElementById('odPvPane').getBoundingClientRect();
  return {draft_bottom: d.bottom, rw_top: w.top, rw_bottom: w.bottom, rw_right: w.right,
          pv_top: p.top, pv_left: p.left, pv_bottom: p.bottom};})()"""


def test_preview_pane_shows_a_spinner_then_the_pages(live):
    if not _soffice():
        pytest.skip("這台沒有 Office 引擎")
    port, send, errs, fake, ctl = live
    _open(port, send, width=1920)
    # 轉圈有沒有出現過：在它還沒出現之前就盯著
    _eval(send, """(function(){ window.__pvSpin = false;
      var s = document.getElementById('odPvSpin');
      new MutationObserver(function(){ if (!s.hidden) window.__pvSpin = true; })
        .observe(s, {attributes: true, attributeFilter: ['hidden']});
      return 1; })()""")
    _generate_first(send, fake, ctl)
    assert _until(send, "window.__pvSpin", 15), "預覽圖還沒出來之前沒有轉圈"
    assert _until(send, "(function(){var i=document.querySelector('#odPreview img');"
                        "return !!i && i.naturalWidth > 0 && document.getElementById('odPvSpin').hidden;})()",
                  120), "預覽圖沒有出來（或轉圈沒有收掉）"
    h1 = _eval(send, "document.getElementById('odPreview').dataset.hash")
    assert h1 and re.fullmatch(r"[0-9a-f]{24}", h1)

    # 寬螢幕：草稿在左、預覽在右，兩邊都不會被擠扁
    g = _eval(send, """(function(){var a=document.getElementById('odDraft').getBoundingClientRect();
      var b=document.getElementById('odPvPane').getBoundingClientRect();
      return [a.left, a.right, b.left, b.right, document.documentElement.scrollWidth, innerWidth];})()""")
    assert g[2] >= g[1] - 1 and g[1] - g[0] >= 380 and g[3] - g[2] >= 380, g
    assert g[4] <= g[5] + 1, f"橫向捲軸：{g}"
    # 「改寫一段」緊接在草稿下面、在左欄（不跑到預覽下面去）
    r = _eval(send, _RW_GEO)
    assert r["rw_top"] >= r["draft_bottom"] - 1 and r["rw_right"] <= r["pv_left"] + 1, r
    assert r["rw_top"] - r["draft_bottom"] < 120, ("改寫一段離草稿太遠", r)
    # 左欄不可以比預覽短一大截（使用者 2026-10-08：拉寬時左邊空了一大塊）—— 草稿框要往下長
    assert r["rw_bottom"] >= r["pv_bottom"] - 40, ("左欄底下空了一大塊", r)
    # 兩欄各有一行標題、在同一個高度；草稿框與預覽框的上緣對齊（使用者 2026-10-08）
    a = _eval(send, """(function(){var h=document.querySelectorAll('#odResult .od-col-head');
      var t=document.getElementById('odDraft').getBoundingClientRect();
      var b=document.getElementById('odPvBox').getBoundingClientRect();
      return {heads: Array.from(h).map(function(x){return x.getBoundingClientRect().top;}),
              draft_top: t.top, box_top: b.top};})()""")
    assert len(a["heads"]) == 2 and abs(a["heads"][0] - a["heads"][1]) <= 1, ("兩欄標題沒對齊", a)
    assert abs(a["draft_top"] - a["box_top"]) <= 2, ("草稿框與預覽框的上緣沒對齊", a)

    # 改草稿 → 等停手之後換一份（新的雜湊）；換的時候舊圖留著、上面轉圈
    _eval(send, "(function(){var t=document.getElementById('odDraft'); t.value += '\\n（預覽要跟著換）';"
                "t.dispatchEvent(new Event('input')); return 1;})()")
    assert _until(send, "!document.getElementById('odPvSpin').hidden && "
                        "!!document.querySelector('#odPreview img')", 10), "換新的時候沒有轉圈 / 舊圖不見了"
    assert _until(send, "document.getElementById('odPreview').dataset.hash !== %s && "
                        "document.getElementById('odPvSpin').hidden" % _js(h1), 120), \
        "改了草稿，預覽圖沒有換"

    # 版面加註：簽沒有正本標示那一組；勾裝訂線 → 預覽跟著換（新的雜湊），選擇記在這個瀏覽器
    assert _eval(send, "document.getElementById('odExtrasLetter').hidden"), "簽也出現了函才有的加註"
    assert _eval(send, "!!document.querySelector('[data-od-dl=png]') && !!document.querySelector('[data-od-dl=svg]')")
    h2 = _eval(send, "document.getElementById('odPreview').dataset.hash")
    _click(send, "#odBinding")
    assert _until(send, "document.getElementById('odPreview').dataset.hash !== %s && "
                        "document.getElementById('odPvSpin').hidden" % _js(h2), 120), "勾了裝訂線，預覽圖沒有換"
    saved = _eval(send, "localStorage.getItem('jtdt.officialDoc.extras')")
    assert saved and json.loads(saved)["binding_line"] is True

    # 點一頁看大圖（共用的 lightbox）
    _click(send, "#odPreview img")
    assert _until(send, "!!document.querySelector('.jt-lightbox') && "
                        "!document.querySelector('.jt-lightbox').hidden", 5), "點了預覽圖沒有放大"
    _key(send, "Escape", vk=27)
    assert _until(send, "document.querySelector('.jt-lightbox').hidden", 5)

    # 1280（側欄開著）與手機寬度：疊起來、沒有橫向捲軸、兩邊都不會太窄
    for width, mobile, min_w in ((1280, False, 500), (390, True, 250)):
        send("Emulation.setDeviceMetricsOverride",
             {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": mobile})
        time.sleep(0.6)
        g = _eval(send, """(function(){var a=document.getElementById('odDraft').getBoundingClientRect();
          var b=document.getElementById('odPvPane').getBoundingClientRect();
          return [a.left, a.right, a.bottom, b.left, b.right, b.top,
                  document.documentElement.scrollWidth, innerWidth];})()""")
        assert g[5] >= g[2] - 1, (width, "沒有疊起來", g)
        assert g[1] - g[0] >= min_w and g[4] - g[3] >= min_w, (width, "被擠扁了", g)
        assert g[6] <= g[7] + 1, (width, "有橫向捲軸", g)
        # 疊起來時順序是 草稿 → 改寫一段 → 預覽（使用者 2026-10-08：改寫要在預覽圖上面）
        r = _eval(send, _RW_GEO)
        assert r["draft_bottom"] - 1 <= r["rw_top"] and r["rw_bottom"] <= r["pv_top"] + 1, (width, r)
    send("Emulation.setDeviceMetricsOverride",
         {"width": 1366, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    assert not errs, "主控台有 JS 例外：\n  " + "\n  ".join(errs)


# ------------------------------------------------------------------ 改寫一段：固定標示

_PIN = """(function(){
  var p = document.getElementById('odDraftPin'), w = document.getElementById('odDraftWrap'),
      t = document.getElementById('odDraft'), m = p.querySelector('mark');
  return {shown: !p.hidden && w.classList.contains('has-pin'), mark: m ? m.textContent : null,
          mark_bg: m ? getComputedStyle(m).backgroundColor : null,
          ta_bg: getComputedStyle(t).backgroundColor,
          pin_h: p.scrollHeight, ta_h: t.scrollHeight, pin_top: p.scrollTop, ta_top: t.scrollTop,
          same_text: p.textContent === t.value + '\\n',
          target: document.getElementById('odRwTarget').textContent};})()"""


def test_the_rewritten_passage_stays_marked_until_accepted_or_discarded(live):
    """選取一段按「改寫」之後，焦點跑到按鈕上，瀏覽器就不畫草稿裡的選取範圍了 ——
    結果還沒採用之前，看不出改的是哪一段；上方的說明還會跟著游標換成別段
    （畫面上寫的是主旨那句，下面的原本卻是說明一那段）。

    判準：結果出來、游標移到別處之後，草稿裡那一段仍然有底色，上方說明仍然是那一段；
    鏡像跟草稿框每一行在同一個地方換行（兩邊內容高度相同）、一起捲動；
    按「不用」或「採用」之後標示收掉。"""
    port, send, errs, fake, ctl = live
    _open(port, send)
    _generate_first(send, fake, ctl)
    sel = "常卡紙，維修廠商表示零件停產"
    _eval(send, "(function(){var t=document.getElementById('odDraft'), i=t.value.indexOf(%s);"
                "t.focus(); t.setSelectionRange(i, i + %d); t.dispatchEvent(new Event('select')); return i;})()"
          % (_js(sel), len(sel)))
    assert _until(send, "document.getElementById('odRwTarget').textContent.indexOf(%s) >= 0" % _js(sel), 5)
    ctl["delay"] = 1.5
    _eval(send, "document.getElementById('odRwGo').click(), 1")
    # 改寫中就標著（焦點已經在按鈕上）
    assert _until(send, "!document.getElementById('odDraftPin').hidden", 3), "按下改寫之後草稿裡沒有標出那一段"
    assert _eval(send, _PIN)["mark"] == sel
    assert _until(send, "!document.getElementById('odRwOut').hidden && "
                        "document.getElementById('odDraftBusy').hidden", 30)

    # 結果還沒採用：游標移到主旨那一行，標示與上方說明都不可以跟著換
    _eval(send, "(function(){var t=document.getElementById('odDraft'), i=t.value.indexOf('主旨：')+4;"
                "t.focus(); t.setSelectionRange(i,i); t.dispatchEvent(new Event('keyup')); "
                "document.getElementById('odRwAccept').focus(); return 1;})()")
    time.sleep(0.3)
    g = _eval(send, _PIN)
    assert g["shown"] and g["mark"] == sel, g
    assert g["mark_bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), g
    assert g["ta_bg"] in ("rgba(0, 0, 0, 0)", "transparent"), ("草稿框要透明，底下的標示才看得到", g)
    assert sel[:6] in g["target"] and "汰換資訊室" not in g["target"], ("上方說明跟著游標換了", g)
    assert abs(g["pin_h"] - g["ta_h"]) <= 2, ("鏡像跟草稿框換行的地方不一樣", g)
    assert g["same_text"], g

    # 結果還沒採用時在前面打字：標示跟著那幾個字走，鏡像的內容跟草稿一致
    _eval(send, "(function(){var t=document.getElementById('odDraft'); t.focus();"
                "t.setSelectionRange(0,0); return 1;})()")
    send("Input.insertText", {"text": "（前面加一行）\n"})
    time.sleep(0.3)
    g = _eval(send, _PIN)
    assert g["shown"] and g["mark"] == sel and g["same_text"], ("打字之後鏡像沒有跟著更新", g)

    # 一起捲動（草稿框改矮一點才捲得動）
    _eval(send, "(function(){var t=document.getElementById('odDraft'); t.style.minHeight='0';"
                "t.style.height='120px'; t.style.flex='none'; return 1;})()")
    time.sleep(0.3)
    _eval(send, "(function(){var t=document.getElementById('odDraft'); t.scrollTop=60;"
                "t.dispatchEvent(new Event('scroll')); return 1;})()")
    time.sleep(0.2)
    g = _eval(send, _PIN)
    assert g["ta_top"] > 0 and abs(g["pin_top"] - g["ta_top"]) <= 1, g
    assert abs(g["pin_h"] - g["ta_h"]) <= 2, ("草稿框改了高度之後鏡像沒有跟著", g)
    _eval(send, "(function(){var t=document.getElementById('odDraft'); t.style.minHeight='';"
                "t.style.height=''; t.style.flex=''; return 1;})()")

    # 「不用」：標示收掉，上方說明回到看游標
    _eval(send, "document.getElementById('odRwReject').click(), 1")
    time.sleep(0.2)
    g = _eval(send, _PIN)
    assert not g["shown"] and g["mark"] is None and "正在改寫" not in g["target"], g

    # 再改一次、按「採用」：換上去之後標示也收掉
    _eval(send, "(function(){var t=document.getElementById('odDraft'), i=t.value.indexOf(%s);"
                "t.focus(); t.setSelectionRange(i, i + %d); t.dispatchEvent(new Event('select')); return 1;})()"
          % (_js(sel), len(sel)))
    ctl["delay"] = 0.3
    _eval(send, "document.getElementById('odRwGo').click(), 1")
    assert _until(send, "!document.getElementById('odRwOut').hidden && "
                        "document.getElementById('odDraftBusy').hidden", 30)
    assert _eval(send, _PIN)["shown"]
    _eval(send, "document.getElementById('odRwAccept').click(), 1")
    assert _until(send, "document.getElementById('odDraftPin').hidden && "
                        "!document.getElementById('odDraftWrap').classList.contains('has-pin')", 5), \
        "採用之後標示沒有收掉"
    assert not errs, "主控台有 JS 例外：\n  " + "\n  ".join(errs)
