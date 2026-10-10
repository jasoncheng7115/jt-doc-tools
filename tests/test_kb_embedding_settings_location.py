"""知識庫的 Embedding（向量檢索）設定搬到「LLM 設定」頁（2026-10-08 使用者要求）＋ 知識庫標 Beta。

## 要守住的事

1. **只有一個家**：表單（沿用 LLM 伺服器、API 種類、位址、金鑰、模型、批次、逾時）只出現在 LLM 設定頁；
   知識庫頁不再有那份表單，而是**講出設定在哪裡、給一個連結**（大家會先去那裡找）。
2. **行為不變**：端點沒搬（`/admin/knowledge/api/embedding*`）、金鑰不回傳、存檔照樣寫稽核、
   測試連線、重建索引都接得上 —— 只看「頁面回 200」證明不了按鈕有接線，所以有一條真的瀏覽器。
3. **重建索引兩邊都有**：LLM 設定頁（改完模型接著按）＋ 知識庫頁的狀態那一區（狀態寫著
   「請按『重建索引』」的地方）。停用向量檢索只在知識庫頁（那是索引的事）。
4. 打開 LLM 設定頁**不可以建出知識庫的資料庫**（管理員可能從來沒用過知識庫）。
5. Beta：側欄「設定」裡的知識庫、知識庫頁標題、Embedding 那一區的標題、公文撰擬的「參考知識庫」。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

from tests._kb_support import FakeEmbed, kb_isolated  # noqa: F401
from tools import source_text

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "app" / "admin" / "templates"

#: 表單欄位 —— 只可以出現在 LLM 設定頁
FIELD_IDS = ("kbEmbInherit", "kbEmbKind", "kbEmbUrl", "kbEmbKey", "kbEmbModel",
             "kbEmbBatch", "kbEmbTimeout", "kbEmbTest")
#: 拿掉的（2026-10-08 使用者：「有講過不要用前綴」）—— 哪一頁都不可以再出現
GONE_IDS = ("kbEmbQp", "kbEmbDp", "kbEmbPreset")


# ------------------------------------------------------------------ 伺服器端（樣板與端點）

def _h(tag: str, html: str) -> str:
    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, re.S)
    assert m, f"找不到 <{tag}>"
    return m.group(1)


def test_the_form_lives_on_the_llm_settings_page(admin_session, kb_isolated):
    c, _, _ = admin_session
    html = c.get("/admin/llm-settings").text
    for fid in FIELD_IDS:
        assert f'id="{fid}"' in html, f"LLM 設定頁少了 {fid}"
    for fid in GONE_IDS:
        assert f'id="{fid}"' not in html, f"前綴的欄位 {fid} 又回來了"
    sec = html[html.index('id="embedding"'):]
    # 管理員不能自己填前綴；「前綴」兩個字只可以出現在說明裡（Nemotron-3-Embed 的前綴由系統
    # 自動套用，2026-10-09 起說明要講出來），不可以是欄位或欄位標題
    form = sec.split('id="llmSaveBar"')[0]
    for lab in re.findall(r"<label\b[^>]*>(.*?)</label>", form, re.S):
        assert "前綴" not in lab, f"前綴又變成可以填的欄位：{lab!r}"
    for tag in re.findall(r"<(?:input|textarea)\b[^>]*>", form):
        assert "prefix" not in tag.lower() and "前綴" not in tag, f"前綴又變成可以填的欄位：{tag}"
    no_advice = re.sub(r'<div class="info-box emb-advice">.*?</div>', "", form, flags=re.S)
    assert "前綴" not in no_advice, "前綴只可以出現在建議模型那一段說明裡"
    h2 = _h("h2", sec)
    assert "Embedding（向量檢索）設定" in h2 and 'class="tool-beta"' in h2, "那一區的標題要標 Beta"
    # 同一套表單樣式（管理區的設定頁一致）
    assert 'class="auth-form"' in sec and 'class="auth-section"' in sec and 'class="af-label"' in sec
    # **整頁只有一顆「儲存」**（2026-10-08 使用者：「原本已有一個儲存，embedding 加入後又有
    # 一個儲存，怪怪的」）—— 在這一區之後、寫明含 Embedding
    assert html.count('id="btn-save"') == 1, "LLM 設定頁有不只一顆儲存"
    assert 'id="kbEmbSave"' not in html and "儲存 Embedding 設定" not in html
    assert html.index('id="llmSaveBar"') > html.index('id="embedding"'), \
        "儲存要在 Embedding 那一區之後（看得出它也管那一區）"
    bar = re.search(r'id="llmSaveBar">(.*?)</div>', html, re.S).group(1)
    assert 'id="btn-save"' in bar and "含 Embedding" in bar


def test_the_knowledge_page_points_there_instead(admin_session, kb_isolated):
    c, _, _ = admin_session
    html = c.get("/admin/knowledge").text
    for fid in FIELD_IDS:
        assert f'id="{fid}"' not in html, f"知識庫頁還留著 {fid}（同一份設定兩個地方改）"
    assert 'href="/admin/llm-settings#embedding"' in html, "知識庫頁要講出設定搬到哪裡"
    assert 'class="tool-beta"' in _h("h1", html), "知識庫頁的標題要標 Beta"
    # 索引的操作留在知識庫頁
    assert 'id="kbRebuild"' in html and 'id="kbVecOff"' in html


def test_only_one_template_has_the_form():
    """同一份設定只能有一個家 —— 有人把表單抄回知識庫頁（或別頁）時要紅。"""
    owners = sorted(p.name for p in TPL.glob("*.html")
                    if 'id="kbEmbUrl"' in p.read_text(encoding="utf-8"))
    assert owners == ["llm_settings.html"], owners


def test_the_llm_page_section_has_its_own_script_and_does_not_reuse_the_main_one():
    """那一區的程式包在自己的函式裡：上面那一段的 `$`、`saveSettings` 等名字一個都不碰
    （同一頁另一個人會改上面那一段，各自獨立才不會互相弄壞）。"""
    s = (TPL / "llm_settings.html").read_text(encoding="utf-8")
    scripts = source_text.blocks(s, "script")
    emb = [x for x in scripts if "kbEmbForm" in x]
    assert len(emb) == 1, "Embedding 那一區要有自己的一段 <script>"
    body = emb[0]
    assert body.strip().startswith("(function") and "saveSettings" not in body
    assert not re.search(r"(?<![\w.])\$\(", body), "不要借用上面那一段的 $()"


def test_get_embedding_has_the_llm_server_and_rebuild_state(admin_session, kb_isolated):
    c, _, _ = admin_session
    j = c.get("/admin/knowledge/api/embedding").json()
    assert {"embed", "active", "configured", "needs_rebuild", "llm_server"} <= set(j), "原本的欄位要還在"
    assert "presets" not in j, "建議前綴拿掉了"
    assert j["embed"]["use_llm_server"] is True, "預設沿用 LLM 伺服器"
    assert j["rebuild"]["running"] is False


def test_opening_the_llm_page_does_not_create_the_knowledge_database(admin_session, kb_isolated):
    from app.core.kb import store
    c, _, _ = admin_session
    assert c.get("/admin/llm-settings").status_code == 200
    assert c.get("/admin/knowledge/api/embedding").status_code == 200
    assert not store.db_path().exists(), "只是打開 LLM 設定頁，就建出了知識庫的資料庫"


def test_rebuild_state_is_read_once_the_database_exists(admin_session, kb_isolated):
    """反向對照：資料庫在的話，重建的進度要讀得出來（不是一律回「沒在跑」）。"""
    from app.core.kb import indexer, store
    c, _, _ = admin_session
    store.meta_set("rebuild_state", json.dumps({"running": True, "done": 3, "total": 9}))
    try:
        rb = c.get("/admin/knowledge/api/embedding").json()["rebuild"]
        assert rb["running"] is True and rb["done"] == 3 and rb["total"] == 9
    finally:
        store.meta_set("rebuild_state", json.dumps({"running": False}))
    assert indexer.rebuild_state()["running"] is False


def test_the_key_is_never_shown_on_either_page(admin_session, kb_isolated):
    c, _, _ = admin_session
    r = c.post("/admin/knowledge/api/embedding",
               json={"kind": "openai", "base_url": "http://127.0.0.1:9", "model": "m",
                     "api_key": "sk-embed-NOSHOW-77"})
    assert r.status_code == 200, r.text
    for url in ("/admin/llm-settings", "/admin/knowledge", "/admin/knowledge/api/embedding",
                "/admin/knowledge/api/overview"):
        assert "NOSHOW" not in c.get(url).text, f"金鑰出現在 {url}"
    assert c.get("/admin/knowledge/api/embedding").json()["embed"]["api_key"] == "__KEPT__"


def test_saving_still_writes_the_audit_event_without_the_key(admin_session, kb_isolated):
    from app.core import audit_db
    c, admin, _ = admin_session
    start = int(audit_db.conn().execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM audit_events").fetchone()["m"])
    c.post("/admin/knowledge/api/embedding",
           json={"base_url": "http://127.0.0.1:9", "model": "m", "api_key": "sk-NOLOG-9"})
    rows = []
    end = time.time() + 5
    while time.time() < end and not rows:
        rows = audit_db.conn().execute(
            "SELECT username, details_json FROM audit_events WHERE event_type='settings_change' "
            "AND target='knowledge' AND id > ?", (start,)).fetchall()
        time.sleep(0.1)
    assert rows and rows[0]["username"] == admin
    assert "NOLOG" not in rows[0]["details_json"]
    assert json.loads(rows[0]["details_json"])["key_status"] == "已更新"


def test_plain_users_cannot_open_the_llm_page(admin_session, kb_isolated):
    """設定換了頁，權限不可以跟著鬆：LLM 設定頁本來就只給管理員。"""
    from fastapi.testclient import TestClient
    import app.main as m
    from app.core import sessions, user_manager
    # 使用者在 auth_off 收尾時一起清掉
    uid = user_manager.create_local("kb-plain", "一般", "PlainUser1234", roles=["default-user"])
    tok, _ = sessions.issue(uid, remember=False, ip="127.0.0.1", ua="pytest")
    u = TestClient(m.app)
    u.cookies.set(sessions.COOKIE_NAME, tok)
    for url in ("/admin/llm-settings", "/admin/knowledge/api/embedding"):
        r = u.get(url, follow_redirects=False)
        assert r.status_code in (302, 303, 403), (url, r.status_code)
        assert "kbEmbForm" not in r.text


# ------------------------------------------------------------------ 側欄與公文撰擬的 Beta

def test_only_the_knowledge_entry_is_marked_beta_in_the_settings_group(client, auth_off):
    import app.main as m
    betas = [it["url"] for it in m._NAV_SETTINGS_ALL if it.get("beta")]
    assert betas == ["/admin/knowledge"], betas
    home = client.get("/").text
    grp = re.search(r'<details class="sb-group"[^>]*data-sbkey="設定".*?</details>', home, re.S)
    assert grp, "找不到側欄的「設定」那一組"
    items = re.findall(r'<a class="sb-item" href="([^"]+)"(.*?)</a>', grp.group(0), re.S)
    assert items, "「設定」那一組一項都沒找到"
    for url, inner in items:
        assert ('class="tool-beta"' in inner) == (url == "/admin/knowledge"), url


def test_searching_for_embedding_finds_the_llm_settings_page():
    import app.main as m
    llm = next(it for it in m._NAV_SETTINGS_ALL if it["url"] == "/admin/llm-settings")
    for word in ("embedding", "嵌入", "向量"):
        assert word in llm["keywords"], f"搜「{word}」要找得到 LLM 設定頁"


def test_the_use_kb_checkbox_is_marked_beta(client, auth_off, monkeypatch):
    from app.core import kb
    monkeypatch.setattr(kb, "list_datasets", lambda *, user_id: [{"id": "x"}])
    html = client.get("/tools/official-doc/").text
    m = re.search(r'<label class="od-check">\s*<input type="checkbox" id="odUseKb">(.*?)</label>',
                  html, re.S)
    assert m, "找不到「參考公文知識庫」那個勾選框"
    assert "參考公文知識庫" in m.group(1) and 'class="tool-beta"' in m.group(1)


# ------------------------------------------------------------------ 真的瀏覽器

sys.path.insert(0, str(ROOT))
from tools.browser_probe import browser as _browser  # noqa: E402
from tools.browser_probe import profile_arg as _profile_arg  # noqa: E402

_needs_browser = pytest.mark.skipif(
    _browser() is None or __import__("importlib").util.find_spec("websockets") is None,
    reason="沒有 chromium / websockets —— 這條要真的瀏覽器才驗得到")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as r:
        return json.loads(r.read())


def _post(port: int, path: str, body: dict):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


@pytest.fixture(scope="module")
def live():
    data = tempfile.mkdtemp(prefix="kbemb-")
    port, cdp = _free_port(), _free_port()
    env = {**os.environ, "JTDT_DATA_DIR": data, "JTDT_CSRF_DISABLE": "1"}
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen(
        [_browser(), "--headless=new", "--no-sandbox", "--disable-gpu",
         _profile_arg(), f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fe = FakeEmbed("ollama").__enter__()
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
        yield port, cdp, fe, Path(data)
    finally:
        fe.__exit__(None, None, None)
        br.terminate()
        srv.terminate()
        try:
            br.wait(timeout=5)
            srv.wait(timeout=5)
        except Exception:
            br.kill()
            srv.kill()
        shutil.rmtree(data, ignore_errors=True)


class _Tab:
    """一個 CDP 分頁：送指令、跑 JS、等條件，順便收主控台的錯誤。"""

    def __init__(self, cdp: int):
        import websockets.sync.client as wsc
        req = urllib.request.Request(f"http://127.0.0.1:{cdp}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.tab = json.loads(r.read())
        self.cdp = cdp
        self.ws = wsc.connect(self.tab["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
        self.n = 0
        self.errs: list[str] = []
        for m in ("Runtime.enable", "Log.enable", "Page.enable"):
            self.send(m)

    def _collect(self, m: dict) -> None:
        if m.get("method") == "Runtime.exceptionThrown":
            d = m["params"]["exceptionDetails"]
            self.errs.append("例外：" + (d.get("exception", {}).get("description")
                                       or d.get("text", "?"))[:200])
        elif m.get("method") == "Log.entryAdded" and m["params"]["entry"].get("level") == "error":
            self.errs.append("主控台：" + m["params"]["entry"].get("text", "")[:200])

    def send(self, method, params=None):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv(timeout=60))
            if m.get("id") == self.n:
                return m
            self._collect(m)

    def js(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": True})
        return r.get("result", {}).get("result", {}).get("value")

    def wait_for(self, expr, timeout=20.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.js(expr):
                return True
            time.sleep(0.2)
        return False

    def go(self, url: str) -> None:
        self.send("Page.navigate", {"url": url})
        self.wait_for("document.readyState === 'complete'")
        time.sleep(0.3)

    def drain(self) -> None:
        try:
            while True:
                self._collect(json.loads(self.ws.recv(timeout=0.2)))
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.cdp}/json/close/{self.tab['id']}",
                                   timeout=5).read()
        except Exception:
            pass


def _msg(t: _Tab) -> str:
    return t.js("document.getElementById('kbEmbMsg').textContent") or ""


def _options(t: _Tab) -> list[str]:
    return t.js("[...document.getElementById('kbEmbModel').options].map(o => o.value)") or []


def _pick_model(t: _Tab, name: str) -> None:
    """模型從伺服器的清單挑（2026-10-08 使用者：「應該是列出 llm server 上符合的 model 不是自己填」）。"""
    assert t.wait_for(f"[...document.getElementById('kbEmbModel').options].some(o => o.value === {json.dumps(name)})"), \
        f"模型清單沒有列出 {name}：{_options(t)} / " + (t.js("document.getElementById('kbEmbModelNote').textContent") or "")
    assert "gemma4:26b" not in _options(t), "聊天用的模型不可以列進嵌入模型的清單"
    # 包在函式裡：頂層的 `const` 會留在頁面上，同一個分頁挑第二次就「已經宣告過」而整段不執行
    t.js(f"(function () {{ const s = document.getElementById('kbEmbModel'); s.value = {json.dumps(name)};"
         "s.dispatchEvent(new Event('change', {bubbles: true})); })()")
    assert t.js(f"document.getElementById('kbEmbModel').value === {json.dumps(name)}"), f"沒有選到 {name}"


@_needs_browser
def test_the_moved_section_works_in_a_browser(live):
    port, cdp, fe, data = live
    t = _Tab(cdp)
    try:
        t.go(f"http://127.0.0.1:{port}/admin/llm-settings")
        assert t.wait_for("(document.getElementById('kbEmbIndex').textContent || '').includes('只有關鍵字')"), \
            "Embedding 那一區沒有讀到狀態（JS 沒有跑起來？）"
        assert t.js("!!document.querySelector('#embedding h2 .tool-beta')")
        assert not (data / "knowledge" / "kb.sqlite").exists(), "打開 LLM 設定頁就建出了知識庫資料庫"

        # 預設沿用 LLM 伺服器：自己的位址欄位收起來、講出會送到哪裡
        assert t.js("document.getElementById('kbEmbInherit').checked"), "預設要沿用 LLM 伺服器"
        assert t.js("document.getElementById('kbEmbOwn').hidden"), "沿用時不必填位址"
        assert "LLM 伺服器" in (t.js("document.getElementById('kbEmbSrc').textContent") or "")
        # 改成另外指定一台 → 欄位出來
        t.js("const c = document.getElementById('kbEmbInherit'); c.click();")
        assert not t.js("document.getElementById('kbEmbOwn').hidden")
        assert t.js("document.getElementById('kbEmbSrc').hidden")
        t.js(f"""const u = document.getElementById('kbEmbUrl'); u.value = {json.dumps(fe.base)};
                 u.dispatchEvent(new Event('change', {{bubbles: true}}));""")
        _pick_model(t, "embeddinggemma:300m")
        assert "768" not in (t.js("document.getElementById('kbEmbModel').selectedOptions[0].textContent") or ""), \
            "沒有的資訊不可以寫出來"

        # 還沒存就按重建 → 擋下來、講出原因，而且真的沒有送出重建
        t.js("document.getElementById('kbRebuild').click()")
        assert t.wait_for("document.getElementById('kbEmbMsg').textContent.includes('還沒儲存')"), _msg(t)
        assert _get(port, "/admin/knowledge/api/embedding")["rebuild"]["running"] is False

        # 測試連線（照畫面上還沒存的設定）
        t.js("document.getElementById('kbEmbTest').click()")
        assert t.wait_for("document.getElementById('kbEmbMsg').textContent.includes('維度 256')"), _msg(t)

        # 整頁一顆「儲存」：改了欄位要亮「有變更還沒儲存」（挑模型時送了 change）
        assert t.js("!document.getElementById('llmDirty').hidden"), "改了欄位，「有變更還沒儲存」沒亮"

        # 這一區存不進去（位址不合格）→ LLM 設定照存，這一區分開講，「沒儲存」維持亮著
        t.js("document.getElementById('kbEmbUrl').value = 'ftp://bad.example';"
             "document.getElementById('btn-save').click()")
        assert t.wait_for("document.getElementById('kbEmbMsg').classList.contains('emb-err')"), _msg(t)
        assert _get(port, "/admin/knowledge/api/embedding")["embed"]["model"] == "", \
            "位址不合格卻存進去了"
        assert t.wait_for("!document.getElementById('btn-save').disabled")
        assert t.js("!document.getElementById('llmDirty').hidden"), \
            "Embedding 沒存進去，「有變更還沒儲存」卻熄了"
        # 這一步刻意讓伺服器回 400，主控台的那一行是預期的（其他的照樣要擋）
        t.drain()
        t.errs[:] = [e for e in t.errs if "status of 400" not in e]

        # 修好再按同一顆「儲存」（含金鑰）
        t.js(f"document.getElementById('kbEmbUrl').value = {json.dumps(fe.base)};"
             "document.getElementById('kbEmbKey').value = 'sk-browser-NOSHOW';"
             "document.getElementById('btn-save').click()")
        assert t.wait_for("document.getElementById('kbEmbMsg').textContent.includes('已儲存')"), _msg(t)
        assert t.wait_for("document.getElementById('llmDirty').hidden"), "存好了「有變更還沒儲存」還亮著"
        saved = _get(port, "/admin/knowledge/api/embedding")
        assert saved["embed"]["model"] == "embeddinggemma:300m"
        assert saved["embed"]["base_url"] == fe.base
        assert saved["embed"]["use_llm_server"] is False
        assert "query_prefix" not in saved["embed"]
        assert saved["embed"]["api_key"] == "__KEPT__"
        assert t.js("document.getElementById('kbEmbKey').value") == "__KEPT__", "金鑰欄位要變回替身字串"
        assert "已設定" in t.js("document.getElementById('kbEmbKeyHint').textContent")
        assert "NOSHOW" not in t.js("document.documentElement.outerHTML")
        assert t.wait_for("document.getElementById('kbEmbIndex').textContent.includes('請按「重建索引」')")

        # 重建索引 → 背景跑完、狀態變成「關鍵字＋向量」（輪詢有接上）
        t.js("document.getElementById('kbRebuild').click()")
        assert t.wait_for("document.getElementById('kbEmbMsg').textContent.includes('已開始重建索引')"), _msg(t)
        assert t.wait_for("document.getElementById('kbEmbIndex').textContent.includes('關鍵字＋向量')", 60), \
            t.js("document.getElementById('kbEmbIndex').textContent")
        assert (_get(port, "/admin/knowledge/api/embedding")["active"] or {}).get("fingerprint")
        assert any(p == "/api/embed" for p in fe.paths), "重建沒有打到嵌入服務"
        t.drain()
        assert not t.errs, "LLM 設定頁的主控台有錯誤：\n  " + "\n  ".join(t.errs)
    finally:
        t.close()


@_needs_browser
def test_the_knowledge_page_link_opens_the_section_even_if_it_was_collapsed(live):
    port, cdp, fe, _ = live
    t = _Tab(cdp)
    try:
        # 先把那一區收起來（收折狀態記在瀏覽器裡）
        t.go(f"http://127.0.0.1:{port}/admin/llm-settings")
        t.js("document.querySelector('#embedding h2').click()")
        assert t.js("document.getElementById('embedding').classList.contains('collapsed')"), \
            "收不起來 —— 這條的前提不成立"
        # 從知識庫頁按「前往 Embedding 設定」
        t.go(f"http://127.0.0.1:{port}/admin/knowledge")
        assert t.wait_for("(document.getElementById('kbStatus').textContent || '').includes('檢索方式')")
        href = t.js("document.getElementById('kbEmbGo').getAttribute('href')")
        assert href == "/admin/llm-settings#embedding"
        t.js("document.getElementById('kbEmbGo').click()")
        assert t.wait_for("location.pathname === '/admin/llm-settings' && document.readyState === 'complete'")
        assert t.wait_for("!!document.getElementById('kbEmbIndex') && "
                          "document.getElementById('kbEmbIndex').textContent.length > 4")
        assert not t.js("document.getElementById('embedding').classList.contains('collapsed')"), \
            "從連結過來，那一區還是收著的（只看得到標題）"
        t.drain()
        assert not t.errs, "主控台有錯誤：\n  " + "\n  ".join(t.errs)
    finally:
        t.close()


@_needs_browser
def test_the_knowledge_page_shows_index_actions_only_when_they_can_work(live):
    """沒設定嵌入服務時不給「重建索引」（按了只會得到一句錯誤）；設定好就有。"""
    port, cdp, fe, data = live
    t = _Tab(cdp)
    try:
        before = _get(port, "/admin/knowledge/api/embedding")["embed"]
        try:
            for configured in (False, True):
                _post(port, "/admin/knowledge/api/embedding",
                      {"base_url": fe.base if configured else "",
                       "model": "embeddinggemma:300m" if configured else ""})
                t.go(f"http://127.0.0.1:{port}/admin/knowledge")
                assert t.wait_for("(document.getElementById('kbStatus').textContent || '')"
                                  ".includes('檢索方式')")
                assert t.js("!document.getElementById('kbRebuild').hidden") == configured, configured
                # 狀態那一區（2026-10-08「這區也要改好看一點」）：三格、每格有圖示；設定好了但還沒重建 → 一格提示
                tiles = t.js("[...document.querySelectorAll('#kbStatus .kb-st-tile')].map((x) =>"
                             " [!!x.querySelector('.kb-st-top svg'), x.querySelector('.kb-st-val').textContent])")
                assert len(tiles) == 3 and all(svg for svg, _ in tiles), tiles
                # 照伺服器的狀態比（同一個模組前面的測試可能已經建好向量索引）
                ov = _get(port, "/admin/knowledge/api/overview")["status"]["embedding"]
                assert tiles[0][1] == ("關鍵字＋向量" if ov["vector_ready"] else "只有關鍵字"), (tiles, ov)
                assert tiles[2][1] == ("已設定" if configured else "還沒設定"), tiles
                note = t.js("(document.querySelector('#kbStatus .kb-st-notes') || {}).textContent || ''")
                assert ("請按「重建索引」" in note) == bool(ov["needs_rebuild"]), (note, ov)
                if ov["needs_rebuild"]:
                    assert t.js("!!document.querySelector('#kbStatus .kb-st-notes li svg')")
        finally:
            _post(port, "/admin/knowledge/api/embedding",
                  {"base_url": before["base_url"], "model": before["model"]})
        assert 'class="tool-beta"' in (t.js("document.querySelector('h1').innerHTML") or "")
        t.drain()
        assert not t.errs, "知識庫頁的主控台有錯誤：\n  " + "\n  ".join(t.errs)
    finally:
        t.close()


@_needs_browser
def test_inheriting_the_llm_server_works_in_a_browser(live):
    """預設沿用：只填模型就能測試連線與存檔；送去的是 LLM 設定裡那一台（2026-10-08 使用者要求）。"""
    port, cdp, fe, data = live
    p = data / "llm_settings.json"
    old = p.read_text(encoding="utf-8")
    cur = json.loads(old)
    cur["base_url"] = fe.base + "/v1"
    p.write_text(json.dumps(cur), encoding="utf-8")
    before = _get(port, "/admin/knowledge/api/embedding")["embed"]
    t = _Tab(cdp)
    try:
        t.go(f"http://127.0.0.1:{port}/admin/llm-settings")
        assert t.wait_for("(document.getElementById('kbEmbIndex').textContent || '').length > 4")
        if not t.js("document.getElementById('kbEmbInherit').checked"):
            t.js("document.getElementById('kbEmbInherit').click()")
        assert t.js("document.getElementById('kbEmbOwn').hidden")
        assert fe.base in (t.js("document.getElementById('kbEmbSrc').textContent") or ""), \
            "沒有講出會送到哪一台"
        _pick_model(t, "granite-embedding:278m")
        t.js("document.getElementById('kbEmbTest').click()")
        assert t.wait_for("document.getElementById('kbEmbMsg').textContent.includes('維度 256')"), _msg(t)
        t.js("document.getElementById('btn-save').click()")
        assert t.wait_for("document.getElementById('kbEmbMsg').textContent.includes('已儲存')"), _msg(t)
        saved = _get(port, "/admin/knowledge/api/embedding")
        assert saved["embed"]["use_llm_server"] is True and saved["embed"]["model"] == "granite-embedding:278m"
        assert saved["embed"]["kind"] == "ollama", "沒有照對方是不是 Ollama 決定種類"
        assert saved["configured"] is True
        t.drain()
        assert not t.errs, "主控台有錯誤：\n  " + "\n  ".join(t.errs)
    finally:
        t.close()
        _post(port, "/admin/knowledge/api/embedding",
              {"use_llm_server": False, "base_url": before["base_url"], "model": before["model"]})
        p.write_text(old, encoding="utf-8")


@_needs_browser
def test_picking_nemotron_says_what_is_added_automatically(live):
    """選了 Nemotron-3-Embed：模型下方講出系統自動加的前綴與上下文長度（伺服器算好的 `usage`）；
    換回別的模型那一行就收起來。建議模型的說明一直在（2026-10-09 使用者：「說明文字也要補充」）。"""
    port, cdp, _, _ = live
    nemo = "hf.co/Abiray/Nemotron-3-Embed-8B-GGUF:Q8_0"
    with FakeEmbed("ollama", models=[(nemo, ["embedding"]), ("embeddinggemma:300m", ["embedding"])]) as fe2:
        t = _Tab(cdp)
        try:
            t.go(f"http://127.0.0.1:{port}/admin/llm-settings")
            assert t.wait_for("!!(document.getElementById('kbEmbIndex').textContent || '').trim()"
                              " && !(document.getElementById('kbEmbIndex').textContent || '').includes('讀取中')")
            advice = t.js("document.querySelector('#embedding .emb-advice').textContent") or ""
            assert "建議使用 NVIDIA Nemotron-3-Embed-8B" in advice and "ollama pull" in advice, advice
            t.js("(function () { const c = document.getElementById('kbEmbInherit'); if (c.checked) c.click(); })()")
            t.js(f"""(function () {{ const u = document.getElementById('kbEmbUrl'); u.value = {json.dumps(fe2.base)};
                     u.dispatchEvent(new Event('change', {{bubbles: true}})); }})()""")
            _pick_model(t, nemo)
            assert t.wait_for("!document.getElementById('kbEmbUsage').hidden"), "選了 Nemotron 沒有講出自動加的前綴"
            txt = t.js("document.getElementById('kbEmbUsage').textContent") or ""
            assert '"query: "' in txt and '"passage: "' in txt, txt
            assert re.search(r"8,?192", txt), txt
            _pick_model(t, "embeddinggemma:300m")
            assert t.wait_for("document.getElementById('kbEmbUsage').hidden"), "換成別的模型那一行要收起來"
            t.drain()
            assert not t.errs, "主控台有錯誤：\n  " + "\n  ".join(t.errs)
        finally:
            t.close()


@_needs_browser
def test_the_model_row_and_the_advice_fill_the_section(live):
    """「嵌入模型」下拉與建議說明框原本限 560px，右邊空了一大塊（2026-10-10 使用者截圖）。
    判準：「重新整理清單」與說明框的右緣貼齊「嵌入服務」那一框的內緣（瀏覽器量）。"""
    port, cdp, _, _ = live
    t = _Tab(cdp)
    try:
        t.send("Emulation.setDeviceMetricsOverride",
               {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        # 帶 `#embedding`：同一個瀏覽器裡前面的測試可能把這一區收起來了（收折狀態記在瀏覽器裡），
        # 從錨點進來會自動展開（`test_the_knowledge_page_link_opens_the_section_even_if_it_was_collapsed`）
        t.go(f"http://127.0.0.1:{port}/admin/llm-settings#embedding")
        assert t.wait_for("document.getElementById('kbEmbModels').getBoundingClientRect().width > 0"), \
            "嵌入服務那一區沒有展開"
        g = t.js("""(function () {
          const f = document.getElementById('kbEmbModel').closest('fieldset'), cs = getComputedStyle(f);
          const inner = f.getBoundingClientRect().right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
          const btn = document.getElementById('kbEmbModels').getBoundingClientRect();
          const adv = document.querySelector('#embedding .emb-advice').getBoundingClientRect();
          const sel = document.getElementById('kbEmbModel').closest('.emb-model-row').getBoundingClientRect();
          return {inner: inner, btn: btn.right, adv: adv.right, row: sel.right, width: inner - sel.left};
        })()""")
        assert g["width"] > 700, ("這個寬度下這一框應該夠寬，量不到東西", g)
        assert abs(g["btn"] - g["inner"]) <= 2 and abs(g["row"] - g["inner"]) <= 2, ("下拉那一列右邊還有留白", g)
        assert abs(g["adv"] - g["inner"]) <= 2, ("建議說明框右邊還有留白", g)
    finally:
        t.close()
