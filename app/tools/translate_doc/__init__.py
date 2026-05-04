"""逐句翻譯工具 — 接 admin 設定好的 LLM server，左原文右譯文逐句並排。

定位：附加功能（依賴 admin → LLM 設定啟用）。LLM 沒啟用時頁面顯示提示，
不擋其他工具運作。

支援來源：直接貼文字 / 上傳 PDF / DOCX / 純文字。輸出 = JSON 對照表，
UI 並排顯示，每句可重新請 LLM 重譯。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="translate-doc",
    name="逐句翻譯",
    description="接 LLM 逐句翻譯，左原文右譯文並排。可貼文字、上傳 PDF / DOCX / TXT。",
    icon="globe",
    category="內容處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)
