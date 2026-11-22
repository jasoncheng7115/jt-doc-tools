"""「我的作業」按「開啟」回到工具頁：**畫面上要真的看到那件作業的結果**。

背景：背景作業帶著 `job.meta["view_url"]` 時，「我的作業」那一列才有「開啟」；
按下去回到工具頁（網址帶 `?job=<作業編號>`），工具頁要自己把那件作業的結果接回來畫。

**判準一定要落在畫面上**（本專案的規則）：

* 只驗「範本裡有讀 `?job=`」不算數 —— 讀了卻沒接上畫結果的程式，那種檢查一樣是綠的；
* 只驗 `hidden` 屬性也不算數 —— 結果區自己沒有 `hidden`，但它被包在一個還藏著的
  面板裡（上傳之後才打開的那一塊），使用者照樣什麼都看不到。

所以每一件都照使用者實際的路走一次：

1. 測試程式用 HTTP 打**頁面自己用的那幾支端點**（上傳 / 載入 → 送出），等作業完成；
2. 確認作業的 `meta.view_url` 是預期的網址，「我的作業」的清單也給「開啟」（`view_ok`）；
3. 真的瀏覽器打開「我的作業」，找到那一列的「開啟」**按下去**；
4. 等工具頁把結果畫出來：結果區**真的看得見**（`checkVisibility()`＋有大小，
   祖先藏著也算看不見），而且內容是**這件作業**的數字（大小、頁數、書籤數、
   亂數種子、OCR 全文、引擎與格式…），不是預設的「—」；
5. 整段過程頁面不可以丟出未處理的 JS 例外。

另外一條反向檢查：網址帶亂寫的 `?job=`（`../x`、不存在的編號）時，頁面不可以壞、
結果區要維持看不見。

素材全部是當場用 PyMuPDF / Pillow 產的杜撰內容。OCR 固定用 Tesseract（`eng`）：
這台開發機沒有 AVX2，本機 EasyOCR 一跑就讓服務 SIGILL。
"""
from __future__ import annotations

import io
import json
import os
import shutil
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
from tools import browser_probe  # noqa: E402
from tools.browser_probe import profile_arg as _profile_arg  # noqa: E402

pytestmark = pytest.mark.skipif(
    browser_probe.browser() is None
    or __import__("importlib").util.find_spec("websockets") is None,
    reason="沒有 chromium / websockets —— 這條要真的瀏覽器才驗得到")

#: 作業完成的等待上限。轉檔走 soffice，主機滿載時一件可以拖到一兩分鐘。
JOB_SECS = 300
#: 畫面把結果畫出來的等待上限（從按下「開啟」起算）。
PAGE_SECS = 90

#: 每一件的耗時（秒），跑完印出來（`-s` 看得到）。
TIMINGS: dict[str, dict] = {}


# ------------------------------------------------------------------ 素材 --

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _pdf(pages: int = 2, sizes: list[tuple[float, float]] | None = None) -> bytes:
    """杜撰內容的小 PDF；`sizes` 可以給每一頁不同的紙張大小（點）。"""
    import fitz
    doc = fitz.open()
    for i in range(pages):
        w, h = (sizes[i] if sizes else (595, 842))
        pg = doc.new_page(width=w, height=h)
        pg.insert_text((72, 90), f"Sample page {i + 1}", fontsize=18)
        pg.insert_text((72, 130), "Lorem ipsum dolor sit amet, consectetur.", fontsize=12)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def _scanned_pdf() -> bytes:
    """一頁**只有影像、沒有文字層**的 PDF（模擬掃描件），給 OCR 用。"""
    import fitz
    src = fitz.open()
    pg = src.new_page(width=595, height=842)
    pg.insert_text((60, 160), "HELLO OCR TEST", fontsize=40)
    pg.insert_text((60, 240), "INVOICE 2026", fontsize=40)
    pix = pg.get_pixmap(dpi=200, alpha=False)
    src.close()
    doc = fitz.open()
    out_pg = doc.new_page(width=595, height=842)
    out_pg.insert_image(out_pg.rect, stream=pix.tobytes("png"))
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def _stamp_png() -> bytes:
    """紅色圓章（透明底），給騎縫章的「上傳印章圖」。"""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((10, 10, 230, 230), outline=(200, 20, 20, 255), width=14)
    d.rectangle((80, 80, 160, 160), fill=(200, 20, 20, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _fmt(n: int) -> str:
    """同 pdf_compress.html 的 `fmt()`（畫面上大小的寫法）。"""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 / 1024 / 1024:.2f} GB"


# ---------------------------------------------------------------- 實例 --

@pytest.fixture(scope="module")
def live():
    import logging

    import httpx

    logging.getLogger("httpx").setLevel(logging.WARNING)   # 每個請求一行，失敗時洗掉真正的訊息

    data = Path(tempfile.mkdtemp(prefix="myjobs-open-e2e-"))
    # **要明寫「認證關閉」** —— 資料庫裡一有使用者，產品的 fail-secure 就會
    # 自動改用本機認證，整站變成登入頁（v1.15.49 記過）。
    (data / "auth_settings.json").write_text(json.dumps({"backend": "off"}), encoding="utf-8")
    # OCR 固定 Tesseract：這台沒有 AVX2，本機 EasyOCR 會讓整個服務 SIGILL。
    (data / "ocr_settings.json").write_text(json.dumps({"engine": "tesseract"}), encoding="utf-8")

    port, cdp = _free_port(), _free_port()
    env = {**os.environ, "JTDT_DATA_DIR": str(data), "JTDT_CSRF_DISABLE": "1"}
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen(
        [browser_probe.browser(), "--headless=new", "--no-sandbox", "--disable-gpu",
         "--window-size=1280,1000", _profile_arg(), f"--remote-debugging-port={cdp}",
         "--remote-allow-origins=*", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    http = None
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

        http = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180)
        yield {"port": port, "cdp": cdp, "http": http, "data": data}
    finally:
        if TIMINGS:
            print("\n[我的作業「開啟」耗時（秒）]")
            for k, v in TIMINGS.items():
                print(f"  {k:16s} {json.dumps(v, ensure_ascii=False)}")
        if http is not None:
            http.close()
        # 只收自己起的那兩個行程（不可以用名字批次殺 —— 這台機器有別的專案在跑）
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
    """一個**全新的分頁**（每一件各開一個，用完就關）。

    共用同一個分頁的話，上一件的頁面（例如 OCR 結果裡內嵌的 PDF 檢視器）
    還在收尾時下一件就導覽過去，實測偶爾整個導覽卡住 180 秒等不到回應 ——
    那是測試環境的狀態殘留，不是要驗的東西，所以每一件從乾淨的分頁開始。
    """

    def __init__(self, cdp: int):
        import websockets.sync.client as wsc
        req = urllib.request.Request(f"http://127.0.0.1:{cdp}/json/new?about:blank",
                                     method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.info = json.loads(r.read())
        self.cdp = cdp
        self.ws = wsc.connect(self.info["webSocketDebuggerUrl"], max_size=None,
                              open_timeout=10)
        self.n = 0
        self.errs: list[str] = []     # 頁面丟出的未處理 JS 例外（含 promise 沒接住的）
        self.recent: list[str] = []   # 最近收到的事件名稱（卡住時帶出來看）
        self.send("Page.enable")
        self.send("Runtime.enable")

    def send(self, method, params=None):
        self.n += 1
        i = self.n
        self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        while True:
            try:
                m = json.loads(self.ws.recv(timeout=180))
            except TimeoutError:
                raise AssertionError(
                    f"CDP {method} {params} 等不到回應；最近的事件：{self.recent[-15:]}")
            if m.get("method"):
                self.recent.append(m["method"])
                del self.recent[:-50]
            if m.get("method") == "Runtime.exceptionThrown":
                d = m["params"]["exceptionDetails"]
                self.errs.append(((d.get("exception") or {}).get("description")
                                  or d.get("text") or str(d))[:400])
            if m.get("id") == i:
                return m

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{self.cdp}/json/close/{self.info['id']}", timeout=5).read()
        except Exception:
            pass


@pytest.fixture
def tab(live):
    t = _Tab(live["cdp"])
    try:
        yield t
    finally:
        t.close()


def _eval(send, expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                  "awaitPromise": True})
    return (r.get("result", {}).get("result", {}) or {}).get("value")


def _wait(send, expr, secs=PAGE_SECS):
    end = time.time() + secs
    while time.time() < end:
        if _eval(send, expr):
            return True
        time.sleep(0.4)
    return False


def _ok(r, what: str):
    assert r.status_code == 200, f"{what}：HTTP {r.status_code} {r.text[:300]}"
    return r.json()


def _wait_job(http, jid: str, secs: int = JOB_SECS) -> dict:
    end = time.time() + secs
    j: dict = {}
    while time.time() < end:
        r = http.get(f"/api/jobs/{jid}")
        if r.status_code == 200:
            j = r.json()
            if j.get("status") in ("done", "error", "cancelled", "interrupted"):
                break
        time.sleep(1)
    assert j.get("status") == "done", (
        f"作業沒有完成：status={j.get('status')} error={j.get('error')} "
        f"message={j.get('message')}")
    return j


# ---------------------------------------------------------- 畫面的判準 --

#: 一個元素「使用者真的看得到」才回它畫面上的文字（`innerText`），否則回 null。
#: `checkVisibility()` 會把祖先的 `hidden` / `display:none` 一起算進去 ——
#: 結果區自己沒藏、但被包在還藏著的面板裡，一樣算看不見。
_VIS_FN = r"""
function __vis(sel) {
  const e = document.querySelector(sel);
  if (!e) return null;
  const v = e.checkVisibility
    ? e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})
    : !!e.offsetParent;
  const r = e.getBoundingClientRect();
  if (!v || r.width < 1 || r.height < 1) return null;
  return e.innerText;
}
"""


def _visible_text(send, sel: str):
    return _eval(send, "(function(){" + _VIS_FN + f"return __vis({json.dumps(sel)});}})()")


def _all_visible_with(checks: list[tuple[str, list[str]]]) -> str:
    """JS：每個選擇器都看得到、而且文字含有每一個期望的字串 → true。"""
    return ("(function(){" + _VIS_FN
            + "const cs=" + json.dumps(checks, ensure_ascii=False) + ";"
            + "return cs.every(([s, subs]) => { const t = __vis(s);"
            + " return t !== null && subs.every(x => t.includes(x)); });})()")


def _why_not(send, checks) -> str:
    """等不到的時候把畫面狀態帶出來：哪一層藏著、實際寫了什麼。"""
    out = []
    for sel, subs in checks:
        info = _eval(send, "(function(){" + _VIS_FN + "const s=" + json.dumps(sel) + ";"
                     "const e=document.querySelector(s); if(!e) return {missing:true};"
                     "const hid=[]; for(let p=e;p&&p!==document.body;p=p.parentElement){"
                     " if(p.hidden||getComputedStyle(p).display==='none')"
                     "  hid.push(p.tagName+(p.id?'#'+p.id:'')+(p.className?'.'+String(p.className).split(' ')[0]:''));}"
                     "return {visible: __vis(s)!==null, hidden_chain: hid,"
                     " text: (e.textContent||'').replace(/\\s+/g,' ').slice(0,300)};})()")
        out.append(f"{sel} 期望含 {subs} → {info}")
    st = _eval(send, "(function(){const s=document.querySelector('.job-status,[id$=Status],#status');"
                     "return s?s.textContent.trim().slice(0,200):null;})()")
    out.append(f"網址＝{_eval(send, 'location.pathname+location.search')}；狀態文字＝{st!r}")
    return "\n  ".join(out)


# ------------------------------------------------------------- 各工具 --
# 每一支回傳：作業編號、預期的 view_url、畫面上要看得到的東西（選擇器＋文字）、
# 另外要成立的 JS 條件（例如預覽圖真的載入）、下載鈕的選擇器（沒有就 None）。
# 端點與欄位一律照**頁面自己送的**（讀過各工具範本的 fetch() 與 router 的 Form 參數）。

def _case_pdf_compress(http):
    up = _ok(http.post("/tools/pdf-compress/analyze",
                       files={"file": ("sample.pdf", _pdf(3), "application/pdf")}), "analyze")
    jid = _ok(http.post("/tools/pdf-compress/submit",
                        json={"upload_id": up["upload_id"], "preset": "balanced"}),
              "submit")["job_id"]
    job = _wait_job(http, jid)
    sm = job["meta"]["summary"]
    checks = [
        ("#resultPanel", ["壓縮完成"]),
        ("#rOrig", [_fmt(sm["orig_size"])]),
        ("#rNew", [_fmt(sm["final_size"])]),
        ("#rPct", [f"{sm['saved_pct']}%"]),
    ]
    return jid, f"/tools/pdf-compress/?job={jid}", checks, [], "#cmpJob .job-download"


def _case_pdf_bookmark(http):
    up = _ok(http.post("/tools/pdf-bookmark/load",
                       files=[("files", ("report.pdf", _pdf(3), "application/pdf"))]), "load")
    marks = [{"title": "第一章 緒論", "page": 1, "level": 1},
             {"title": "1.1 背景", "page": 2, "level": 2},
             {"title": "第二章 方法", "page": 3, "level": 1}]
    jid = _ok(http.post("/tools/pdf-bookmark/submit", data={
        "upload_id": up["upload_id"], "bookmarks": json.dumps(marks, ensure_ascii=False),
        "toc_page": "false", "toc_title": "目錄", "toc_max_level": "3",
        "toc_dots": "true", "toc_at": "1", "filename": "report.pdf"}), "submit")["job_id"]
    job = _wait_job(http, jid)
    res = job["meta"]["bookmark_result"]
    assert res["bookmark_count"] == 3 and res["page_count"] == 3, res
    checks = [("#bmResult", ["3 個書籤", "共 3 頁"])]
    return jid, f"/tools/pdf-bookmark/?job={jid}", checks, [], "#bmJob .job-download"


def _case_pdf_page_size(http):
    # 一頁 Letter、兩頁 A4 → 統一成 A4：調整 1 頁、2 頁原本就對
    pdf = _pdf(3, sizes=[(612, 792), (595, 842), (595, 842)])
    up = _ok(http.post("/tools/pdf-page-size/load",
                       files={"file": ("mixed.pdf", pdf, "application/pdf")}), "load")
    jid = _ok(http.post("/tools/pdf-page-size/submit", data={
        "upload_id": up["upload_id"], "paper": "a4", "custom_w_mm": "210",
        "custom_h_mm": "297", "orientation": "auto", "fit": "scale",
        "align": "center", "keep_same": "true", "filename": "mixed.pdf"}), "submit")["job_id"]
    job = _wait_job(http, jid)
    r = job["meta"]["resize_result"]
    assert r["total"] == 3 and r["changed"] >= 1, r
    checks = [("#psResult", [f"共 {r['total']} 頁", f"調整 {r['changed']} 頁"])]
    return jid, f"/tools/pdf-page-size/?job={jid}", checks, [], "#psJob .job-download"


def _case_pdf_seam_stamp(http):
    up = _ok(http.post("/tools/pdf-seam-stamp/load",
                       files={"file": ("contract.pdf", _pdf(4), "application/pdf")}), "load")
    uid = up["upload_id"]
    _ok(http.post("/tools/pdf-seam-stamp/stamp-upload", data={"upload_id": uid},
                  files={"file": ("stamp.png", _stamp_png(), "image/png")}), "stamp-upload")
    jid = _ok(http.post("/tools/pdf-seam-stamp/submit", data={
        "upload_id": uid, "source": "upload", "asset_id": "", "text": "騎縫章",
        "shape": "circle", "color": "#c81414", "double_ring": "true", "mode": "side",
        "group": "2", "edge": "right", "size_mm": "30", "offset_mm": "3", "pos_mm": "0",
        "angle_deg": "0", "opacity": "1", "jitter_pos": "true", "jitter_pos_mm": "8",
        "jitter_angle": "false", "jitter_angle_deg": "4", "seed": "24680",
        "filename": "contract.pdf"}), "submit")["job_id"]
    job = _wait_job(http, jid)
    r = job["meta"]["seam_result"]
    assert r["seed"] == 24680 and r["groups"] == 2, r
    checks = [("#smResult", [f"{r['groups']} 組騎縫章", f"每組 {r['per_group']} 頁",
                             "亂數種子 24680"])]
    return jid, f"/tools/pdf-seam-stamp/?job={jid}", checks, [], "#smJob .job-download"


def _case_pdf_to_slides(http):
    up = _ok(http.post("/tools/pdf-to-slides/upload",
                       files={"file": ("deck.pdf", _pdf(2, sizes=[(842, 595)] * 2),
                                       "application/pdf")}), "upload")
    jid = _ok(http.post("/tools/pdf-to-slides/submit",
                        json={"upload_id": up["upload_id"], "output_format": "pptx"}),
              "submit")["job_id"]
    _wait_job(http, jid)
    checks = [("#resultPanel", ["轉換完成"]),
              ("#rEngine", ["jtdt-layout"]),
              ("#rFormat", ["PPTX"]),
              ("#rPreview", ["第 1 頁"])]
    extra = ["(function(){const i=document.querySelector('#rPreviewPages img[data-kind=orig]');"
             "return !!(i && i.complete && i.naturalWidth > 0);})()"]
    return jid, f"/tools/pdf-to-slides/?job={jid}", checks, extra, "#rDownload"


def _case_markdown_to_doc(http):
    md = "# 季度報告\n\n本文件為測試用的杜撰內容。\n\n## 摘要\n\n- 第一點\n- 第二點\n"
    r = _ok(http.post("/tools/markdown-to-doc/convert", data={
        "theme": "classic", "font": "default", "title": "quarterly-report",
        "text": md, "formats": "pdf"}), "convert")
    jid, uid = r["job_id"], r["upload_id"]
    _wait_job(http, jid)
    res = _ok(http.get(f"/tools/markdown-to-doc/result/{uid}"), "result")
    assert res["page_count"] >= 1, res
    checks = [("#md2ResultPanel", ["結果"]),
              ("#md2Stat", [res["stem"], str(res["page_count"]), str(res["char_count"])]),
              ("#md2Downloads", ["下載 PDF"])]
    extra = [f"document.querySelectorAll('#md2Previews .md2-preview-card img').length === {res['page_count']}",
             "!!document.querySelector('#md2Downloads a[href*=\"/download/\"][href$=\"/pdf\"]')"]
    return jid, f"/tools/markdown-to-doc/?job={jid}&uid={uid}", checks, extra, "#md2Downloads a.btn-primary"


def _case_pdf_ocr(http):
    up = _ok(http.post("/tools/pdf-ocr/upload",
                       files={"file": ("scan.pdf", _scanned_pdf(), "application/pdf")}), "upload")
    uid = up["upload_id"]
    jid = _ok(http.post(f"/tools/pdf-ocr/run/{uid}", data={
        "langs": "eng", "dpi": "200", "skip_pages_with_text": "true",
        "use_llm": "false", "use_llm_vision": "false", "use_llm_direct": "false",
        "use_llm_align": "false", "use_llm_full": "false"}), "run")["job_id"]
    job = _wait_job(http, jid)
    st = job["meta"]["stats"]
    assert st.get("pages_ocrd") == 1 and st.get("words_inserted", 0) > 0, (
        f"OCR 沒辨識出字（素材或引擎問題，不是「開啟」的問題）：{st}")
    checks = [("#resultBox .po-result",
               ["完成", f"處理 {st['pages_ocrd']} / {st['pages_total']} 頁",
                f"插入文字 {st['words_inserted']} 字"]),
              ("#resultBox details.po-fulltext summary", ["識別結果全文"])]
    # 全文在收合的 <details> 裡（畫面預設不展開）→ 用 textContent 看內容有沒有接回來
    extra = ["(function(){const p=document.getElementById('poFullTextPre');"
             "return !!(p && /HELLO/i.test(p.textContent) && /INVOICE/i.test(p.textContent));})()",
             f"!!document.querySelector('#resultBox a[href*=\"{uid}\"]')"]
    return jid, f"/tools/pdf-ocr/?job={jid}", checks, extra, "#resultBox a[download]"


def _case_pdf_to_office(http):
    up = _ok(http.post("/tools/pdf-to-office/upload",
                       files={"file": ("memo.pdf", _pdf(2), "application/pdf")}), "upload")
    jid = _ok(http.post("/tools/pdf-to-office/submit", json={
        "upload_id": up["upload_id"], "output_format": "docx",
        "engine": "pdf2docx-refine", "enable_postprocess": False, "fixer_opts": {}}),
        "submit")["job_id"]
    _wait_job(http, jid)
    full = _ok(http.get(f"/tools/pdf-to-office/result/{jid}"), "result")
    sm = full["summary"]
    eng = (sm.get("report") or {}).get("primary_engine") or sm.get("engine")
    assert eng, full
    checks = [("#resultPanel", ["轉換完成"]),
              ("#rEngine", [str(eng)]),
              ("#rFormat", ["DOCX"])]
    extra = [f"(document.getElementById('rDownload').getAttribute('href')||'').startsWith('/api/jobs/{jid}/download/')"]
    return jid, f"/tools/pdf-to-office/?job={jid}", checks, extra, "#rDownload"


CASES = {
    "pdf-compress": _case_pdf_compress,
    "pdf-bookmark": _case_pdf_bookmark,
    "pdf-page-size": _case_pdf_page_size,
    "pdf-seam-stamp": _case_pdf_seam_stamp,
    "pdf-to-slides": _case_pdf_to_slides,
    "markdown-to-doc": _case_markdown_to_doc,
    "pdf-ocr": _case_pdf_ocr,
    "pdf-to-office": _case_pdf_to_office,
}

#: 需要 Office 引擎（soffice）才跑得動的
_NEEDS_OFFICE = {"pdf-to-slides", "markdown-to-doc", "pdf-to-office"}


def _office_ok() -> bool:
    try:
        from app.core import office_convert
        return bool(office_convert.find_soffice())
    except Exception:  # noqa: BLE001
        return bool(shutil.which("soffice") or Path("/opt/oxoffice").exists())


# ---------------------------------------------------------------- 測試 --

@pytest.mark.parametrize("tool", list(CASES))
def test_open_from_my_jobs_shows_the_result(live, tab, tool):
    """作業完成 → 「我的作業」按「開啟」→ 工具頁上**看得到這件作業的結果**。"""
    if tool in _NEEDS_OFFICE and not _office_ok():
        pytest.skip("這台沒有 Office 引擎（OxOffice / LibreOffice），轉檔作業跑不起來")
    send, http, errs = tab.send, live["http"], tab.errs
    t0 = time.time()
    jid, want_url, checks, extra, dl = CASES[tool](http)
    t_job = time.time() - t0

    # ---- 作業帶著「開啟」要去的網址 ----
    job = http.get(f"/api/jobs/{jid}").json()
    assert job["meta"].get("view_url") == want_url, (
        f"meta.view_url 不對：{job['meta'].get('view_url')!r}，預期 {want_url!r}")

    # ---- 「我的作業」清單要給「開啟」（資料庫那份寫入會晚一步，等一下）----
    row: dict = {}
    end = time.time() + 30
    while time.time() < end:
        rows = http.get("/api/jobs").json().get("jobs") or []
        row = next((r for r in rows if r.get("id") == jid), {})
        if row.get("view_url") == want_url and row.get("view_ok"):
            break
        time.sleep(0.5)
    assert row.get("view_url") == want_url and row.get("view_ok"), (
        f"「我的作業」清單沒有給「開啟」：{ {k: row.get(k) for k in ('status', 'view_url', 'view_ok')} }")

    # ---- 真的在「我的作業」按「開啟」 ----
    t1 = time.time()
    send("Page.navigate", {"url": f"http://127.0.0.1:{live['port']}/my-jobs"})
    find = ("(function(){const a=[...document.querySelectorAll('.mj-actions a')]"
            f".find(x => x.getAttribute('href') === {json.dumps(want_url)});"
            "return a ? a.textContent.trim() : null;})()")
    assert _wait(send, find, 30), "「我的作業」頁面上找不到這一列的「開啟」"
    assert "開啟" in _eval(send, find)
    _eval(send, "(function(){[...document.querySelectorAll('.mj-actions a')]"
                f".find(x => x.getAttribute('href') === {json.dumps(want_url)}).click(); return 1;}})()")
    assert _wait(send, f"location.pathname + location.search === {json.dumps(want_url)}", 30), \
        f"按了「開啟」沒有到 {want_url}（現在在 {_eval(send, 'location.href')}）"
    # 導覽到的頁面本身要是正常的頁（不是 404 / 錯誤頁）
    r = http.get(want_url)
    assert r.status_code == 200, (
        f"「開啟」指到的頁面回 HTTP {r.status_code}：{r.text[:200]}")

    # ---- 結果要真的畫在畫面上 ----
    if not _wait(send, _all_visible_with(checks), PAGE_SECS):
        pytest.fail(f"{tool}：按「開啟」回來，畫面上沒有看到結果\n  " + _why_not(send, checks)
                    + (f"\n  JS 例外：{errs}" if errs else ""))
    for cond in extra:
        ok = _wait(send, cond, 30)
        assert ok, f"{tool}：條件沒成立：{cond}\n  " + _why_not(send, checks)
    t_page = time.time() - t1

    # ---- 下載鈕也要看得到、而且按下去拿得到檔案 ----
    # 結果畫出來了卻找不到下載鈕，使用者還是得回「我的作業」去下載 ——
    # 「開啟」回來的那一頁要能把事情做完。
    if dl:
        seen = _wait(send, _all_visible_with([(dl, [])]), 15)
        assert seen, f"{tool}：結果畫出來了，但下載鈕看不到\n  " + _why_not(send, [(dl, [])])
        href = _eval(send, f"document.querySelector({json.dumps(dl)}).getAttribute('href')")
        assert href, f"{tool}：下載鈕沒有連結"
        r = http.get(href)
        assert r.status_code == 200 and len(r.content) > 100, (
            f"{tool}：下載鈕的連結 {href} 回 HTTP {r.status_code}（{len(r.content)} bytes）")
    assert not errs, f"{tool}：頁面丟了未處理的 JS 例外：{errs}"
    TIMINGS[tool] = {"作業": round(t_job, 1), "開啟到畫出結果": round(t_page, 1)}


#: 反向檢查要看的頁面：(頁面路徑, 結果區選擇器)
_RESULT_AREAS = [
    ("/tools/pdf-compress/", "#resultPanel"),
    ("/tools/pdf-bookmark/", "#bmResult"),
    ("/tools/pdf-page-size/", "#psResult"),
    ("/tools/pdf-seam-stamp/", "#smResult"),
    ("/tools/pdf-to-slides/", "#resultPanel"),
    ("/tools/markdown-to-doc/", "#md2ResultPanel"),
    ("/tools/pdf-ocr/", "#resultBox .po-result"),
    ("/tools/pdf-to-office/", "#resultPanel"),
]


@pytest.mark.parametrize("bad", ["../x", "0" * 32, "%2F%2Fevil.example"],
                         ids=["dotdot", "unknown-id", "encoded-slashes"])
@pytest.mark.parametrize("path,area", _RESULT_AREAS, ids=[p for p, _ in _RESULT_AREAS])
def test_a_garbage_job_param_does_not_break_the_page(live, tab, path, area, bad):
    """網址是使用者改得到的：亂寫的 `?job=` 不可以讓頁面丟例外，結果區要維持看不見。"""
    send, errs = tab.send, tab.errs
    q = f"?job={bad}" + (f"&uid={bad}" if "markdown" in path else "")
    send("Page.navigate", {"url": f"http://127.0.0.1:{live['port']}{path}{q}"})
    assert _wait(send, "document.readyState === 'complete'", 30), "頁面沒載完"
    # 給頁面的接回程式時間跑完（輪詢一兩輪、讀不到作業的那條路）
    time.sleep(3)
    assert _eval(send, "!!document.querySelector('main, .panel')"), "頁面沒有正常畫出來"
    assert _visible_text(send, area) is None, f"{path}{q}：結果區不該出現"
    assert not errs, f"{path}{q}：頁面丟了未處理的 JS 例外：{errs}"


def test_pdf_fill_api_job_offers_no_open_link(live):
    """表單自動填寫的 `/submit`（只給 API）：填寫歷史頁已停用，「開啟」會是死連結 —— 不可以給（只有下載）。"""
    http = live["http"]
    jid = _ok(http.post("/tools/pdf-fill/submit", data={"font_id": "auto", "company_id": ""},
                        files={"file": ("vendor-form-e2e.pdf", _pdf(1), "application/pdf")}), "submit")["job_id"]
    _wait_job(http, jid)
    assert not http.get(f"/api/jobs/{jid}").json()["meta"].get("view_url")

