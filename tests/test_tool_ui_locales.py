"""工具的介面語系白名單（`ToolMetadata.locales`）。

有幾支工具是為**中文 / 台灣的文件與慣例**做的 —— 把英文文件丟進去會「執行成功
但什麼都沒抓到」，**比看不到這支工具更糟**（使用者會以為處理過了）。所以介面
語言不是中文時，它們不列在側欄與搜尋裡。

**最高原則：加 i18n 不可以改壞現有功能。** 所以這支測試的第一條就是
「繁體中文底下，每一支工具都照樣看得到」—— 今天的行為必須跟加這個欄位之前
一模一樣。
"""
from __future__ import annotations

import pytest

from app.core.ui_locale import CHINESE, DEFAULT_LOCALE, TAIWAN_ONLY, tool_visible
from app.tool_registry import discover_tools

#: 靠台灣特有的資料或格式才成立 —— 簡體中文環境也不成立。
#:
#: **v1.15.32 移出兩支**：`doc-deident` / `text-deident` 加了英美的樣式
#: （SSN / NI / IBAN / 電話 / 郵遞區號 / 美式地址 / 英文月份生日），
#: 而且樣式**依文件語言分組** —— 英文模式下台灣專屬的式子會關掉。
#: 反灰的判準一直是「丟進去會不會無聲失敗」：以前英文文件丟進去會**抓錯**
#: （台灣市話式子把護照號、IBAN 片段當成電話），現在不會了。
_TAIWAN_ONLY = {
    "vat-lookup", "einvoice-scan", "transit-proof", "submission-check",
    "pdf-fill",
}
#: 靠華人文書慣例（印章）—— 曾經限成中文，2026-09-05 使用者指示**解除**：
#: 蓋章 / 簽名 / 跨頁防抽換不是華人專有，英文環境一樣會蓋公司章、貼簽名圖。
#: 這個集合現在是空的，但**留著不刪** —— 下一支「靠華人慣例」的工具還是會用到
#: `CHINESE` 這個常數，而且這裡寫著當初為什麼判斷錯，免得有人再限一次。
_CHINESE: set[str] = set()


@pytest.fixture(scope="module")
def tools():
    return discover_tools()


def test_traditional_chinese_still_shows_every_tool(tools):
    """**這條是最高原則的守門**：繁中底下一支都不可以少。"""
    hidden = [t.metadata.id for t in tools
              if not tool_visible(t.metadata.locales, DEFAULT_LOCALE)]
    assert hidden == [], f"繁體中文底下不應該有工具被藏起來：{hidden}"


def test_english_locks_exactly_the_chinese_only_tools(tools):
    """英文底下這七支**反灰點不下去**（不是藏起來 —— 使用者要求）。

    印章那兩支曾經也在裡面，2026-09-05 解除 —— **限制要有理由**：
    這七支是「英文文件丟進去會執行成功但什麼都沒抓到」，
    蓋章不是（英文 PDF 一樣蓋得上去）。
    """
    locked = {t.metadata.id for t in tools
              if not tool_visible(t.metadata.locales, "en")}
    assert locked == _TAIWAN_ONLY | _CHINESE, locked


def test_simplified_chinese_keeps_the_seal_tools(tools):
    """簡體中文底下只鎖台灣專用那七支。

    印章類原本列在這裡（靠華人慣例），使用者指示解除之後兩邊都不鎖 ——
    這條測試留著是為了守「簡中不可以誤鎖台灣以外的東西」。
    """
    locked = {t.metadata.id for t in tools
              if not tool_visible(t.metadata.locales, "zh-Hans")}
    assert locked == _TAIWAN_ONLY, locked
    assert _CHINESE & locked == set()


def test_locked_tools_are_still_listed(tools):
    """**反灰不等於消失** —— 側欄仍然列得出全部工具，只是有幾支點不下去。"""
    import app.main as app_main

    class _En:
        cookies = {"jtdt_locale": "en"}
        headers: dict = {}

    groups = app_main._nav_groups_for_locale(_En())
    listed = {t["id"] for g in groups for t in g["tools"]}
    assert listed == {t.metadata.id for t in tools}, "英文底下不可以少列任何一支"
    locked = {t["id"] for g in groups for t in g["tools"] if t.get("locked")}
    assert locked == _TAIWAN_ONLY | _CHINESE, locked

    class _Zh:
        cookies: dict = {}
        headers: dict = {}

    zh_locked = [t["id"] for g in app_main._nav_groups_for_locale(_Zh())
                 for t in g["tools"] if t.get("locked")]
    assert zh_locked == [], f"繁中底下不可以有工具被鎖住：{zh_locked}"


def test_the_marks_use_the_shared_constants(tools):
    """語系值只能用共用常數 —— 每支工具各寫一份 tuple 一定會漂。"""
    for t in tools:
        loc = t.metadata.locales
        if t.metadata.id in _TAIWAN_ONLY:
            assert loc == TAIWAN_ONLY, (t.metadata.id, loc)
        elif t.metadata.id in _CHINESE:
            assert loc == CHINESE, (t.metadata.id, loc)
        else:
            assert loc == (), f"{t.metadata.id} 不該有語系限制：{loc}"


def test_hiding_is_not_disabling(tools):
    """**隱藏 ≠ 停用。** 路由照常掛載、工具照常啟用。

    有人可能介面用英文、手上卻正好有一份中文表單；而 `/api/<tool-id>` 是給機器
    呼叫的，更不可以跟著介面語言變。
    """
    for t in tools:
        if t.metadata.id in _TAIWAN_ONLY | _CHINESE:
            assert t.metadata.enabled, t.metadata.id
            assert t.router is not None, t.metadata.id
            assert t.router.routes, f"{t.metadata.id} 應該仍然有路由"


def test_permissions_do_not_depend_on_locale():
    """權限不可以因為使用者換了介面語言就改變 —— 內建角色不認得語系這回事。"""
    import inspect

    from app.core import roles
    src = inspect.getsource(roles)
    assert "locale" not in src.lower(), "角色定義不應該跟介面語言有關"


def test_locale_resolution_survives_a_partial_request():
    """**不可以假設拿到的是完整的 Request。**

    側欄那條路徑在測試與部分內部呼叫裡會收到簡化的假物件（甚至 None）。
    語言只是顯示偏好 —— 取不到就回繁中，絕不可以因此讓整個側欄炸掉。
    （這條是實際踩到的：加了語言判斷之後，既有的
    `test_nav_tool_groups_auth_on_no_user_empty` 立刻紅了。）
    """
    from app.core.ui_locale import DEFAULT_LOCALE, resolve

    class _Bare:            # 沒有 cookies、也沒有 headers
        pass

    assert resolve(None) == DEFAULT_LOCALE
    assert resolve(_Bare()) == DEFAULT_LOCALE

    class _OnlyCookies:
        cookies = {"jtdt_locale": "en"}

    assert resolve(_OnlyCookies()) == "en"


def test_browser_language_alone_never_switches_the_ui():
    """**不可以因為瀏覽器語言就自動切成英文。**

    切成英文會連帶把九支中文專用工具從側欄拿掉 —— 一位把系統設成英文的台灣
    使用者會突然發現統編查詢不見了，而他完全沒有做過任何選擇。
    因為瀏覽器設定而改變功能可見範圍，是不能接受的。
    """
    from app.core.ui_locale import DEFAULT_LOCALE, resolve

    class _EnBrowser:
        cookies: dict = {}
        headers = {"accept-language": "en-US,en;q=0.9"}

    assert resolve(_EnBrowser()) == DEFAULT_LOCALE

    class _Chose:
        cookies = {"jtdt_locale": "en"}
        headers = {"accept-language": "zh-TW"}

    assert resolve(_Chose()) == "en", "明確選過的要贏"


def test_no_two_tools_in_one_group_share_an_icon(tools):
    """同一組裡不可以有兩支工具用同一個圖示。

    2026-09-05 使用者問「用印跟騎縫章 icon 是不是長的一樣？」—— 是。
    兩支都在「填單用印」、都用 `stamp`，側欄相鄰，**要讀完字才分得出來**。
    一起清出另外三對（`image-to-pdf` / `pdf-to-image`、
    `doc-deident` / `text-deident`、`doc-diff` / `text-diff`）。

    **跨組重複不管** —— 那是兩個不同的清單，不會擺在一起比。
    """
    import collections
    by = collections.defaultdict(list)
    for t in tools:
        by[(t.metadata.category, t.metadata.icon)].append(t.metadata.id)
    dup = {k: v for k, v in by.items() if len(v) > 1}
    assert not dup, f"同一組內圖示重複：{dup}"


def test_every_icon_name_exists(tools):
    """圖示名稱打錯**不會報錯，只是沒有圖** —— 那種缺陷只有用眼睛看得到。"""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "app" / "web" / "templates" / "components" / "icons.html").read_text(encoding="utf-8")
    known = set(re.findall(r"name == '([a-z0-9-]+)'", src))
    missing = sorted({t.metadata.icon for t in tools if t.metadata.icon not in known})
    assert not missing, f"這些圖示名稱在 icons.html 裡不存在：{missing}"
