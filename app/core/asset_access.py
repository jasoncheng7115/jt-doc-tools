"""資產庫的圖（印章 / 簽名 / Logo / 浮水印）誰可以看、誰可以蓋進文件 —— 只在這裡定義。

GitHub issue #54（2026-10-05）：一般使用者預設**沒有**「用印與簽名」權限，卻能在
PDF 編輯器的「套印 / 簽名」挑到資產庫裡的公司印章，存檔時直接蓋進 PDF；圖檔網址
（`/assets/{id}/file`）也只要登入就拿得到 —— 拿到印章 PNG 再用「上傳新圖片」貼回去，
等於繞過權限。所以三個地方都照這張表擋：編輯器的資產清單、編輯器存檔、圖檔網址。

規則是「能在哪支工具用到這種資產，就看得到、用得到這種資產」：

* 印章：用印與簽名、騎縫章
* 簽名：用印與簽名
* 浮水印：浮水印
* Logo：不限（常用在信頭，不代表核准；2026-10-05 使用者同意不限）

**保護的是資產庫裡公司正式的章**，不是任何長得像章的圖 —— 使用者自己上傳的圖擋不了，
也不該擋。認證關閉（單機模式）時沒有帳號的概念，一律放行，跟原本一樣。
"""
from __future__ import annotations

from typing import Optional

#: 資產種類 → 能用這種資產的工具（任一個有權限就可以）。`None` ＝ 登入即可。
#: 不在表上的種類一律不給（新增資產種類時要決定它歸誰管，不可以預設全開）。
ASSET_TOOLS: dict[str, Optional[tuple[str, ...]]] = {
    "stamp": ("pdf-stamp", "pdf-seam-stamp"),
    "signature": ("pdf-stamp",),
    "watermark": ("pdf-watermark",),
    "logo": None,
}

#: 擋下時給使用者看的說明（`tr()` 鍵）—— 講得出要找誰、要什麼權限。
DENIED_MESSAGE = "資產庫的印章與簽名要有「用印與簽名」的使用權限才能使用，請洽管理員"


def can_use(request, asset_type: str) -> bool:
    """目前這個請求的使用者能不能看 / 用這種資產。"""
    from . import auth_settings
    if not auth_settings.is_enabled():
        return True
    if asset_type not in ASSET_TOOLS:
        return False
    tools = ASSET_TOOLS[asset_type]
    if tools is None:
        return True
    user = getattr(getattr(request, "state", None), "user", None)
    if not user or not user.get("user_id"):
        return False
    from . import permissions
    return any(permissions.user_can_use_tool(user["user_id"], t) for t in tools)


#: 受管制的章被擋下時的說明（`tr()` 鍵）。
CONTROLLED_MESSAGE = "這顆章僅限核准的用印申請案件使用，請提出用印申請"


def can_use_asset(request, asset) -> bool:
    """這一顆資產能不能在一般工具裡看 / 用。

    先看用印申請的管制（`seal_policy`）：「僅限核准案件使用」的印章與簽名，在用印工具、騎縫章、
    PDF 編輯器、資產圖檔網址、API 一律不給 —— 不分是誰、不分認證有沒有開（只有核准案件的用印流程
    另走自己的路徑）。沒有管制的再照種類規則（`can_use`）。**所有讀資產庫給使用者的地方都要走這裡**，
    `tests/test_seal_policy_entry_points.py` 會檢查。
    """
    if asset is None:
        return False
    from . import seal_policy
    if seal_policy.is_controlled(asset.id, asset.type):
        return False
    return can_use(request, asset.type)


def denied_message(asset) -> str:
    """擋下時該講哪一句（受管制的章要講「去申請」，不是「沒有權限」）。"""
    from . import seal_policy
    if asset is not None and seal_policy.is_controlled(asset.id, asset.type):
        return CONTROLLED_MESSAGE
    return DENIED_MESSAGE


def usable(request, assets) -> list:
    """清單只留這個人能用的（看不到就不會被選到）。"""
    return [a for a in assets if can_use_asset(request, a)]


def is_controlled_asset(asset) -> bool:
    """只看用印申請的管制，不看種類權限。給本來就靠工具權限把關的地方用（用印工具、騎縫章）：
    那些路徑換成 `can_use_asset` 會多一道種類檢查，沒綁使用者的 API Token 呼叫會被誤擋。"""
    if asset is None:
        return False
    from . import seal_policy
    return seal_policy.is_controlled(asset.id, asset.type)
