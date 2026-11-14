"""介面文字的翻譯查表。**繁體中文是原文，英文是附加。**

設計上刻意用「**原文本身就是 key**」（gettext 的 msgid 做法）：

    {{ t('我的作業') }}

好處有三個，而且每一個都是這個專案吃過虧才學到的：

1. **查不到翻譯就回退成中文**，畫面永遠是完整的 —— 不會出現空白按鈕或
   `nav.jobs.title` 這種 key 露在畫面上。
2. **中文仍然留在樣板裡**，所以 `test_taiwan_terminology.py` 這類「掃描使用者
   看得到的文字」的檢查**照樣有效**。改成符號 key 的話那些測試會變成永遠綠燈
   的假測試 —— 這個專案已經有過好幾次「檢查安靜失效」的教訓。
3. 不必為 553 條字串發明 key。

代價是：改中文原文時要同步改語系檔的 key。自動檢查會列出「語系檔裡有、但樣板
裡已經找不到」的孤兒條目。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .ui_locale import DEFAULT_LOCALE, SUPPORTED

#: 語系檔放這裡：`app/i18n/<locale>.json`，內容是 {繁體中文原文: 譯文}。
CATALOG_DIR = Path(__file__).resolve().parent.parent / "i18n"


@lru_cache(maxsize=8)
def catalog(locale: str) -> dict[str, str]:
    """載入某個語言的對照表。缺檔 / 壞檔一律回空的 —— 全部回退中文。"""
    if locale == DEFAULT_LOCALE or locale not in SUPPORTED:
        return {}
    path = CATALOG_DIR / f"{locale}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v}


def translate(text: str, locale: str | None = None) -> str:
    """把一段繁體中文換成目標語言；查不到就原樣回傳（＝回退中文）。"""
    if not text or not locale or locale == DEFAULT_LOCALE:
        return text
    return catalog(locale).get(text, text)


#: 伺服器組好的訊息裡會變的部分：「…」引號裡的字（檔名、原文）、獨立的數字（不含 Big5、UTF-8 這種字裡的數字）
_MSG_VARS = __import__("re").compile(r"「([^」]*)」|(?<![A-Za-z\d\-.])\d+(?:[.,]\d+)*(?![A-Za-z\d])")


def translate_message(text: str, locale: str | None = None) -> str:
    """翻譯**伺服器已經組好**的訊息（API 錯誤）：先整句查；查不到就把「…」與數字換成 `{0}` `{1}`… 再查，
    查到就把原本的值填回去（引號裡的字照原樣，例如檔名）。都查不到就原樣回傳中文。

    前端的 `tr()` 只接得住「變數全是數字」的句子（見 `static/js/i18n.js`），檔名這種要在產生的地方翻 ——
    這支就是給伺服器那一端用的。
    """
    if not text or not locale or locale == DEFAULT_LOCALE:
        return text
    cat = catalog(locale)
    if text in cat:
        return cat[text]
    vals: list[str] = []

    def sub(m):
        i = len(vals)
        if m.group(1) is not None:
            vals.append(m.group(1))
            return "「{%d}」" % i
        vals.append(m.group(0))
        return "{%d}" % i
    key = _MSG_VARS.sub(sub, text)
    if not vals or key not in cat:
        return text
    out = cat[key]
    for i, v in enumerate(vals):
        out = out.replace("{%d}" % i, v)
    return out
