#!/usr/bin/env python3
"""從中文版介紹站產出其他語言版（`index.html` → `index-en.html` / `index-ja.html`）。

**為什麼用生成而不是手工維護兩份**：同一份文件放兩個地方一定會漂 —— 這個專案
已經吃過好幾次虧（`github/TEST_PLAN.md` 停在 v1.8.55 少了 182 行、介紹站的工具
數字與卡片對不上）。中文版永遠是**唯一的來源**，英文版每次重新生成。

用法：
    python3 github/build-i18n-page.py --extract   # 抽出待翻字串到 i18n/index.<lang>.json
    python3 github/build-i18n-page.py             # 產生所有語言
    python3 github/build-i18n-page.py --lang ja   # 只做日文
    python3 github/build-i18n-page.py --check     # 只檢查有沒有漏翻（給自動檢查用）

**語言清單來自 `app.core.ui_locale.SUPPORTED`**，不在這裡另外寫一份 ——
同一份清單放兩個地方一定會漂（本專案最常復發的那一類）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent / "docs"
I18N = DOCS / "i18n"
#: 中文標點也要算 —— 只認漢字的話，`<b>A</b>、<b>B</b>` 中間那個頓號會留在
#: 英文頁上（實測就是這樣漏的）。
CJK = re.compile(r"[㐀-鿿、。：；！？（）「」《》…—]")
#: **純標點的片段不收**。中文標點也算「要翻」是為了 `<b>A</b>、<b>B</b>` 中間那個
#: 頓號，但整段只有標點時（表格裡孤零零一個 `—`、一個 `）`）那不是句子 ——
#: 收進去只會變成一個誰都對得上的鍵，然後把**別處的譯文**貼到那個標點的位置。
#: 2026-09-04 使用者從英文版介紹站截圖回報：表格欄位變成「; JSON:」、
#: 免責聲明每一條都以逗號開頭，根因就是這個。
_HAS_WORD = re.compile(r"[㐀-鿿A-Za-z0-9]")

#: 這些區段不翻：程式碼、樣式、註解、以及安裝指令那一類原樣照抄的東西。
_SKIP_BLOCK = re.compile(
    r"<(script|style|code|pre)\b.*?</\1>|<!--.*?-->"
    # 語言選單是**產生的**（見 `lang_group`）—— 抽出來翻只會多出
    # 「English」「日本語」這種每加一種語言就變動一次的假條目。
    r"|<select id=\"langSwitch\".*?</select>", re.S | re.I)
#: 會翻的屬性（使用者看得到的）。
_ATTRS = ("alt", "title", "placeholder", "aria-label", "content")


#: 整塊一起翻的區塊標籤。**句子被行內標籤切開時，逐段翻一定會壞** ——
#: 中文照原順序接起來剛好通順，英文語序不同，接出來就是
#: 「, including but not limited to…」這種以逗號開頭的碎片
#: （2026-09-04 使用者從英文版介紹站截圖回報）。
_BLOCK = re.compile(
    r"<(p|li|h1|h2|h3|h4|h5|h6|td|th|summary|figcaption|button|label|blockquote)"
    r"(\s[^>]*)?>(.*?)</\1>", re.S | re.I)
#: 區塊裡只有這些行內標籤時才整塊收；出現別的區塊標籤就退回逐段。
_INLINE_ONLY = re.compile(
    r"</?(b|strong|i|em|u|s|a|code|span|br|small|kbd|sup|sub|abbr|mark)\b[^>]*>",
    re.I)
_HAS_TAG = re.compile(r"<[^>]+>")


def _block_segments(html: str, inside, base: int = 0) -> list:
    """含行內標籤的整塊 —— 標籤留在字串裡，讓譯者自己擺到英文該在的位置。

    外層區塊裡還有區塊時（表格格子裡放 `<p>`），往裡面找：只看最外層的話，
    格子裡那一句會被 `<code>` 切成「資料目錄（」「），2 小時後」這種碎片逐段翻。"""
    out = []
    for m in _BLOCK.finditer(html):
        inner = m.group(3)
        a, b = base + m.start(3), base + m.end(3)
        if inside(a) or not CJK.search(inner) or not _HAS_WORD.search(inner):
            continue
        if not _HAS_TAG.search(inner):
            continue                      # 純文字的走原本的逐段路徑就好
        if _INLINE_ONLY.sub("", inner).find("<") >= 0:
            # 裡面還有別的區塊，整塊收會太大。**只有表格格子裡放 `<p>` 這一種**往內層找
            # （合規頁的寫法）；其他頁的巢狀清單照舊逐段，鍵不變、既有譯文才對得上。
            if m.group(1).lower() in ("td", "th") and re.search(r"<p[\s>]", inner, re.I):
                out += _block_segments(inner, inside, a)
            continue
        out.append((False, a, b, inner))
    return out


#: 中文不用空格，英文要。`…的<b>公司內部文件</b>，…` 翻成英文之後會變成
#: `is<b>downloaded from GitHub</b>, so` —— 字黏在一起（2026-09-05 使用者回報）。
#: 這是**語言層級**的規則，所以放在產生英文頁的最後一步，而不是逐條去改譯文。
_INLINE_TAGS = "b|strong|i|em|code|span|a|kbd|small|u|mark"


#: 日文字（假名 / 漢字 / 全形標點）。用來判斷「標籤兩邊是不是都是日文」。
_JA_CH = r"[\u3040-\u30ff\u3400-\u9fff\uff01-\uff60、。「」－]"
_ANY_TAG = r"</?(?:" + _INLINE_TAGS + r")\b[^>]*>"


def _tighten_cjk_spaces(html: str) -> str:
    """日文頁：行內標記**兩邊都是日文**時，把中間那個空白收掉。

    這是英文那條規則的鏡像。中文原文在 `<b>` 旁邊常常留一個空白
    （因為標籤裡面是拉丁字，例如 `<b>不需</b> Office 引擎`），
    翻成日文之後兩側都變成日文字，那個空白就變成
    「不要 です」這種怪東西。

    **判準是標籤兩側實際的字元，不是標籤本身** —— 「これらのツールは
    <b>Word …</b>」那個空白要留著（一邊是日文、一邊是拉丁字，
    日文排版本來就會空一格）。
    """
    html = re.sub(rf"({_JA_CH})((?:{_ANY_TAG})+)[ ](?={_JA_CH})", r"\1\2", html)
    html = re.sub(rf"({_JA_CH})[ ]((?:{_ANY_TAG})+)(?={_JA_CH})", r"\1\2", html)
    return html


def _space_around_inline_tags(html: str) -> str:
    """英文頁：行內標記與前後英數字之間補一個空白。"""
    # 「字母 + <b>」→ 「字母 + 空白 + <b>」
    html = re.sub(r"(?<=[A-Za-z0-9,.)])(<(?:" + _INLINE_TAGS + r")[ >])",
                  r" \1", html)
    # 「</b> + 字母」→ 「</b> + 空白 + 字母」
    html = re.sub(r"(</(?:" + _INLINE_TAGS + r")>)(?=[A-Za-z0-9(])",
                  r"\1 ", html)
    return html


#: 安裝指令是**程式碼**，抽字串那條路不會碰它 —— 但裡面的錯誤訊息是給人看的，
#: 英文版留著中文很怪（2026-09-05 使用者截圖回報）。這裡逐字換掉那幾句。
#:
#: **那幾句是內嵌在一行指令裡的**（PowerShell 的 `Write-Host "…"`），
#: 使用者複製貼上跑的就是這一份，所以換成該語言是對的、不會跟 `install.ps1`
#: 的行為對不上。
_CMD_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "[X] 下載安裝腳本失敗：": "[X] Could not download the install script: ",
        "請檢查網路（VPN？防火牆？DNS？）後重試。":
            "Check the network (VPN? firewall? DNS?) and try again.",
        "按 Enter 關閉": "Press Enter to close",
    },
    "ja": {
        "[X] 下載安裝腳本失敗：": "[X] インストールスクリプトをダウンロードできませんでした: ",
        "請檢查網路（VPN？防火牆？DNS？）後重試。":
            "ネットワーク（VPN / ファイアウォール / DNS）を確認してからやり直してください。",
        "按 Enter 關閉": "Enter キーで閉じる",
    },
}


def _translate_install_command(html: str, lang: str) -> str:
    """把安裝指令裡給人看的中文訊息換成該語言。

    沒有對照表的語言原樣保留（顯示中文比顯示一句沒人看得懂的機器翻譯好）。
    """
    for zh, txt in _CMD_TRANSLATIONS.get(lang, {}).items():
        html = html.replace(zh, txt)
    return html


def _drop_hidden_tool_shots(html: str, lang: str) -> str:
    """台灣專屬的工具在別的語言底下是反灰的 —— **介紹站也不要放它們的截圖**
    （使用者 2026-09-14：「台灣專用功能 不需在 英文 日文 pages 截圖」）。

    整個 `<figure class="ss-row">` 拿掉，然後把 `ss-num` 的編號重排 ——
    少了一張卻留著 01 / 03 / 04 的跳號比沒拿掉還難看。

    要拿掉哪幾張**由註冊表的 `ToolMetadata.locales` 決定**（共用擷取工具的
    `hidden_shots`），不要在這裡再維護一份名單。
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.capture_locale_screenshots import hidden_shots

    names = hidden_shots(lang)
    if not names:
        return html
    pat = re.compile(r"\s*<figure class=\"ss-row[^\"]*\">.*?</figure>", re.S)

    def keep(m):
        seg = m.group(0)
        return "" if any(f"screenshots/{n}.png" in seg for n in names) else seg

    html = pat.sub(keep, html)

    # 編號重排：`<div class="ss-num">01</div>`
    n = [0]

    def renum(m):
        n[0] += 1
        return f'<div class="ss-num">{n[0]:02d}</div>'

    return re.sub(r'<div class="ss-num">\d+</div>', renum, html)


def _localised_screenshots(html: str, docs_dir, lang: str) -> str:
    """某語言版指到該語言介面的截圖（`screenshots/<語言>/…`）。

    讀者看到的畫面要跟他實際會看到的一樣 —— 截圖正是「有沒有真的支援那個
    語言」最直接的證據。**只有在該語言那張真的存在時才換**，所以還沒補截圖的
    工具會繼續沿用中文版那張，不會變成破圖。
    """
    def rep(m):
        name = m.group(1)
        return (f"screenshots/{lang}/{name}"
                if (docs_dir / "screenshots" / lang / name).is_file()
                else m.group(0))
    return re.sub(r"screenshots/([\w.-]+\.png)", rep, html)


def _segments(html: str):
    """回傳 [(是不是屬性, 起, 迄, 原文)]，只收含中文的片段。

    先收「含行內標籤的整塊」，再收剩下的純文字節點與屬性 ——
    已經被整塊收走的範圍不再重複收。
    """
    holes = [(m.start(), m.end()) for m in _SKIP_BLOCK.finditer(html)]

    def inside(pos: int) -> bool:
        return any(a <= pos < b for a, b in holes)

    blocks = _block_segments(html, inside)
    taken = [(a, b) for _f, a, b, _t in blocks]

    def covered(pos: int) -> bool:
        return any(a <= pos < b for a, b in taken)

    out = list(blocks)
    for m in re.finditer(r">([^<>]+)<", html):
        # **判斷用文字本身的位置，不是 `>` 的位置** —— `</code>中文<` 的那個 `>`
        # 屬於 code 區塊，用它判斷會把後面那段正常的中文一起跳過（第一版就是
        # 這樣漏了 21 段，而且產出的英文頁裡看得到中文才發現）。
        if (inside(m.start(1)) or covered(m.start(1))
                or not CJK.search(m.group(1)) or not _HAS_WORD.search(m.group(1))):
            continue
        out.append((False, m.start(1), m.end(1), m.group(1)))
    for m in re.finditer(r'(%s)="([^"]*)"' % "|".join(_ATTRS), html):
        if (inside(m.start()) or not CJK.search(m.group(2))
                or not _HAS_WORD.search(m.group(2))):
            continue
        out.append((True, m.start(2), m.end(2), m.group(2)))
    return sorted(out, key=lambda x: x[1])


def extract(src: Path, cat_path: Path) -> int:
    html = src.read_text(encoding="utf-8")
    cat = json.loads(cat_path.read_text(encoding="utf-8")) if cat_path.exists() else {}
    seen = []
    for _attr, _a, _b, text in _segments(html):
        key = text.strip()
        if key and key not in seen:
            seen.append(key)
    for k in seen:
        cat.setdefault(k, "")
    # 樣板裡已經沒有的條目留著會誤導，直接丟掉
    cat = {k: v for k, v in cat.items() if k in seen}
    cat_path.parent.mkdir(parents=True, exist_ok=True)
    cat_path.write_text(json.dumps(cat, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    todo = [k for k, v in cat.items() if not v]
    print(f"{src.name}: {len(seen)} 條，未翻 {len(todo)}")
    return len(todo)


def build(src: Path, cat_path: Path, dst: Path, lang: str = "en") -> int:
    html = src.read_text(encoding="utf-8")
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    missing = []
    parts, last = [], 0
    for _attr, a, b, text in _segments(html):
        key = text.strip()
        rep = cat.get(key) or ""
        if not rep:
            missing.append(key)
            continue                      # 沒翻的原樣留著中文（比空白好）
        parts.append(html[last:a])
        parts.append(text.replace(key, rep, 1))
        last = b
    parts.append(html[last:])
    out = "".join(parts)
    out = re.sub(r'<html lang="[^"]*"', f'<html lang="{lang}"', out, count=1)
    # **順序很重要**：先把站內連結改成同語言版，再放語言切換那一組 ——
    # 反過來的話「繁體中文」那條會被一併改寫成 `index-en.html`
    # （原本靠 `id="langSwitch"` 在 `<a>` 上跳過，id 移到 `<span>` 之後就失效了）。
    out = _rewrite_internal_links(out, lang)
    # 語言切換：列出**其他所有語言**（三語之後「切換」這個形狀就不成立了）。
    out = re.sub(r'<select id="langSwitch"[^>]*>.*?</select>',
                 lang_group(src.stem, lang), out, count=1, flags=re.S)
    # **只有英文需要在行內標籤旁補空白**（英文詞之間要空格，日文不要 ——
    # 補了會變成「設定 を 保存」）。安裝指令與截圖則是逐語言各自一份。
    if lang == "en":
        out = _space_around_inline_tags(out)
    elif lang == "ja":
        out = _tighten_cjk_spaces(out)
    out = _translate_install_command(out, lang)
    out = _drop_hidden_tool_shots(out, lang)
    out = _localised_screenshots(out, dst.parent, lang)
    dst.write_text(out, encoding="utf-8")
    print(f"{dst.name}: 產生完成（{len(missing)} 條還沒翻，暫時保留中文）")
    import os as _os
    if missing and _os.environ.get("JTDT_SHOW_MISSING"):
        for _k in missing:
            print("   缺: " + repr(_k))
    return len(missing)



#: 站內頁面。**同語言的頁要連同語言的頁** —— 原本英文版的導覽連的是
#: `api.html`（中文頁），讀者一點就掉回中文（使用者 2026-09-14 指出）。
_SITE_PAGES = ("index", "api", "troubleshooting", "compliance")


def _rewrite_internal_links(html: str, lang: str) -> str:
    """把某個語言版裡的站內連結改成指向同一個語言版。

    **語言切換那一組不受影響** —— 它是在這一步**之後**才產生的
    （順序反過來的話「繁體中文」那條會被改寫成同語言版，就沒有出口了）。
    """
    def fix(m):
        seg = m.group(0)
        for name in _SITE_PAGES:
            seg = seg.replace(f'href="{name}.html"', f'href="{name}-{lang}.html"')
            seg = seg.replace(f'href="{name}.html#', f'href="{name}-{lang}.html#')
        return seg
    return re.sub(r"<a\b[^>]*>", fix, html)


#: 語言的自稱 —— 唯一來源是 `ui_locale.LOCALE_NAMES`（樣板不要自己寫死）。
def lang_group(page: str, current: str) -> str:
    """語言選單（導覽列上的下拉）。

    **兩語的時候一顆切換鈕就夠，三語之後就不夠了** —— 原本是
    「中文頁指向 -en、英文頁指回中文」，加第三種語言之後日文頁就沒有出口
    到英文頁；改成並排連結之後又變成導覽列上「English日本語」黏成一團
    （2026-09-14 使用者截圖回報）。**語言只會越加越多，所以用下拉。**

    列出**全部**語言（含目前這個，`selected`）—— 下拉的慣例是先讓人看到
    「現在是哪一個」，這跟「列出其他語言」的連結寫法不一樣。

    行為在 `docs/lang-switch.js`（**只有那一份**），不寫 inline 的
    `onchange`。
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.core.ui_locale import DEFAULT_LOCALE, LOCALE_NAMES, SUPPORTED
    out = ['<select id="langSwitch" class="nav-lang" aria-label="Language">']
    for code in SUPPORTED:
        href = f"{page}.html" if code == DEFAULT_LOCALE else f"{page}-{code}.html"
        sel = " selected" if code == current else ""
        out.append(f'<option value="{href}" lang="{code}"{sel}>'
                   f'{LOCALE_NAMES[code]}</option>')
    out.append("</select>")
    return "".join(out)


#: 舊名字留著給舊的呼叫端（同一支函式，不要抄第二份）。
_lang_group = lang_group


def _locales() -> "list[str]":
    """要產出哪些語言 —— **唯一來源是 `ui_locale.SUPPORTED`**。"""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.core.ui_locale import DEFAULT_LOCALE, SUPPORTED
    return [c for c in SUPPORTED if c != DEFAULT_LOCALE]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lang", default=None, help="只做這個語言（預設全部）")
    a = ap.parse_args()
    langs = [a.lang] if a.lang else _locales()
    rc = 0
    for lang in langs:
        for name in _SITE_PAGES:
            s = DOCS / f"{name}.html"
            if not s.exists():
                continue
            cat = I18N / f"{name}.{lang}.json"
            dst = DOCS / f"{name}-{lang}.html"
            if a.extract:
                extract(s, cat)
            elif a.check:
                rc |= 1 if extract(s, cat) else 0
            else:
                rc |= 1 if build(s, cat, dst, lang=lang) else 0
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
