#!/usr/bin/env python3
"""替介紹站的**各語言版**抓一組該語言介面的截圖。

各語言版頁面原本引用的是中文介面的截圖 —— 讀者看到的介面跟他實際會看到的
不一樣，而這正是「有沒有真的支援那個語言」最直接的證據。

抓的是一個 auth-off 的拋棄式實例（跟 `scripts/page_screenshots.py` 同一套做法），
把 `jtdt_locale` cookie 設成該語言再截。**視窗高度固定**，不抓整頁 —— 介紹站的
截圖是要放在卡片裡的示意圖，整頁長圖縮下去什麼都看不清楚。

用法：
    # 先起實例：JTDT_DATA_DIR=$(mktemp -d) uvicorn app.main:app --port 8799
    python tools/capture_locale_screenshots.py --locale en --base http://127.0.0.1:8799
    python tools/capture_locale_screenshots.py --locale ja --base http://127.0.0.1:8799
輸出：`github/docs/screenshots/<語言>/<名字>.png`

**語言清單一律從 `app/core/ui_locale.SUPPORTED` 取**，不要在這裡再寫一份 ——
加第四個語言時不必回頭改這支工具（本專案「同一份清單寫兩個地方一定會漂」）。
"""
from __future__ import annotations

import argparse
import asyncio
import re
import base64
import json
import subprocess
from pathlib import Path

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
from tools.repo_paths import public_root as _public_root

REPO = Path(__file__).resolve().parent.parent
_sys.path.insert(0, str(REPO))
from tools.browser_probe import profile_arg as _profile_arg  # noqa: E402


def _out_dir(locale: str) -> Path:
    """中文是原文 —— 它的截圖放在 `screenshots/`，沒有語言那一層。"""
    base = _public_root(REPO) / "docs" / "screenshots"
    return base if locale == "zh-Hant" else base / locale

#: 檔名 → 頁面路徑。名字**跟中文版那組對齊**，各語言版只要改資料夾就好。
SHOTS: dict[str, str] = {
    "index": "/",
    "fill": "/tools/pdf-fill/",
    "stamp": "/tools/pdf-stamp/",
    "watermark": "/tools/pdf-watermark/",
    "pdf-editor": "/tools/pdf-editor/",
    "pdf-ocr": "/tools/pdf-ocr/",
    "pdf-to-image": "/tools/pdf-to-image/",
    "pdf-to-office": "/tools/pdf-to-office/",
    "einvoice-scan": "/tools/einvoice-scan/",
    "translate-doc": "/tools/translate-doc/",
    "meeting-summary": "/tools/meeting-summary/",
    "official-doc": "/tools/official-doc/",
    "mfp-fetch": "/tools/mfp-fetch/",
    "business-card": "/tools/business-card-reader/",
    "deident-1": "/tools/doc-deident/",
    "deident-2": "/tools/text-deident/",
    "fonts": "/admin/fonts",
    "premissions": "/admin/permissions",
    "users-multi-realm": "/admin/users",
}
WIDTH, HEIGHT = 1400, 1020

#: 每一頁要怎麼弄成「真的在用」的樣子。空的上傳頁當產品截圖沒有意義
#: （2026-09-05 使用者回報），**而且送出失敗的錯誤對話框更糟**
#: （2026-09-14 使用者截圖：日文版一堆頁面停在「先に PDF をアップロード
#: してください」）。
#:
#: 欄位：
#:   `file`   要上傳哪一份範例檔（`SAMPLES` 的鍵）
#:   `before` 上傳前先做的事（JS，回傳值不看）
#:   `submit` 上傳後要不要按主要按鈕。`True` ＝按「第一顆看得到的主要按鈕」；
#:            也可以給一串選擇器，照順序按下去（**有些頁面的第一顆主要按鈕
#:            不是送出** —— 會議摘要要先按「使用貼上的內容」才輪到「開始分析」）
#:   `ready`  等到這段 JS 回 true 才算畫好。預設是「畫面上有沒有真的載進來的
#:            圖」，但**不是每一支工具的結果都是圖**（會議摘要的圖是前端畫的
#:            `<svg>`）—— 沒有這個欄位的話那種頁面一定等滿逾時
#:   `wait`   最多等幾秒（轉檔類要久一點）
#:
#: **檔案一律塞 `.file-upload input[type=file]`** —— 不可以用
#: `input[type=file]`：印章 / 浮水印頁**第一個** file input 是隱藏的
#: 資產上傳框，PDF 塞進去永遠不會被當成文件，然後按下去就是一個錯誤對話框。
#: 範例檔一律自己合成（`tools/seed_demo_data.py`）—— `temp_pdfs/` 裡是客戶
#: 資料，而截圖是要公開的。
RECIPES: dict[str, dict] = {
    # 表單自動填寫：**要看到真的填好的那張表**，不是只看到「已填入 N 個」。
    # 結果區的上半是統計，填好的表在更下面 —— 所以指定要捲到哪裡。
    "fill": {"file": "form", "submit": True, "wait": 20,
             "focus": "#previewStack"},
    # 印章要**按下去**才會進到拖曳定位的畫面 —— 只選好檔案的話畫面停在
    # 上傳區，跟空的頁面沒兩樣。
    "stamp": {"file": "doc", "submit": True, "wait": 25},
    "watermark": {
        # 浮水印要先有「來源」才按得下去。用**文字**模式最單純，
        # 不必另外準備一張圖，而且畫面上看得到設定。
        "before": """(() => {
            const t = [...document.querySelectorAll('.opt-label, label, button')]
              .find(e => /文字|Text|テキスト/.test((e.textContent||'').trim())
                         && e.closest('.wm-source, .opt, label'));
            if (t) (t.closest('label') || t).click();
            const box = document.querySelector('#wmText, textarea, input[type=text]');
            if (box) { box.value = 'CONFIDENTIAL'; box.dispatchEvent(new Event('input', {bubbles:true})); }
          })()""",
        "file": "doc", "submit": True, "wait": 12},
    "pdf-editor": {"file": "doc", "submit": False, "wait": 6},
    "pdf-ocr": {"file": "scan", "submit": False, "wait": 6},
    "pdf-to-image": {"file": "doc", "submit": True, "wait": 14},
    "pdf-to-office": {"file": "doc", "submit": True, "wait": 40},
    # 逐句翻譯沒有用共用的上傳元件（它自己一個 `#fileInput`）。
    "translate-doc": {"file": "doc", "input": "#fileInput",
                      "submit": False, "wait": 8},
    # 權限矩陣右邊要先選一位 subject，不然只有一句「從左側選一位…」。
    "premissions": {"before": """(() => {
        const it = document.querySelector('#permList .perm-row');
        if (it) { it.click(); return true; }
        return false;
      })()""", "submit": False, "wait": 5, "scroll": False},
    # 會議摘要：**要看到分析出來的東西**，不是只看到「解析好了，共 N 段」。
    #
    # **用貼上那條路不用上傳** —— 上傳是非同步的，按下去的那一刻
    # `upload_id` 還沒回來，於是跳出「請先貼上逐字稿」的對話框
    # （2026-09-23 第一次抓就是這樣，而**蓋著對話框的截圖就是拍壞的**）。
    # 貼上是同步的，畫面狀態確定。
    #
    # 行首的 `[mm:ss]` 讀得到 —— **有時間才畫得出發言佔比與章節時間軸**，
    # 而那兩張圖正是這支工具最值得看的地方。
    # 內容全部虛構（跟 `seed_demo_data.py` 那份同一批假資料）。
    # 一場短會議跑完大約 40~80 秒（幾十次模型請求），所以 `wait` 要夠久。
    "meeting-summary": {"before": """(() => {
        const box = document.getElementById('msPasteBox');
        if (!box) return false;
        box.value = [
          '[00:01] 王小明：那我們開始。今天三件事：測試機的網路、防火牆告警、還有儲存的方案。',
          '[00:10] 李美華：第一件我先講。測試機的 NAT 還沒開通，所以 7993 跟 7143 這兩個埠測不到。',
          '[00:21] 王小明：那就先開通。網管那邊我來發單，這禮拜五以前會好。',
          '[00:28] 陳大維：防火牆那邊比較麻煩。上禮拜維護的時候關了十二個小時，後來沒有再打開。',
          '[00:40] 陳大維：告警系統一直顯示 inactive，我以為是誤報，查了才發現是真的沒開。',
          '[00:52] 李美華：那我們要不要把它跟通知系統解開？每次維護都要記得手動開，遲早再出一次。',
          '[01:02] 王小明：解開。之後防火牆的開關改成手動，維護完由值班的人確認一次。',
          '[01:12] 陳大維：第三件是儲存。現在的環境還撐得住，明年如果效能不夠再提採購。',
          '[01:24] 李美華：那 RAID 卡的部分呢？上次說要換的那張，現在還是用軟體的。',
          '[01:33] 王小明：先用現有的環境測，不行再說。備份有做好，硬碟壞了立刻換就行。',
          '[01:44] 陳大維：了解。那我把測試結果整理一份，下禮拜會議前寄給大家。',
        ].join('\\n');
        box.dispatchEvent(new Event('input', {bubbles: true}));
        return true;
      })()""",
      # **第一顆主要按鈕不是送出**：要先按「使用貼上的內容」把文字收下來，
      # 才輪到「開始分析」。
      "submit": ["#msPasteGo", "#msStart"],
      # 結果的圖是**前端畫的 `<svg>`**，不是 `<img>` 也不是 `<canvas>` ——
      # 用預設判準會等滿逾時然後拍到還沒畫完的畫面。
      "ready": "!!document.querySelector('#msCards .ms-card')",
      "wait": 240, "focus": "#msCards"},
    # 公文撰擬：**要看到真的寫出來的草稿與右邊的預覽圖**。載入產品內建的範例
    # （`examples.py` 的 SIGN-01，內容虛構）再按「產生草稿」；預覽圖走 Office 引擎轉，
    # 轉圈收掉、圖真的載進來才算畫好。需要接得上的 LLM（實例的 LLM 設定要先填）。
    # 示範事務機（`seed_demo_data.seed_mfp_devices`）：清單有共用與自己新增、相容性、掃描 / 取件，下面接最近的掃描與取件
    "mfp-fetch": {"ready": """(() => !!document.querySelector('#mfListWrap table')
        && document.querySelectorAll('#mfScansWrap tbody tr').length >= 3)()""",
      "wait": 30, "scroll": False},
    # 名片辨識：一張照片拍 6 張名片（`seed_demo_data.make_card_photo`，內容全部杜撰）→ 選「一張圖片多張名片」
    # 上傳 → 停在確認範圍，每一張都框好並編號。上傳框是頁面自己的 `#bcFiles`。
    # `DOM.setFileInputFiles` 本身就會觸發 change，再手動發一次會上傳兩次（多一個批次）。
    "business-card": {"before": """(() => {
        const r = document.querySelector('input[name=bcMode][value=multi]');
        if (!r) return false;
        r.click();
        return true;
      })()""",
      "file": "cards", "input": "#bcFiles", "fires_change": True, "submit": False,
      "ready": """(() => !document.getElementById('bcFrames').hidden
        && document.querySelectorAll('#bcSvg polygon').length >= 6
        && document.getElementById('bcPageImg').complete
        && document.getElementById('bcPageImg').naturalWidth > 0)()""",
      "wait": 60, "focus": "#bcFrames", "focus_block": "start"},
    "official-doc": {"before": """(() => {
        const s = document.getElementById('odExample');
        if (!s) return false;
        s.value = 'SIGN-01';
        s.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
      })()""",
      "submit": ["#odGo"],
      "ready": """(() => { const i = document.querySelector('#odPreview img');
        return !!i && i.naturalWidth > 0 && document.getElementById('odPvSpin').hidden; })()""",
      # 草稿與預覽圖要並排（內容區至少 1100px 才排兩欄）：用寬一點的視窗拍，
      # 再縮成跟其他截圖一樣的大小；捲到「草稿」那一行標題（上緣對齊）。
      "wait": 240, "focus": "#odResult .od-draft-grid", "focus_block": "start",
      "viewport": (1600, 1166)},
    "deident-1": {"file": "deident", "submit": True, "wait": 14},
    # 文字去識別化沒有檔案可放 —— 直接把範例文字貼進去（**內容全部虛構**，
    # 跟 `seed_demo_data.py` 那份是同一批假資料）。空白的輸入框當產品截圖
    # 沒有意義（2026-09-14 使用者回報）。
    "deident-2": {"before": """(() => {
        const box = document.getElementById('srcText');
        if (!box) return false;
        box.value = [
          'Employee record (sample \u2014 every value here is made up)', '',
          'Name: John Doe',
          'SSN: 900-12-3456',
          'Date of birth: January 5, 1985',
          'Phone: +1 (555) 010-4477',
          'Email: john.doe@example.com',
          'Address: 1842 Maple Street, Springfield, IL 62704',
          'Card: 4111 1111 1111 1111',
        ].join('\\n');
        box.dispatchEvent(new Event('input', {bubbles: true}));
        return true;
      })()""", "submit": True, "wait": 12},
}

#: 範例檔的檔名前綴，全部由 `tools/seed_demo_data.py` 合成。
SAMPLES = ("doc", "form", "scan", "deident", "meeting", "cards")
_SAMPLE_FILE = {"doc": "quotation", "form": "vendor-form",
                "scan": "scan", "deident": "deident",
                "meeting": "meeting", "cards": "cards"}


#: 瀏覽器是 snap 版時，**它讀不到 `/opt`，而且 `/tmp` 是它自己的那一個**
#: （CLAUDE.md 慣例第⑲條）。`DOM.setFileInputFiles` 照樣「成功」、檔名也
#: 顯示得出來 —— 只有真的要送的時候 XHR 才會炸 `network error`，
#: **而伺服器端一筆請求都沒有**。這一整批截圖的上傳從來沒有成功過
#: （英文那組停在「Please upload a PDF first」就是這個原因）。
_SNAP_STAGE = Path.home() / "snap" / "chromium" / "common" / "jtdt-shots"


def _browser_is_snap(exe: str) -> bool:
    if "/snap/" in str(Path(exe).resolve()):
        return True
    try:                      # `/usr/bin/chromium-browser` 是一支 shell 包裝
        return "snap" in Path(exe).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def stage_for_browser(f: Path, snap: bool) -> Path:
    """把範例檔放到瀏覽器讀得到的地方。不是 snap 就原樣回。"""
    if not snap:
        return f
    _SNAP_STAGE.mkdir(parents=True, exist_ok=True)
    dst = _SNAP_STAGE / f.name
    dst.write_bytes(f.read_bytes())
    return dst


def sample_for(kind: str, locale: str) -> "Path | None":
    """該語言要用哪一份範例檔。

    有 `<名字>.<語言>.pdf` 就用那一份，否則退回 `<名字>.pdf` ——
    **素材要跟著規則走**：中文介面放英文報價單只是怪，但去識別化在英文介面
    用的是英美那組式子，塞中文個資進去會**一筆都偵測不到**，截圖就變成
    「這個工具沒作用」。
    """
    base = _SAMPLE_FILE.get(kind)
    if not base:
        return None
    d = REPO / "temp" / "demo"
    # **副檔名不要寫死 `.pdf`** —— 會議摘要收的是逐字稿（`.vtt`），
    # 寫死的話它永遠找不到素材，而失敗的樣子是「截到一張空的上傳頁」。
    for ext in (".pdf", ".vtt", ".txt", ".docx", ".jpg"):
        for f in (d / f"{base}.{locale}{ext}", d / f"{base}{ext}"):
            if f.is_file():
                return f
    return None


def tool_id_for(name: str) -> "str | None":
    """截圖名字 → 工具 id（`/tools/<id>/` 這種路徑才有）。"""
    m = re.match(r"^/tools/([^/]+)/$", SHOTS.get(name, ""))
    return m.group(1) if m else None


def hidden_shots(locale: str) -> set:
    """在這個語言下**反灰**的工具 —— 那幾張截圖不要出現在該語言的介紹站。

    台灣專屬的工具（統編查詢 / 電子發票 / 表單自動填寫…）在英文 / 日文介面下
    本來就點不下去，介紹站還放它們的截圖只會讓人以為能用
    （使用者 2026-09-14：「台灣專用功能 不需在 英文 日文 pages 截圖」）。

    判準走註冊表的 `ToolMetadata.locales`，**不要自己維護一份名單** ——
    那種名單在這個專案漂掉過很多次。
    """
    from app.core.ui_locale import tool_visible
    from app.tool_registry import discover_tools

    locales = {t.metadata.id: t.metadata.locales for t in discover_tools()}
    out = set()
    for name in SHOTS:
        tid = tool_id_for(name)
        if tid and not tool_visible(locales.get(tid), locale):
            out.add(name)
    return out



#: 主要文件的上傳框。**共用元件 `components/file_upload.html` 的那一個** ——
#: 頁面上還有別的 file input（印章 / 浮水印的資產上傳是隱藏的），
#: 用 `input[type=file]` 會挑到錯的那個。
_MAIN_FILE_INPUT = ".file-upload input[type=file]"

#: 按下主要按鈕（看得到、沒有 disabled 的第一顆）。
_CLICK_PRIMARY = """
  (() => {
    const vis = (e) => e && e.offsetParent !== null && !e.disabled;
    const b = [...document.querySelectorAll('.btn-primary, button[type=submit]')]
      .find(vis);
    if (b) { b.click(); return true; }
    return false;
  })()"""

#: 結果區的圖片載好了沒。**固定 sleep 幾秒是猜的** —— 猜少了就拍到一塊
#: 空白的灰底（實測印章的編輯器 12 秒還沒畫出來），猜多了每一張都在空等。
#: 判準：畫面上有沒有一張**真的載進來**的圖（`naturalWidth > 0`），
#: 或是有內容的 canvas。
_RESULT_READY = """
  (() => {
    const vis = (e) => e && e.offsetParent !== null;
    const imgs = [...document.querySelectorAll('img')].filter(vis);
    if (imgs.some((i) => i.naturalWidth > 40 && i.clientWidth > 40)) return true;
    const cv = [...document.querySelectorAll('canvas')].filter(vis);
    return cv.some((c) => c.width > 40 && c.height > 40);
  })()"""

#: 按完之後**要捲到結果那一塊**。上傳區在頁面上半部，結果 / 編輯器在下面 ——
#: 不捲的話截到的還是那個上傳框，看起來就像「檔案放進去了但什麼都沒發生」
#: （2026-09-14 使用者回報「截圖要放文件進去啊」）。
#: 判準是「**最後一個看得到的 `.panel`**」，不是捲到底（頁尾沒有內容）。
_SCROLL_TO_RESULT = """
  (() => {
    const vis = (e) => e && e.offsetParent !== null
      && e.getBoundingClientRect().height > 80;
    const panels = [...document.querySelectorAll('.panel, .result, .preview')]
      .filter(vis);
    const el = panels[panels.length - 1];
    if (!el) return false;
    el.scrollIntoView({block: 'start'});
    window.scrollBy(0, -70);          // 別讓標題被上面的列蓋住
    return true;
  })()"""

#: 畫面上有沒有蓋著對話框（`showConfirm` / `showModal` / 原生 alert 用的殼）。
#: 有的話這一張就是拍壞的 —— 印出來讓人看得到，不要安靜地存下去。
_DIALOG_TEXT = """
  (() => {
    const d = [...document.querySelectorAll(
      '.jt-modal, .modal, [role=dialog], .jt-confirm')]
      .find(e => e.offsetParent !== null);
    return d ? (d.textContent || '').trim().replace(/\\s+/g, ' ') : '';
  })()"""

CHROME = "/usr/bin/chromium-browser"



#: 這幾張畫面上的中文是**示範資料**不是介面文字 —— 翻掉才是錯的。
#:
#: `seed_demo_data.py` 建的示範帳號是中文姓名、示範印章叫「範例之印」，
#: 而個資限用章的用途範本是**會被印到章上的值**（那個下拉已經標
#: `data-i18n="skip"`，這裡列的是它在別處出現的情況）。
#:
#: **不排除的話這幾條會永遠掛在報告上**，而誤報一多這份檢查就會被當雜訊
#: 忽略（用詞檢查那次的教訓）。
_DATA_SHOTS: dict[str, tuple[str, ...]] = {
    "stamp": ("範例之印",),
    "premissions": ("張家瑜", "陳美華", "黃大生"),
    "users-multi-realm": ("張家瑜", "陳美華", "黃大生"),
}


#: 拍完之後順手掃一次「畫面上還有沒有中文」。
#:
#: TEST_PLAN §0.6 列的三格裡，**「送出後的結果區」一直沒有人掃** ——
#: 逐頁掃描器只看頁面剛載入的狀態，而這支工具**真的把檔案送出去、等結果
#: 出現**才拍照，所以它眼前那一幕正是缺的那一格。
#:
#: **重用逐頁掃描器的 JS 與判準**（`i18n_untranslated_scan`），不要另抄一份
#: —— 同一份判準寫兩個地方一定會漂（本專案第 N 次）。
async def _residual_cjk(cmd, locale: str) -> list[str]:
    from tools.i18n_untranslated_scan import JS as _SCAN_JS, _catalog_keys, _keep
    r = await cmd("Runtime.evaluate", {"expression": _SCAN_JS, "returnByValue": True})
    try:
        items = json.loads((r.get("result", {}) or {}).get("value") or "[]")
    except Exception:  # noqa: BLE001
        return []
    keys = _catalog_keys(locale)
    return [i["text"] for i in items if _keep(i["text"], locale, keys)]


def _not_demo_data(shot: str, texts: list[str]) -> list[str]:
    allow = _DATA_SHOTS.get(shot, ())
    return [t for t in texts if not any(a in t for a in allow)]


async def _capture(base: str, cdp_port: int, locale: str, only=None) -> list[str]:
    import httpx
    import websockets

    snap = _browser_is_snap(CHROME)
    if snap:
        print("  瀏覽器是 snap 版 —— 範例檔改放到它讀得到的地方")
    proc = subprocess.Popen(
        [CHROME, "--headless", "--no-sandbox", "--disable-gpu",
         _profile_arg(), f"--remote-debugging-port={cdp_port}", "--remote-allow-origins=*",
         "--hide-scrollbars", f"--window-size={WIDTH},{HEIGHT}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    done: list[str] = []
    try:
        ws_url = None
        for _ in range(40):
            await asyncio.sleep(0.5)
            try:
                tabs = [t for t in httpx.get(
                    f"http://127.0.0.1:{cdp_port}/json/list", timeout=2).json()
                    if t.get("type") == "page"]
                if tabs:
                    ws_url = tabs[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
        if not ws_url:
            raise SystemExit("連不上 CDP")
        async with websockets.connect(ws_url, max_size=80 * 1024 * 1024) as ws:
            mid = 0

            async def cmd(method, params=None):
                nonlocal mid
                mid += 1
                await ws.send(json.dumps({"id": mid, "method": method,
                                          "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == mid:
                        if "error" in msg:
                            raise RuntimeError(msg["error"])
                        return msg.get("result", {})

            await cmd("Page.enable")
            await cmd("Runtime.enable")
            await cmd("Network.enable")
            host = base.split("//", 1)[-1].split(":")[0].split("/")[0]
            await cmd("Network.setCookie", {"name": "jtdt_locale", "value": locale,
                                            "domain": host, "path": "/"})
            await cmd("Emulation.setDeviceMetricsOverride",
                      {"width": WIDTH, "height": HEIGHT,
                       "deviceScaleFactor": 1, "mobile": False})
            out = _out_dir(locale)
            out.mkdir(parents=True, exist_ok=True)
            await cmd("DOM.enable")
            skip = hidden_shots(locale)
            if skip:
                print(f"  {locale} 跳過（該語言下反灰的工具）：{sorted(skip)}")
            residual: dict[str, list[str]] = {}
            for name, path in SHOTS.items():
                if only and name not in only:
                    continue
                if name in skip:
                    continue
                r = RECIPES.get(name)
                vw, vh = (r or {}).get("viewport") or (WIDTH, HEIGHT)
                await cmd("Emulation.setDeviceMetricsOverride",
                          {"width": vw, "height": vh, "deviceScaleFactor": 1, "mobile": False})
                await cmd("Page.navigate", {"url": base + path})
                await asyncio.sleep(2.0)
                if r:
                    sample = sample_for(r.get("file", ""), locale)
                    if r.get("before"):
                        await cmd("Runtime.evaluate", {"expression": r["before"]})
                        await asyncio.sleep(1.0)
                    if sample and sample.is_file():
                        sample = stage_for_browser(sample, snap)
                        # 用 CDP 直接把檔案塞進 <input type=file> —— headless 沒有
                        # 檔案選擇器，只能這樣做。塞完要自己發 change 事件，
                        # 頁面才會開始處理（少了它畫面完全不動）。
                        doc = await cmd("DOM.getDocument", {"depth": -1})
                        sel = r.get("input") or _MAIN_FILE_INPUT
                        node = await cmd("DOM.querySelector", {
                            "nodeId": doc["root"]["nodeId"],
                            "selector": sel})
                        if not node.get("nodeId"):
                            print(f"  ! {name}: 找不到主要的上傳框")
                        else:
                            await cmd("DOM.setFileInputFiles", {
                                "files": [str(sample)], "nodeId": node["nodeId"]})
                            if not r.get("fires_change"):
                                await cmd("Runtime.evaluate", {"expression":
                                    f"document.querySelector({sel!r})"
                                    ".dispatchEvent(new Event('change',{bubbles:true}))"})
                            await asyncio.sleep(4.0)
                    submit = r.get("submit")
                    if submit is True:
                        await cmd("Runtime.evaluate", {"expression": _CLICK_PRIMARY})
                    elif submit:
                        # 逐顆按：第一顆多半只是「把輸入收下來」，送出在後面。
                        for sel in submit:
                            hit = await cmd("Runtime.evaluate", {
                                "returnByValue": True, "expression": f"""
                              (() => {{
                                const b = document.querySelector({sel!r});
                                if (!b || b.offsetParent === null || b.disabled)
                                  return false;
                                b.click(); return true;
                              }})()"""})
                            if not (hit.get("result", {}) or {}).get("value"):
                                print(f"  ! {name}: 按不到 {sel}")
                                break
                            await asyncio.sleep(1.5)
                    # 等到結果真的畫出來（最多 `wait` 秒），不是死等。
                    ready = r.get("ready") or _RESULT_READY
                    deadline = float(r.get("wait", 10))
                    waited = 0.0
                    while waited < deadline:
                        await asyncio.sleep(1.0)
                        waited += 1.0
                        ok = await cmd("Runtime.evaluate",
                                       {"expression": ready,
                                        "returnByValue": True})
                        if (ok.get("result", {}) or {}).get("value"):
                            await asyncio.sleep(1.5)   # 讓它畫完
                            break
                    # 等滿了也沒關係：**不是每一頁的結果都是圖**（去識別化是
                    # 一張清單、轉文書檔是一條下載連結）。這個輪詢只是「有圖就
                    # 早點收工」，不是判斷成功與否 —— 真正的判準是下面那條
                    # 「畫面上有沒有蓋著對話框」，以及人逐張看過。
                    if r.get("focus"):
                        # 指定捲到哪一塊 —— 「最後一個 panel」有時候只捲到
                        # 結果區的**標題**，真正要看的東西還在下面。
                        await cmd("Runtime.evaluate", {"expression": f"""
                          (() => {{
                            const el = document.querySelector({r['focus']!r});
                            if (!el) return false;
                            el.scrollIntoView({{block: {r.get('focus_block', 'center')!r}}});
                            return true;
                          }})()"""})
                        await asyncio.sleep(1.2)
                    elif r.get("scroll", True):
                        await cmd("Runtime.evaluate",
                                  {"expression": _SCROLL_TO_RESULT})
                        await asyncio.sleep(1.2)
                    # **對話框 = 這一張沒拍成功**：畫面上蓋著「請先上傳…」那種
                    # 提示時，截圖比空的上傳頁還糟（使用者 2026-09-14 回報）。
                    bad = await cmd("Runtime.evaluate", {
                        "expression": _DIALOG_TEXT, "returnByValue": True})
                    msg = (bad.get("result", {}) or {}).get("value") or ""
                    if msg:
                        print(f"  ! {name}: 畫面上有對話框 → {msg[:60]}")
                if locale != "zh-Hant":
                    left = _not_demo_data(name, await _residual_cjk(cmd, locale))
                    if left:
                        residual[name] = left
                shot = await cmd("Page.captureScreenshot", {})
                png = base64.b64decode(shot["data"])
                if (vw, vh) != (WIDTH, HEIGHT):
                    # 寬視窗拍的縮回統一大小（介紹站每張卡片一樣大）
                    import io
                    from PIL import Image
                    im = Image.open(io.BytesIO(png)).convert("RGB").resize((WIDTH, HEIGHT), Image.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, "PNG", optimize=True)
                    png = buf.getvalue()
                (out / f"{name}.png").write_bytes(png)
                done.append(name)
            if residual:
                print(f"  ! {locale} 這幾張的畫面上還有中文（送出後的結果區）：")
                # **不要截斷** —— 只印前幾條的話，後面的永遠沒有人看到，
                # 而「報告上只有 4 條」看起來就像「只漏了 4 條」
                #（2026-09-23 實際踩到：卡片標題那一整組被截掉了）。
                for k, v in residual.items():
                    print(f"      {k}（{len(v)} 條）：")
                    for one in v:
                        print(f"        - {one}")
        return done
    finally:
        # 等它真的結束：沒等的話瀏覽器在設定檔目錄被刪掉之後還會寫幾個檔，留下殘骸
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            proc.kill()
            proc.wait(timeout=15)


def main() -> int:
    from app.core import ui_locale

    langs = list(ui_locale.SUPPORTED)
    ap = argparse.ArgumentParser()
    ap.add_argument("--locale", default="en", choices=langs)
    ap.add_argument("--base", default="http://127.0.0.1:8799")
    ap.add_argument("--cdp-port", type=int, default=9421)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    done = asyncio.run(_capture(args.base, args.cdp_port, args.locale,
                                set(args.only) if args.only else None))
    print(f"抓了 {len(done)} 張 -> {_out_dir(args.locale)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
