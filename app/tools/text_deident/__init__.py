"""文字去識別化 — 對純文字（貼上或上傳）做敏感資料偵測 + 遮罩 / 替換假資料。

跟 doc-deident 共用 `patterns.CATALOG`（同一份 regex + 驗證 + masker），
但純文字情境不需要 PDF bbox / span 處理，邏輯簡單很多。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="text-deident",
    name="文字去識別化",
    description="貼文字或上傳 .txt / .md / .docx / .odt 等檔，偵測敏感資料並編修 / 遮罩 / 替換假資料。",
    # 文件版（doc-deident）用 `shield`；純文字版改 `text`，同一組裡才分得出來。
    icon="text",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)
