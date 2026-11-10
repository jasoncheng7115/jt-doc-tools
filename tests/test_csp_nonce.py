"""CSP nonce 靜態回歸測試（Phase 1：script-src 移除 'unsafe-inline'）。

確保：
  ① 每個可執行的 inline <script>（無 src）都帶 nonce — 否則 strict CSP 會擋掉。
  ② <script nonce="{{ ... }}"> 不可被包在 {% raw %} 內 — 否則 Jinja 不渲染 nonce，
     輸出字面 {{ }}（無效 nonce）→ CSP 擋掉整段（v1.12.28 踩過 markdown-to-doc
     / pdf-to-markdown / admin_ocr_langs）。
  ③ 模板無 inline 事件處理器（onclick= 等，strict CSP 不涵蓋，須 addEventListener）。
"""
import re
from pathlib import Path

import pytest

_TPL_DIRS = ["app/web/templates", "app/admin/templates"]
_ROOT = Path(__file__).resolve().parent.parent
_TPL_DIRS += [str(p.relative_to(_ROOT)) for p in (_ROOT / "app/tools").glob("*/templates")]

_HTML = [p for d in _TPL_DIRS for p in (_ROOT / d).rglob("*.html")]

#: 不是由本站當網頁送出的樣板（下載下來在 file:// 打開，沒有 CSP）—— 不可以帶 nonce：
#: 每次請求的 nonce 不同，會讓同一版本的檔案位元組每次都不一樣，公開的 SHA-256 就沒辦法核對。
_NOT_A_SERVED_PAGE = {
    "paper_backup_offline.html": "加密紙本備份的離線還原包：以附件下載、雙擊打開",
}

_SCRIPT_OPEN = re.compile(r"<script\b([^>]*)>")
_STYLE_OPEN = re.compile(r"<style\b([^>]*)>")
# 獨立的 inline style="..." 屬性（前面不是 - 或字母 → 不含 data-*-style;
# style= 緊接引號無空格 → 排除 JS 變數 `let style = '...'`）
# 等號兩邊可能有空白、值也可能沒有引號（HTML 都允許）—— 第一版要求
# `style=` 緊接引號，`style = "…"` 與 `style=red` 都漏掉。
#
# **但一定要限定在標籤內比對**，否則 `let style = 'rectangle'` 這種 JS 變數
# 指派會被誤報（實測 pdf_stamp.html 命中 3 次）。誤報一多這份檢查就會被
# 當成雜訊忽略，那比漏掉更糟。
_INLINE_STYLE_ATTR = re.compile(
    r'<[a-zA-Z][^>]*?(?<![-\w])style\s*=\s*["\'a-zA-Z]', re.S)
_RAW_BLOCK = re.compile(r"\{%-?\s*raw\s*-?%\}(.*?)\{%-?\s*endraw\s*-?%\}", re.S)
# **不要維護事件名白名單**。第一版只列 15 個名字，於是 `onmousedown`
# `ondblclick` `ontoggle` `oncontextmenu` `onwheel` `onkeypress`
# `onpointerdown` 全部放行 —— 而瀏覽器照擋。症狀是「按鈕點了沒反應、
# 沒有任何錯誤訊息」，而測試是綠的（v1.14.31 對抗式驗證用 8 個常見事件
# 名去餵，全部漏掉）。改成「on + 字母」的通式，只在 HTML 標籤內比對。
_INLINE_HANDLER = re.compile(r"<[a-zA-Z][^>]*?\son[a-z]+\s*=", re.I | re.S)


@pytest.mark.parametrize("path", _HTML, ids=lambda p: str(p.relative_to(_ROOT)))
def test_inline_scripts_have_nonce(path):
    """可執行 inline <script>（無 src、非 JSON 資料）必須帶 nonce。"""
    if path.name in _NOT_A_SERVED_PAGE:
        return
    s = path.read_text(encoding="utf-8")
    for m in _SCRIPT_OPEN.finditer(s):
        attrs = m.group(1)
        if "src=" in attrs:
            continue
        if 'type="application/json"' in attrs or "type='application/json'" in attrs:
            continue
        assert "nonce=" in attrs, (
            f"{path.name}: inline <script{attrs}> 缺 nonce → strict CSP 會擋")


@pytest.mark.parametrize("path", _HTML, ids=lambda p: str(p.relative_to(_ROOT)))
def test_no_nonce_script_inside_raw(path):
    """<script nonce=...> 不可在 {% raw %} 內（Jinja 不渲染 → 無效 nonce）。"""
    s = path.read_text(encoding="utf-8")
    for raw in _RAW_BLOCK.finditer(s):
        assert "<script nonce" not in raw.group(1), (
            f"{path.name}: <script nonce> 被包在 {{% raw %}} 內 → nonce 不會渲染")


@pytest.mark.parametrize("path", _HTML, ids=lambda p: str(p.relative_to(_ROOT)))
def test_style_blocks_have_nonce(path):
    """每個 <style> 區塊必須帶 nonce（strict CSP style-src 'nonce-…'）。"""
    if path.name in _NOT_A_SERVED_PAGE:
        return
    s = path.read_text(encoding="utf-8")
    for m in _STYLE_OPEN.finditer(s):
        assert "nonce=" in m.group(1), (
            f"{path.name}: <style{m.group(1)}> 缺 nonce → strict CSP 會擋")


@pytest.mark.parametrize("path", _HTML, ids=lambda p: str(p.relative_to(_ROOT)))
def test_no_inline_style_attr(path):
    """模板不可有 inline style="..." 屬性（strict CSP style-src 不涵蓋；
    動態樣式改 data-style + CSSOM，靜態改 data-s + 產生的 CSS）。"""
    s = path.read_text(encoding="utf-8")
    hits = _INLINE_STYLE_ATTR.findall(s)
    assert not hits, (
        f"{path.name}: 殘留 {len(hits)} 個 inline style 屬性 → 改 data-s / data-style")


@pytest.mark.parametrize("path", _HTML, ids=lambda p: str(p.relative_to(_ROOT)))
def test_no_nonce_style_inside_raw(path):
    """<style nonce=...> 不可在 {% raw %} 內（同 script，nonce 不會渲染）。"""
    s = path.read_text(encoding="utf-8")
    for raw in _RAW_BLOCK.finditer(s):
        assert "<style nonce" not in raw.group(1), (
            f"{path.name}: <style nonce> 在 {{% raw %}} 內 → nonce 不渲染")


@pytest.mark.parametrize("path", _HTML, ids=lambda p: str(p.relative_to(_ROOT)))
def test_no_inline_event_handlers(path):
    """模板不可有 inline 事件處理器（strict CSP 不涵蓋，須改 addEventListener）。"""
    s = path.read_text(encoding="utf-8")
    # 排除 .onclick= 之類的 JS 屬性指派（不是 HTML 屬性）
    hits = [m.group(0) for m in _INLINE_HANDLER.finditer(s)
            if not s[max(0, m.start() - 1)] in (".", "_")]
    assert not hits, f"{path.name}: 殘留 inline 事件處理器 {hits[:3]} → 改 addEventListener"


# ---------------------------------------------------------------------------
# static/js/*.js 也要掃 —— v1.14.31 對抗式驗證抓到的缺口
# ---------------------------------------------------------------------------

_JS = sorted((_ROOT / "static" / "js").glob("*.js"))


def _strip_js_comments(src: str) -> str:
    """去掉 JS 註解。

    **不可以用天真的 `//` 比對** —— 那會把 `'http://x'` 裡的 `//` 當成註解，
    把後面整行吃掉。這裡逐字掃，認得字串、樣板字串與正規表示式常值。
    """
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            q, j = c, i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == q:
                    break
                j += 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


@pytest.mark.parametrize("path", _JS, ids=lambda p: p.name)
def test_js_does_not_build_inline_style_or_handlers(path):
    """`static/js/*.js` 不可以用字串拼出 `style="…"` 或 `on*="…"`。

    ## 由來

    這份檢查原本只掃模板。同一段程式碼寫在模板裡會被當場擋下、寫在 `.js` 裡
    就一路綠燈 —— 而裡面**已經有一個真的會被瀏覽器擋掉的寫法**：
    `stamp_date_overlay.js` 用 `innerHTML` 拼了
    `<img style="width:100%; height:100%; pointer-events:none;">`。

    實測（本站 CSP vs 無 CSP 對照組）：CSP 下 `el.style.length === 0`、
    computed 寬度回到自然尺寸，console 留下
    「Applying inline style violates … style-src」。那一處剛好被 CSS 檔裡的
    `.dpe-asset img` 規則補回來所以畫面沒壞，但下一個人這樣寫就不一定了。
    """
    src = _strip_js_comments(path.read_text(encoding="utf-8"))
    hits = [m.group(0)[:60] for m in _INLINE_STYLE_ATTR.finditer(src)]
    hits += [m.group(0)[:60] for m in _INLINE_HANDLER.finditer(src)]
    assert not hits, (
        f"{path.name}: 用字串拼出了 inline style / 事件處理器（CSP 會擋掉）：\n"
        + "\n".join(hits) + "\n改用 DOM API：`el.style.xxx =` / `addEventListener`")


@pytest.mark.parametrize("path", _JS, ids=lambda p: p.name)
def test_js_does_not_set_style_or_handler_via_setattribute(path):
    """`setAttribute('style'|'on*', …)` 一樣會被 CSP 擋。"""
    src = _strip_js_comments(path.read_text(encoding="utf-8"))
    bad = re.findall(r"setAttribute\(\s*[\"'](style|on[a-z]+)[\"']", src, re.I)
    assert not bad, f"{path.name}: setAttribute({bad!r}) 會被 CSP 擋掉"
