"""PDF 中繼資料清除：檢視並移除作者 / 標題 / XMP / 修訂歷史等。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-metadata",
    name="中繼資料清除",
    description="檢視並清除 PDF 的作者、標題、XMP、修訂歷史等 metadata。",
    icon="info",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)
