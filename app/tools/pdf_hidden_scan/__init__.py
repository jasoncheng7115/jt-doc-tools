"""PDF 隱藏內容掃描：找 JavaScript / 嵌入檔 / 白字 / 視窗外內容 / 外部連結
等，顯示風險清單並一鍵清除。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-hidden-scan",
    name="隱藏內容掃描",
    description="掃出 PDF 的 JavaScript、嵌入檔、隱藏文字、外部連結等風險，一鍵清除。",
    icon="search",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)
